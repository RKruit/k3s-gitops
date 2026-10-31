#!/usr/bin/env python3
"""Zendure-follow: price-driven home-battery controller (SolarFlow 2400 AC+, sv01).

Strategy (Robert, 31-10-2026): NO dependence on solar; arbitrage on NextEnergy
all-in hourly prices; salderingsregeling still applies. Charge from the grid in
the cheapest hours, discharge to the house (zero export) in the most expensive
hours, idle in between so energy is preserved for the peaks. PV-surplus charging
stays in as a free bonus but the strategy never depends on it.

Price source: public NextEnergy widget (mijn.nextenergy.nl/Website_CW/MarketPrices),
scraped hourly with headless chromium (same proven recipe as nextenergy_report.py).
Thresholds = percentiles of today's hourly prices (charge <= CHARGE_PCT quantile,
discharge >= DISCHARGE_PCT quantile), gated on a minimum spread; flat days idle.

Control laws (verified 31-10-2026):
  price=charge  (SOC < CHARGE_TARGET)  -> 0 / IN_CAP (grid charge, full speed)
  price=discharge (SOC > device floor) -> zero-export following: outputLimit =
      measured feed + GAIN*(import - MARGIN), hold band 0..HOLD_HI, export past
      -EXPORT_NUDGE shaves 15 W; charge-on-surplus stays available
  price=idle / flat / no prices        -> 0/0 (+ charge-on-surplus bonus)
Failsafe: P1 unreadable > FAIL_GRACE s -> 0/0; SIGTERM -> 0/0; caps 800 W
(Robert keeps the socket limit until the dedicated group exists); SOC limits
stay device-managed. With saldering, exporting never beats avoided import, so
the controller never pushes power outward.
Status on http://192.168.1.10:8891/status.json
"""
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- tuning -----------------------------------------------------------------
P1_URL = "http://192.168.68.173/api/v1/data"
BAT_URL = "http://192.168.68.227"
SN = "HEC4NLNBP107471"
WIDGET_URL = "https://mijn.nextenergy.nl/Website_CW/MarketPrices"
MARGIN = 20        # W: keep this much always importing while discharging
HOLD_HI = 40       # W: import band 0..HOLD_HI -> hold (no writes) in discharge regime
CHARGE_AT = -40    # W: legacy threshold (kept for reference)
# Surplus-charge gate (anti-chatter): a fleeting surplus must persist before
# the battery flips to charge; once charging it holds until the surplus is
# really gone. Measured 31-10-2026: the raw threshold flickered and produced
# few-second charge bursts Robert saw.
SURPLUS_ENTER = -120     # W rolling-mean required to start charging
# (micro-surplus ~60 W: charging vs saldering is break-even incl. roundtrip
# loss + wear, so leave it to the grid; only meaningful surpluses charge)
SURPLUS_RELEASE = -30    # W rolling-mean where charging stops again
SURPLUS_DWELL = 30       # s the mean must stay below SURPLUS_ENTER before engage
# (micro-dips of ~10 s = ~0.001 kWh; relay cycling costs more than that)
SURPLUS_WINDOW = 8       # s rolling window for the mean
SURPLUS_TARGET = 95      # %: surplus charging may fill up to this SoC (Robert)
EXPORT_NUDGE = 10  # W: export beyond this inside the hold band -> shave 15 W off
OUT_CAP = 800      # W: max discharge (socket/group limit; Robert keeps 800 W)
IN_CAP = 800       # W: max charge
AC_MODE = {"charge": 1, "discharge": 2}   # zenSDK direction flag; verified vs reference
REST_AFTER = 120   # s of both-zero before handing smartMode back (device low-power)
GAIN = 0.5         # fraction of grid error applied per step (stable: 0<GAIN<1)
WRITE_MIN = 5      # W: skip writes while |target - last_written| <= this
WRITE_GAP = 1.5    # s: minimum time between writes
LOOP = 1.0         # s: P1 poll interval
FAIL_GRACE = 10.0  # s without a valid P1 read -> steer to 0/0
STATUS_PORT = 8891
CHARGE_PCT = 0.30  # charge while today's price is below this quantile
DISCHARGE_PCT = 0.70
MIN_SPREAD = 0.03  # EUR/kWh: below this spread the whole day idles (not worth cycling)
CHARGE_TARGET = 90 # %: stop grid-charging at this SOC (Robert: max 90%; minder degradatie)
# One-day tweak (Robert 31-10): today only discharge from 16:00 on — the 15h
# hour is 1 ct/kWh cheaper than 16h+, so holding the energy one more hour pays.
# Reverts automatically tomorrow (remove these two lines to make it permanent).
DISCHARGE_NOT_BEFORE_DATE = "2026-10-31"
DISCHARGE_NOT_BEFORE_HOUR = 16
PRICE_STALE = 5400 # s: prices older than this -> treat as unknown (idle, safe)
STATUS_DIR = "/var/lib/zendure-follow"

BAR_RE = re.compile(r'aria-label="(\d+), (\d+(?:\.\d+)?)(?:,\d+)?\. (?:Stroom|Electricity)\.')

state = {
    "mode": "observe" if "--observe" in sys.argv else "control",
    "started": time.time(),
    "last_p1": None,
    "p1_age_s": None,
    "soc": None,
    "regime": None,
    "target": {"outputLimit": None, "inputLimit": None},
    "last_written": {"outputLimit": None, "inputLimit": None},
    "writes": 0,
    "last_write_at": None,
    "last_error": "",
    "direction": None,
    "rested": False,
    "rest_since": None,
    "pending_write": None,
    "retries": 0,
    "p1_hist": [],
    "surplus_gate": False,
    "surplus_since": None,
    "price_regime": "no_prices",
    "price_now": None,
    "price_charge_thr": None,
    "price_discharge_thr": None,
    "prices_today": {},
    "prices_fetched_at": None,
}


def _get(url, timeout=4):
    req = urllib.request.Request(url, headers={"User-Agent": "sv01-zendure-follow"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def read_p1():
    d = _get(P1_URL)
    v = d.get("active_power_w")
    if not isinstance(v, (int, float)):
        raise ValueError(f"active_power_w missing: {d!r}")
    return float(v)


def read_bat():
    return _get(f"{BAT_URL}/properties/report")["properties"]


def write(out, in_):
    """Write limits following the reference-integration pattern.

    Direction change  -> ONE post: {smartMode:1, other_limit:0, acMode:X, limit:W}
    Same-direction    -> just the limit key (smartMode already holds 1)
    Idle both-zero    -> after REST_AFTER s of stillness hand smartMode back
                         (0) once, so the device can drop to low-power standby.
    """
    now = time.time()
    if out > 0:
        want = "discharge"
    elif in_ > 0:
        want = "charge"
    else:
        want = None

    idle_still = out == 0 and in_ == 0
    if not idle_still and want not in AC_MODE:
        return {}
    if idle_still and state["rested"]:
        state["last_written"] = {"outputLimit": 0, "inputLimit": 0}
        return {}  # already handed back; nothing to send
    if idle_still:
        if state["rest_since"] is None:
            state["rest_since"] = now
        if now - state["rest_since"] < REST_AFTER:
            props = {"outputLimit": 0, "inputLimit": 0}
        else:
            props = {"outputLimit": 0, "inputLimit": 0, "smartMode": 0}
            state["rested"] = True
            state["direction"] = None
    elif state["rested"] or want != state["direction"]:
        other = {"charge": "outputLimit", "discharge": "inputLimit"}[want]
        target = {"charge": "inputLimit", "discharge": "outputLimit"}[want]
        props = {"smartMode": 1, other: 0, "acMode": AC_MODE[want], target: (in_ if want == "charge" else out)}
        state["rested"] = False
        state["rest_since"] = None
        state["direction"] = want
    else:
        target = "inputLimit" if want == "charge" else "outputLimit"
        props = {target: (in_ if want == "charge" else out)}

    body = json.dumps({"sn": SN, "properties": props}).encode()
    req = urllib.request.Request(
        f"{BAT_URL}/properties/write", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=4) as r:
        resp = r.read(200).decode("utf-8", "replace")
    state["writes"] += 1
    state["last_write_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["last_written"] = {"outputLimit": out, "inputLimit": in_}
    state["pending_write"] = {"props": props, "at": now, "tries": 0}
    print(f"WRITE {props} -> {resp}", flush=True)
    return resp


def verify_or_retry(props_report):
    """Re-send the last write when the device did not take/engage it.

    The 2400 AC+ sometimes needs the direction write repeated before the
    power actually flows (measured 31-10-2026). Verified = properties match
    AND, for an engaged direction, the power flow shows up in the report.
    """
    p = state.get("pending_write")
    if not p:
        return
    age = time.time() - p["at"]
    if age < 4 or p["tries"] >= 3:
        if age > 12:
            state["pending_write"] = None  # give up quietly; next law change rewrites
        return
    props = p["props"]
    r = props_report
    landed = True
    if props.get("acMode") is not None and r.get("acMode") != props["acMode"]:
        landed = False
    for key in ("inputLimit", "outputLimit"):
        if key in props and r.get(key) != props[key]:
            landed = False
    flow_ok = True
    if props.get("acMode") == AC_MODE["charge"] and props.get("inputLimit"):
        # 2400 AC+ quirk: the direction write sometimes needs a repeat before
        # the charge actually draws power. Verified by seeing the flow.
        flow_ok = (r.get("gridInputPower") or 0) > 0.5 * props["inputLimit"]
    if landed and flow_ok:
        state["pending_write"] = None
        state["retries"] = 0
        return
    try:
        body = json.dumps({"sn": SN, "properties": props}).encode()
        req = urllib.request.Request(
            f"{BAT_URL}/properties/write", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=4) as r:
            resp = r.read(200).decode("utf-8", "replace")
        p["tries"] += 1
        state["retries"] = p["tries"]
        p["at"] = time.time()
        state["writes"] += 1
        print(f"RETRY({p['tries']}) {props} -> {resp}", flush=True)
    except Exception as e:
        state["last_error"] = f"retry write: {e}"


def quantile(sorted_vals, frac):
    q = min(max(int(round(frac * len(sorted_vals))), 1), len(sorted_vals)) - 1
    return sorted_vals[q]


def fetch_prices():
    """Scrape today's hourly all-in prices; returns {hour: euro_per_kwh} or {}."""
    for budget in (15000, 25000):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["chromium-browser", "--headless=new", "--disable-gpu", "--no-sandbox",
                   "--no-first-run", "--disable-crash-reporter",
                   "--user-data-dir=" + os.path.join(tmp, "profile"),
                   "--virtual-time-budget=" + str(budget),
                   "--window-size=1280,900", "--dump-dom", WIDGET_URL]
            try:
                dom = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
            except (subprocess.TimeoutExpired, Exception):
                continue
        bars = {(int(m.group(1)), float(m.group(2).replace(",", ".")))
                for m in BAR_RE.finditer(dom)}
        if len(bars) >= 20:
            return dict(bars)
    return {}


def save_prices(prices, fetched):
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        with open(os.path.join(STATUS_DIR, "prices.json"), "w") as f:
            json.dump({"date": datetime.date.today().isoformat(),
                       "hourly": prices, "fetched": fetched}, f)
    except OSError:
        pass


def price_thread():
    while not stop.is_set():
        try:
            prices = fetch_prices()
            if prices:
                state["prices_today"] = prices
                state["prices_fetched_at"] = time.time()
                save_prices(prices, state["prices_fetched_at"])
        except Exception as e:
            state["last_error"] = f"prices: {e}"
        # fetch again 2 min past the next full hour (midnight rollover lands fast)
        now = time.time()
        nxt = (int(now // 3600) + 1) * 3600 + 121
        stop.wait(min(max(nxt - now, 60), 3900))


def price_regime(now_dt):
    """Set state from today's prices; return 'charge' / 'discharge' / 'idle'."""
    fetched = state["prices_fetched_at"]
    prices = state["prices_today"]
    if not fetched or time.time() - fetched > PRICE_STALE or now_dt.hour not in prices:
        state["price_regime"] = "no_prices"
        state["price_now"] = None
        return "idle"
    state["price_now"] = prices[now_dt.hour]
    vals = sorted(prices.values())
    c_thr = quantile(vals, CHARGE_PCT)
    d_thr = quantile(vals, DISCHARGE_PCT)
    state["price_charge_thr"] = c_thr
    state["price_discharge_thr"] = d_thr
    if d_thr - c_thr < MIN_SPREAD:
        state["price_regime"] = "flat_day"
        return "idle"
    if state["price_now"] <= c_thr:
        state["price_regime"] = "charge"
        return "charge"
    if state["price_now"] >= d_thr:
        if str(now_dt.date()) == DISCHARGE_NOT_BEFORE_DATE and now_dt.hour < DISCHARGE_NOT_BEFORE_HOUR:
            state["price_regime"] = f"idle (wacht tot {DISCHARGE_NOT_BEFORE_HOUR}:00)"
            return "idle"
        state["price_regime"] = "dispatch_window"
        return "discharge"
    state["price_regime"] = "idle"
    return "idle"


def surplus_gate_eval(p1_avg, soc, ceil_pct):
    """Stateful anti-chatter gate for surplus charging; returns True/False."""
    def _dbg(msg):
        if os.environ.get("ZF_DEBUG"):
            print(f"GATE {msg} (avg={p1_avg}, soc={soc})", flush=True)
    gate = state["surplus_gate"]
    if not gate:
        if p1_avg is not None and p1_avg < SURPLUS_ENTER:
            if state["surplus_since"] is None:
                state["surplus_since"] = time.time()
                _dbg(f"arm {SURPLUS_DWELL}s")
            if time.time() - state["surplus_since"] >= SURPLUS_DWELL and soc < SURPLUS_TARGET:
                state["surplus_gate"] = True
                _dbg(f"engage after {round(time.time() - state['surplus_since'], 1)}s")
                return True
        else:
            if state["surplus_since"] is not None:
                _dbg("disarm (surplus gone during dwell)")
            state["surplus_since"] = None
        return False
    # engaged: hold until the surplus is really gone or the SoC target is reached
    if soc >= SURPLUS_TARGET or p1_avg is None or p1_avg > SURPLUS_RELEASE:
        state["surplus_gate"] = False
        state["surplus_since"] = None
        _dbg("release")
        return False
    return True


def damped_charge_target(p1, props, last_written):
    """Incremental surplus-charging law (verified): export converges to MARGIN."""
    charged = (last_written["inputLimit"] or 0)      # device follows its limit
    tgt_c = charged + GAIN * (-p1 - MARGIN)
    tgt_c = min(max(tgt_c, 0.0), IN_CAP)
    return int(5 * round(tgt_c / 5))


def compute_target(p1, props, last_written, p_regime, p1_avg=None):
    """Return (out_target, in_target) or None to hold the current state.

    p1: net grid power in W (positive = import, negative = export).
    """
    soc = props.get("electricLevel")
    ceil_pct = (props.get("socSet") or 1000) / 10.0   # report stores tenths of a percent
    floor_pct = (props.get("minSoc") or 0) / 10.0
    if soc is None:
        raise ValueError("electricLevel missing in report")

    lw_out = last_written["outputLimit"] or 0
    lw_in = last_written["inputLimit"] or 0
    surplus_charge = surplus_gate_eval(p1_avg, soc, ceil_pct)

    if p_regime == "charge" and soc < CHARGE_TARGET:
        if lw_in == IN_CAP:
            return None
        state["regime"] = "price-charge"
        return 0, IN_CAP

    if p_regime == "discharge" and soc > floor_pct:
        if surplus_charge:
            tgt_c = damped_charge_target(p1, props, last_written)
            if lw_in == tgt_c and lw_out == 0:
                return None
            state["regime"] = "discharge: surplus-charge"
            return 0, tgt_c
        if p1 > HOLD_HI:
            feed = props.get("outputHomePower") or 0  # measured W into the home now
            tgt = feed + GAIN * (p1 - MARGIN)
            tgt = min(max(tgt, 0.0), OUT_CAP)
            tgt = int(5 * round(tgt / 5))
            if last_written["outputLimit"] is not None \
                    and abs(tgt - lw_out) <= WRITE_MIN and lw_in == 0:
                return None
            state["regime"] = "discharge"
            return tgt, 0
        if p1 < -EXPORT_NUDGE and lw_out > 0:
            tgt = int(5 * round((lw_out - 15) / 5))
            state["regime"] = "nudge"
            return max(tgt, 0), 0
        state["regime"] = "discharge-hold"
        if lw_in != 0:  # surplus window ended; stop charging
            return lw_out, 0
        return None

    # idle (or charge window with SOC target reached / no prices / flat day)
    if surplus_charge:
        tgt_c = damped_charge_target(p1, props, last_written)
        if lw_in == tgt_c and lw_out == 0:
            return None
        state["regime"] = "surplus-charge"
        return 0, tgt_c
    state["regime"] = "idle" if p_regime == "idle" else f"idle ({p_regime})"
    if lw_out == 0 and lw_in == 0:
        return None
    return 0, 0


def main_loop(observe):
    last_ok = 0.0
    last_p1 = None
    next_write_ok = 0.0
    while not stop.is_set():
        try:
            p1 = read_p1()
            last_p1 = p1
            last_ok = time.time()
            state["last_p1"] = p1
            state["p1_age_s"] = 0
            state["p1_hist"] = [(t, v) for (t, v) in state["p1_hist"] if time.time() - t <= SURPLUS_WINDOW + 1]
            state["p1_hist"].append((last_ok, p1))
        except Exception as e:
            state["p1_age_s"] = round(time.time() - last_ok, 1) if last_ok else None
            state["last_error"] = f"P1: {e}"

        try:
            props = read_bat()
            state["soc"] = props.get("electricLevel")
            verify_or_retry(props)
            if state["last_written"]["outputLimit"] is None:
                state["last_written"] = {"outputLimit": int(props.get("outputLimit") or 0),
                                         "inputLimit": int(props.get("inputLimit") or 0)}
                ac = props.get("acMode")
                state["direction"] = "charge" if ac == 1 else ("discharge" if ac == 2 else None)
            if state["direction"] and props.get("smartMode") == 0:
                state["direction"] = None  # device rebooted/flipped the RAM flag; re-ensure next write
            fresh = last_p1 is not None and (time.time() - last_ok) <= FAIL_GRACE
            cutoff = time.time() - SURPLUS_WINDOW
            fresh_hist = [(t, v) for (t, v) in state["p1_hist"] if t >= cutoff]
            p1_avg = round(sum(v for _, v in fresh_hist) / len(fresh_hist), 1) if fresh_hist else None
            state["p1_avg"] = p1_avg
            p_regime = price_regime(datetime.datetime.now().astimezone())
            tgt = compute_target(last_p1, props, state["last_written"], p_regime, p1_avg) if fresh else (0, 0)
            if os.environ.get("ZF_DEBUG"):
                sig = (state["regime"], state["surplus_gate"], None if tgt is None else tuple(tgt))
                if sig != state.get("_last_dbg"):
                    state["_last_dbg"] = sig
                    print(f"DBG p1={last_p1} avg8s={p1_avg} p_regime={p_regime} regime={state['regime']} "
                          f"gate={state['surplus_gate']} tgt={sig[2]}", flush=True)
            state["target"] = {"outputLimit": tgt[0], "inputLimit": tgt[1]} if tgt else state["target"]

            now = time.time()
            lw0 = state["last_written"]
            idle_pair = tgt == (0, 0) or (tgt is None and lw0["outputLimit"] == 0 and lw0["inputLimit"] == 0)
            if idle_pair and state["rest_since"] is None:
                state["rest_since"] = now
            need_rest = idle_pair and state["rest_since"] is not None \
                and not state["rested"] and now - state["rest_since"] >= REST_AFTER
            changed = tgt and not (tgt[0] == lw0["outputLimit"] and tgt[1] == lw0["inputLimit"])
            if ((tgt and changed) or need_rest) and now >= next_write_ok:
                if observe:
                    print(f"OBSERVE p1={last_p1:.0f}W soc={props.get('electricLevel')} "
                          f"regime={state['regime']} -> out={tgt[0] if tgt else 0}W in={tgt[1] if tgt else 0}W "
                          f"{'(rest)' if need_rest else ''}", flush=True)
                else:
                    write(tgt[0] if tgt else 0, tgt[1] if tgt else 0)
                next_write_ok = now + WRITE_GAP
        except Exception as e:
            state["last_error"] = f"BAT: {e}"
        stop.wait(LOOP)


stop = threading.Event()


def _shutdown(signum, _frame):
    stop.set()
    if state["mode"] == "control":
        try:
            write(0, 0)
            print("shutdown: battery stopped (0/0)", flush=True)
        except Exception as e:
            state["last_error"] = f"shutdown write failed: {e}"
    sys.exit(0)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/status.json":
            self.send_error(404)
            return
        snap = dict(state)
        snap["uptime_s"] = round(time.time() - state["started"])
        body = json.dumps(snap).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass


if __name__ == "__main__":
    observe = "--observe" in sys.argv
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    threading.Thread(target=price_thread, daemon=True).start()
    threading.Thread(target=main_loop, args=(observe,), daemon=True).start()
    print(f"zendure-follow up ({'observe' if observe else 'control'}), status on :{STATUS_PORT}/status.json", flush=True)
    ThreadingHTTPServer(("0.0.0.0", STATUS_PORT), Handler).serve_forever()
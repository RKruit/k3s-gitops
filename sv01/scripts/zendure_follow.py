#!/usr/bin/env python3
"""Zendure-follow: price-driven home-battery controller (SolarFlow 2400 AC+, sv01).

Strategy (Robert, 31-10-2026): NO dependence on solar; arbitrage on NextEnergy
all-in hourly prices; salderingsregeling still applies. Charge from the grid in
the cheapest hours, discharge to the house (zero export) in the most expensive
hours, idle in between so energy is preserved for the peaks. PV-surplus charging is ON again since 09-11-2026 (Robert: "laad wel op bij zon
overschot" — off 06→09-11 per "stop met opladen van overschot"); SURPLUS_TARGET
100% stands ("zon mag tot 100%"), price-based grid charging unchanged.

Price source: NextEnergy app-API (personal contract; today, yesterday and —
once published, ±16:00 — tomorrow in one login; recipe from
zendure_schedule_report.py). Tomorrow's all-in prices are cached to
prices-tomorrow.json for the Grafana "morgen" bars panel. DP labels lead the clock by one
hour -- DP[label] holds the price of CLOCK hour label+1 -- confirmed against
Robert's billing page 03-11-2026 (mon 06-07 = DP[5] 0,42, 14-15 = DP[13] 0,37).
So clock hour 0 = yesterday's DP[23] and clock hour h = DP[h-1]. Fallback: the
public widget (same DP shift applied; its clock hour 0 stays unknown -> idle).
Thresholds = percentiles of today's 24 CLOCK-hour prices (charge <= CHARGE_PCT
quantile, discharge >= DISCHARGE_PCT quantile), gated on a minimum spread;
flat days idle.

Control laws (verified 31-10-2026):
  price=charge  (SOC < CHARGE_TARGET)  -> 0 / IN_CAP (grid charge, full speed)
  price=discharge (SOC > device floor) -> zero-export following: outputLimit =
      measured feed + GAIN*(import - MARGIN), hold band 0..HOLD_HI, export past
      -EXPORT_NUDGE shaves 15 W; charge-on-surplus ON again sinds 09-11-2026
  price=idle / flat / no prices        -> 0/0 (+ surplus gate may charge)
Failsafe: P1 unreadable > FAIL_GRACE s -> 0/0; SIGTERM -> 0/0; caps 800 W
(Robert keeps the socket limit until the dedicated group exists); SOC limits
stay device-managed. With saldering, exporting never beats avoided import, so
the controller never pushes power outward.
Status on http://192.168.1.10:8891/status.json
"""
import datetime
import http.cookiejar
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- tuning -----------------------------------------------------------------
P1_URL = "http://192.168.68.221/api/v1/data"  # .221 sinds 08-11-2026 (P1 verhuisd na Deco-uitval; .173 was het oude adres)
BAT_URL = "http://192.168.68.229"  # .229 sinds 08-11-2026 (DHCP-na-Deco-uitval; .227 was de boot-IP; reserveer addresses in de Deco!)
SN = "HEC4NLNBP107471"
WIDGET_URL = "https://mijn.nextenergy.nl/Website_CW/MarketPrices"
APP_AUTH_FILE = "/srv/sv01-secrets/nextenergy.env"
APP_CFG_FILE = os.path.expanduser("~/.hermes/zendure/nextenergy_app.json")
MARGIN = 20        # W: keep this much always importing while discharging
HOLD_HI = 40       # W: import band 0..HOLD_HI -> hold (no writes) in discharge regime
CHARGE_AT = -40    # W: legacy threshold (kept for reference)
# Surplus-charge gate (anti-chatter): steer on the ~3-min ROLLING MEAN, so a
# duty-cycling load (flapping surplus ~+40/-1200 W every ~30-60 s, measured
# 13:50) never touches the relays: one engage, one steady limit, smooth P1.
SURPLUS_ENTER = -150     # W rolling-mean required to start charging
SURPLUS_RELEASE = -10    # W rolling-mean where charging stops again
SURPLUS_WINDOW = 180     # s rolling window (mean over it drives enter/release/target)
SURPLUS_TARGET = 100     # %: surplus charging may fill up to this SoC (Robert 02-11-2026: zon mag tot 100% laden)
SURPLUS_CHARGE_ENABLED = True  # her-aan 09-11-2026 (Robert: 'Laad wel op bij zon overschot'); uit 06→09-11-2026 (bevel 06-11)
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
DRIFT_REASSERT_GAP = 20    # s minimum between drift-reassert writes
DRIFT_STICK_CLEAR = 300    # s the reasserted limit must hold to clear the burst counter
DRIFT_BURST_LIMIT = 3      # consecutive quick re-drifts -> pause + alarm (foreign writer)
FOREIGN_ERR = "foreign writer: limit keeps being reverted"  # -> Kuma #20 DOWN
STATUS_PORT = 8891
CHARGE_PCT = 0.30  # charge while today's price is below this quantile
DISCHARGE_PCT = 0.70
MIN_SPREAD = 0.03  # EUR/kWh: below this spread the whole day idles (not worth cycling)
CHARGE_TARGET = 90  # %: stop grid-charging at this SOC (Robert 11-11-2026 'Laad max 90%' — terugroepen van de tijdelijke 100%; het terugkerende patroon: bij grote spreiding tijdelijk 100, daarna terug naar 90)
# One-day override pattern (DISCHARGE_FULL_* / DISCHARGE_NOT_BEFORE_*): zie
# skill zendure-solarflow. De 09-11-2026 full-cap override ('ontlaad naar 0%')
# werd diezelfde avond opgeheven op Roberts instructie 'accu niet maximaal
# ontladen, alleen wat nodig is' — constants + compute_target-gate verwijderd;
# de normale zero-export-volger is weer de baas.
# One-day tweak (Robert 31-10): today only discharge from 16:00 on — the 15h
# hour is 1 ct/kWh cheaper than 16h+, so holding the energy one more hour pays.
# Reverts automatically tomorrow (remove these two lines to make it permanent).
DISCHARGE_NOT_BEFORE_DATE = "2026-10-31"
DISCHARGE_NOT_BEFORE_HOUR = 16
# One-day threshold overrides (Robert 11-11-2026): (datum, van_uur, drempel),
# laatste match wint; vervallen automatisch met de datum. Fases vandaag:
# tot 19:00 drempel 0,59 (uur 20 valt dan uit), vanaf 19:00 0,48 — Robert:
# 'accu leegmaken' → venstert door in de goedkopere avonduren (20: 0,56,
# 21: 0,49) tot de device-vloer; uur 22 (0,44) valt buiten, 23 laadt.
DISCHARGE_THR_STEPS = (
    ("2026-11-11", 0, 0.59),
    ("2026-11-11", 19, 0.48),
)
# One-day CHARGE-threshold override (Robert 11-11-2026 ~22:00 'de laadrempel
# staat wat hoog voor de rest van de dag; kijk ook naar morgen'): vanavond
# niet bijladen om 23:00 (0,39) — morgen 12-11 is goedkoper (nacht 0,35-0,39,
# midag 13-15u 0,33-0,34), de normale P30-wet pakt die vensters zelf vanaf
# 00:00. Spiegel van DISCHARGE_THR_STEPS; vervalt automatisch met de datum.
CHARGE_THR_STEPS = (
    ("2026-11-11", 22, 0.38),
)
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
    "price_source": "-",
    "rested": False,
    "rest_since": None,
    "pending_write": None,
    "retries": 0,
    "p1_hist": [],
    "surplus_gate": False,
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
        if str(state["last_error"]).startswith("retry write:"):
            state["last_error"] = ""  # write verified again
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


# --- drift self-heal --------------------------------------------------------
# Silent device-/cloud-side rewrites of the engaged limit (seen 12-11-2026:
# charge @800 written 12:00:01, flow verified bevestigd ~12:00:15, later
# inputLimit=0 in the report while the write counter never moved — same
# family as the 2x minSoc auto-revert). The law only rewrites on a target
# CHANGE, so a drifted limit would stay broken all day; drift_check re-sends
# our own intended limit when the report deviates. A reverter that keeps it
# up ~every 15-20 s (plan mode) trips the burst counter: pause + alarm.
def drift_check(observe, props):
    """Re-assert the engaged limit when the device report silently disagrees."""
    if observe:
        return
    st = state.setdefault("drift", {"seen": 0, "last_at": 0.0, "bursts": 0, "reasserts": 0})
    lw = state["last_written"]
    if lw["outputLimit"] is None:
        return
    in_w = lw["inputLimit"] or 0
    out_w = lw["outputLimit"] or 0
    if in_w == 0 and out_w == 0:
        return  # resting: 0/0 is intended, hands off in rest
    want = "charge" if in_w > 0 else "discharge"
    key = "inputLimit" if want == "charge" else "outputLimit"
    want_val = int(lw[key] or 0)
    report_val = props.get(key)
    if report_val is None:
        return
    now = time.time()
    if int(report_val or 0) == want_val:
        st["seen"] = 0
        if st["bursts"] and now - st["last_at"] >= DRIFT_STICK_CLEAR:
            st["bursts"] = 0  # reassert held long enough
        if state.get("last_error") == FOREIGN_ERR:
            state["last_error"] = ""  # limit holds again, alarm off
        return
    st["seen"] += 1
    if st["seen"] < 2:
        return  # require 2 consecutive samples (report refresh noise)
    if now - st["last_at"] < DRIFT_REASSERT_GAP:
        return
    if st["bursts"] >= DRIFT_BURST_LIMIT:
        if state.get("last_error") != FOREIGN_ERR:
            print(f"FOREIGN WRITER: {key} keeps reverting to {report_val} "
                  f"(intended {want_val}); reassert paused", flush=True)
            state["last_error"] = FOREIGN_ERR  # Kuma #20 goes DOWN on this
        return
    if props.get("acMode") != AC_MODE[want] or props.get("smartMode") == 0:
        state["direction"] = None  # force the full direction post on reassert
    try:
        st["reasserts"] += 1
        st["last_at"] = now
        st["bursts"] += 1
        st["seen"] = 0
        print(f"DRIFT {key}={report_val} (intended {want_val}, "
              f"#{st['reasserts']}, burst {st['bursts']}) -> reassert", flush=True)
        write(lw["outputLimit"], lw["inputLimit"])
    except Exception as e:
        state["last_error"] = f"reassert write: {e}"


def quantile(sorted_vals, frac):
    q = min(max(int(round(frac * len(sorted_vals))), 1), len(sorted_vals)) - 1
    return sorted_vals[q]


def fetch_widget():
    """Scrape the widget's 24 DP-labelled bars; returns {label: euro/kWh} or {}.
    NOTE: labels lead the clock by one hour (see module docstring)."""
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


def _app_session():
    """OutSystems login-sessie (opener, crf, modver) — één login per cyclus,
    daarna meerdere dag-queries in dezelfde sessie (vandaag, gisteren voor de
    midnight-wrap en morgen voor het dashboard). Recipe: nextenergy_app.json +
    secrets file. Returns None on any failure."""
    if not (os.path.exists(APP_AUTH_FILE) and os.path.exists(APP_CFG_FILE)):
        return None
    try:
        cfg = json.load(open(APP_CFG_FILE))
        creds = {}
        with open(APP_AUTH_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    creds[k] = v
        base = cfg["base_url"]
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))
        H = {"Content-Type": "application/json; charset=UTF-8",
             "Accept": "application/json", "OutSystems-locale": "en-US",
             "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/131",
             "Origin": base, "Referer": base + "/Mobile_EnergyNext/Login",
             "X-Requested-With": "XMLHttpRequest"}
        opener.open(base + "/Mobile_EnergyNext/Login", timeout=20).read()
        modver = cfg.get("module_version_fallback", "")
        try:
            ts = int(time.time() * 1000)
            mv = json.loads(opener.open(base + "/Mobile_EnergyNext/"
                                        "moduleservices/moduleversioninfo?%d" % ts,
                                        timeout=20).read())
            modver = mv.get("versionToken", modver)
        except Exception:
            pass
        crf = ""
        for c in jar:
            if c.name == "nr2Users_Customers":
                m = re.search(r"crf=([^;]+)", urllib.parse.unquote(c.value))
                crf = m.group(1) if m else ""
        login = {"versionInfo": {"moduleVersion": modver,
                                 "apiVersion": cfg["login_api_version"]},
                 "viewName": "CommonScreensFlow.Login",
                 "inputParameters": {"Username": creds["NEXTENERGY_EMAIL"],
                                     "Password": creds["NEXTENERGY_PASSWORD"],
                                     "DeviceId": ""}}
        h = dict(H)
        h["X-CSRFToken"] = crf
        resp = json.loads(opener.open(urllib.request.Request(
            base + cfg["login_path"], data=json.dumps(login).encode(),
            headers=h, method="POST"), timeout=25).read())
        if resp.get("exception"):
            return None
        # de CSRF-token verandert bij login: opnieuw ophalen uit de cookie
        crf = ""
        for c in jar:
            if c.name == "nr2Users_Customers":
                m = re.search(r"crf=([^;]+)", urllib.parse.unquote(c.value))
                crf = m.group(1) if m else ""
        return opener, crf, modver
    except Exception:
        return None


def _dp_query(opener, crf, modver, day):
    """DP-set {label: euro/kWh} + persoonlijk gastarief (€/m³) voor
    kalenderdag `day` (labels lopen één uur vóór op de klok — zie
    dp_to_clock). Returns (prices, gas) of None on any failure; gas is None
    when the app didn't publish a gas rate for that date (gas is dag-gebaseerd)."""
    try:
        cfg = json.load(open(APP_CFG_FILE))
        base = cfg["base_url"]
        body = json.loads(json.dumps(cfg["price_body"]))
        body["versionInfo"]["moduleVersion"] = modver
        body["clientVariables"]["PriceDate"] = day.isoformat()
        body["clientVariables"]["PriceCostLevel"] = cfg["cost_level"]
        h = {"Content-Type": "application/json; charset=UTF-8",
             "Accept": "application/json", "OutSystems-locale": "en-US",
             "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/131",
             "Origin": base, "Referer": base + "/Mobile_EnergyNext/MarketPrices",
             "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": crf}
        resp = json.loads(opener.open(urllib.request.Request(
            base + cfg["price_path"], data=json.dumps(body).encode(),
            headers=h, method="POST"), timeout=25).read())
        if resp.get("exception"):
            return None
        lst = (resp.get("data", {}).get("DataPoints") or {}).get("List") or []
        prices = {int(it["Label"]): float(it["Value"]) for it in lst
                  if it.get("Value") is not None}
        if len(prices) < 20 or len(prices) > 24:
            return None
        try:
            gas = float(resp["data"]["CurrentGasPrice"])
        except (KeyError, TypeError, ValueError):
            gas = None  # geen gastarief gepubliceerd voor deze PriceDate
        return prices, gas
    except Exception:
        return None


def save_tomorrow(prices, gas, fetched):
    """Morgenprijs-cache (KLOK-uren 0..23) voor het Grafana-morgenpanel. De
    poller gebruikt 'm alleen wanneer date de échte kalenderdatum van morgen
    is — een cache van de vorige avond kan zo nooit op de verkeerde dag landen
    (de app publiceert morgenprijzen pas ±16:00)."""
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        with open(os.path.join(STATUS_DIR, "prices-tomorrow.json"), "w") as f:
            json.dump({"date": (datetime.date.today()
                                + datetime.timedelta(days=1)).isoformat(),
                       "hourly": prices, "gas_eur": gas, "fetched": fetched,
                       "source": "app"}, f)
    except OSError:
        pass


def dp_to_clock(dp_today, dp_prev):
    """DP labels -> clock hours: clock hour 0 = DP[23] of yesterday,
    clock hour h (1..23) = DP[h-1] of today (labels lead the clock by 1)."""
    if not dp_today:
        return None
    prices = {}
    p0 = (dp_prev or {}).get(23)
    if p0 is not None:
        prices[0] = p0
    for h in range(1, 24):
        v = dp_today.get(h - 1)
        if v is not None:
            prices[h] = v
    return prices or None


def widget_to_clock(bars):
    """Widget bars are DP-labelled too -> clock 1..23; their clock hour 0 is
    unknown (tomorrow's first hour sits in DP[23]) -> treated as unknown."""
    return {h + 1: v for h, v in bars.items() if 0 <= h <= 22} or None


def fetch_prices():
    """Actuele prijzen in KLOK-uren {0..23: euro/kWh}, of {} bij falen.
    Returns (prices, gas): gas = persoonlijk all-in gastarief (€/m³) voor
    vandaag, of None. Primary: app-API (personal contract) — één login per
    cyclus, daarna DP-queries voor gisteren (midnight-wrap), vandaag en
    morgen. De morgenprijzen verschijnen pas ±16:00; geen DP-response = geen
    cache-update (de vorige cache kan sowieso niet op de verkeerde dag
    landen, zie save_tomorrow). Fallback: public widget with the same DP
    shift applied."""
    today = datetime.date.today()
    sess = _app_session()
    if sess:
        dp_t = _dp_query(*sess, today)
        if dp_t:
            dp_y = _dp_query(*sess, today - datetime.timedelta(days=1))
            dp_tom = _dp_query(*sess, today + datetime.timedelta(days=1))
            prices = dp_to_clock(dp_t[0], dp_y[0] if dp_y else None)
            gas = dp_t[1]
            if prices:
                state["price_source"] = "app"
                tom, gas_tom = None, None
                if dp_tom:
                    tom = dp_to_clock(dp_tom[0], dp_t[0])
                    gas_tom = dp_tom[1]
                if tom and len(tom) >= 20:
                    save_tomorrow(tom, gas_tom, time.time())
                return prices, gas
    bars = fetch_widget()
    if bars:
        prices = widget_to_clock(bars)
        if prices:
            state["price_source"] = "widget"
            return prices, None
    return {}, None


def save_prices(prices, gas, fetched):
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        with open(os.path.join(STATUS_DIR, "prices.json"), "w") as f:
            json.dump({"date": datetime.date.today().isoformat(),
                       "hourly": prices, "gas_eur": gas, "fetched": fetched}, f)
    except OSError:
        pass


def price_thread():
    while not stop.is_set():
        try:
            prices, gas = fetch_prices()
            if prices:
                state["prices_today"] = prices
                state["prices_fetched_at"] = time.time()
                save_prices(prices, gas, state["prices_fetched_at"])
                if str(state["last_error"]).startswith("prices:"):
                    state["last_error"] = ""
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
    for _td, _th, _cthr in CHARGE_THR_STEPS:
        if str(now_dt.date()) == _td and now_dt.hour >= _th:
            c_thr = _cthr  # one-day charge-threshold override (Robert)
    d_thr = quantile(vals, DISCHARGE_PCT)
    for _td, _th, _tthr in DISCHARGE_THR_STEPS:
        if str(now_dt.date()) == _td and now_dt.hour >= _th:
            d_thr = _tthr  # one-day override (Robert)
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


def _dbg(msg):
    if os.environ.get("ZF_DEBUG"):
        print(f"GATE {msg}", flush=True)


def surplus_gate_eval(p1_avg, soc):
    """Stateful gate for surplus charging; True while the battery may charge.

    Steers entirely on the long rolling mean: a duty-cycling flapping surplus
    averages out and never flickers the gate itself.
    """
    if not SURPLUS_CHARGE_ENABLED:
        state["surplus_gate"] = False
        return False
    gate = state["surplus_gate"]
    if not gate:
        # judge only on a sufficiently filled window (restart = fresh history):
        if (state.get("p1_cov") or 0) < 0.75 * SURPLUS_WINDOW:
            return False
        if p1_avg is not None and p1_avg < SURPLUS_ENTER and soc < SURPLUS_TARGET:
            state["surplus_gate"] = True
            _dbg(f"engage (mean{SURPLUS_WINDOW}s={p1_avg})")
            return True
        return False
    if soc >= SURPLUS_TARGET or p1_avg is None or p1_avg > SURPLUS_RELEASE:
        state["surplus_gate"] = False
        _dbg(f"release (avg={p1_avg}, soc={soc})")
        return False
    return True


def surplus_target(props, p1_avg):
    """Mean-steering charge target: absorb the average surplus, keep MARGIN.

    Uses the measured gridInputPower as the current charge level (so manual or
    device-side changes self-correct) and the long-window mean of P1.
    """
    measured = props.get("gridInputPower") or 0
    tgt_c = measured + (-p1_avg - MARGIN) if p1_avg is not None else 0.0
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
    surplus_charge = surplus_gate_eval(p1_avg, soc)

    if p_regime == "charge" and soc < CHARGE_TARGET:
        if lw_in == IN_CAP:
            return None
        state["regime"] = "price-charge"
        return 0, IN_CAP

    if p_regime == "discharge" and soc > floor_pct:
        if surplus_charge:
            tgt_c = surplus_target(props, p1_avg)
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
        tgt_c = surplus_target(props, p1_avg)
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
            if str(state["last_error"]).startswith("P1:"):
                state["last_error"] = ""  # P1 healthy again
        except Exception as e:
            state["p1_age_s"] = round(time.time() - last_ok, 1) if last_ok else None
            state["last_error"] = f"P1: {e}"

        try:
            props = read_bat()
            state["soc"] = props.get("electricLevel")
            if str(state["last_error"]).startswith("BAT:"):
                state["last_error"] = ""  # battery reachable again
            verify_or_retry(props)
            if state["last_written"]["outputLimit"] is None:
                state["last_written"] = {"outputLimit": int(props.get("outputLimit") or 0),
                                         "inputLimit": int(props.get("inputLimit") or 0)}
                ac = props.get("acMode")
                state["direction"] = "charge" if ac == 1 else ("discharge" if ac == 2 else None)
            if state["direction"] and props.get("smartMode") == 0:
                state["direction"] = None  # device rebooted/flipped the RAM flag; re-ensure next write
            drift_check(observe, props)
            fresh = last_p1 is not None and (time.time() - last_ok) <= FAIL_GRACE
            cutoff = time.time() - SURPLUS_WINDOW
            fresh_hist = [(t, v) for (t, v) in state["p1_hist"] if t >= cutoff]
            state["p1_cov"] = len(fresh_hist)
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
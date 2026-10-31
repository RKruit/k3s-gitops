#!/usr/bin/env python3
"""Zendure-follow: zero-export controller for the SolarFlow 2400 AC+ (sv01, Route A).

Reads the HomeWizard P1 (local API v1) once per second and steers the Zendure
device over its zenSDK local HTTP API (GET /properties/report, POST
/properties/write).

Control law (incremental, damped): the P1 reading already includes the
battery's own output, so the target is the MEASURED feed plus half of the
grid error:
  import  > HOLD_HI -> outputLimit = feed + 0.5*(import - MARGIN), capped OUT_CAP
  import  < CHARGE_AT -> inputLimit = charged + 0.5*(export - MARGIN), capped IN_CAP
  else              -> hold (no writes); grid idles between 0 and HOLD_HI.
Grid error halves every step -> converges in a few seconds without ringing.

Failsafe: no valid P1 reading for FAIL_GRACE seconds -> write 0/0 (stop).
Observe mode (--observe): computes and logs targets, writes nothing.

Status served on http://192.168.1.10:8891/status.json
"""
import json
import signal
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- tuning -----------------------------------------------------------------
P1_URL = "http://192.168.68.173/api/v1/data"
BAT_URL = "http://192.168.68.227"
SN = "HEC4NLNBP107471"
MARGIN = 20        # W: keep this much always importing (never push out)
HOLD_HI = 40       # W: import band 0..HOLD_HI -> hold the current state (no writes)
CHARGE_AT = -40    # W: export beyond this -> charge regime
EXPORT_NUDGE = 10  # W: export beyond this inside the hold band -> shave 15 W off
OUT_CAP = 800      # W: max discharge (2400 AC+ can do more; conservative start)
IN_CAP = 800       # W: max charge
GAIN = 0.5         # fraction of grid error applied per step (stable: 0<GAIN<1)
WRITE_MIN = 5      # W: skip writes while |target - last_written| <= this
WRITE_GAP = 1.5    # s: minimum time between writes
LOOP = 1.0         # s: P1 poll interval
FAIL_GRACE = 10.0  # s without a valid P1 read -> steer to 0/0
STATUS_PORT = 8891

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
    props = {"outputLimit": out, "inputLimit": in_}
    body = json.dumps({"sn": SN, "properties": props}).encode()
    req = urllib.request.Request(
        f"{BAT_URL}/properties/write", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=4) as r:
        resp = r.read(200).decode("utf-8", "replace")
    state["writes"] += 1
    state["last_write_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["last_written"] = {"outputLimit": out, "inputLimit": in_}
    print(f"WRITE {props} -> {resp}", flush=True)
    return resp


def compute_target(p1, props, last_written):
    """Return (out_target, in_target) or None to hold the current state.

    p1: net grid power in W (positive = import, negative = export).
    """
    soc = props.get("electricLevel")
    ceil_pct = (props.get("socSet") or 1000) / 10.0   # report stores tenths of a percent
    floor_pct = (props.get("minSoc") or 0) / 10.0
    if soc is None:
        raise ValueError("electricLevel missing in report")

    if p1 > HOLD_HI:
        if soc > floor_pct:
            feed = props.get("outputHomePower") or 0          # measured W into the home now
            tgt = feed + GAIN * (p1 - MARGIN)
            tgt = min(max(tgt, 0.0), OUT_CAP)
            tgt = 5 * round(tgt / 5)
            if last_written["outputLimit"] is not None and abs(tgt - last_written["outputLimit"]) <= WRITE_MIN:
                return None
            state["regime"] = "discharge"
            return int(tgt), 0
        state["regime"] = "battery floor"
        return 0, 0

    if p1 < CHARGE_AT:
        if soc < ceil_pct:
            charged = (last_written["inputLimit"] or 0)       # device follows its limit
            tgt = charged + GAIN * (-p1 - MARGIN)
            tgt = min(max(tgt, 0.0), IN_CAP)
            tgt = 5 * round(tgt / 5)
            if last_written["inputLimit"] is not None and abs(tgt - last_written["inputLimit"]) <= WRITE_MIN \
                    and (last_written["outputLimit"] or 0) == 0:
                return None
            state["regime"] = "charge"
            return 0, int(tgt)
        state["regime"] = "battery full"
        return 0, 0

    state["regime"] = "hold"
    if p1 < -EXPORT_NUDGE and (last_written["outputLimit"] or 0) > 0:
        # tiny export in the hold band -> gently shave the output limit
        tgt = (last_written["outputLimit"] or 0) - 15
        tgt = 5 * round(tgt / 5)
        if tgt >= 0:
            state["regime"] = "nudge"
            return max(int(tgt), 0), 0
    return None


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
        except Exception as e:
            state["p1_age_s"] = round(time.time() - last_ok, 1) if last_ok else None
            state["last_error"] = f"P1: {e}"

        try:
            props = read_bat()
            state["soc"] = props.get("electricLevel")
            fresh = last_p1 is not None and (time.time() - last_ok) <= FAIL_GRACE
            tgt = compute_target(last_p1, props, state["last_written"]) if fresh else (0, 0)
            state["target"] = {"outputLimit": tgt[0], "inputLimit": tgt[1]} if tgt else state["target"]

            now = time.time()
            if tgt and not (tgt[0] == state["last_written"]["outputLimit"]
                            and tgt[1] == state["last_written"]["inputLimit"]) \
                    and now >= next_write_ok:
                if observe:
                    print(f"OBSERVE p1={last_p1:.0f}W -> out={tgt[0]}W in={tgt[1]}W "
                          f"(feed_meas={props.get('outputHomePower')}, soc={props.get('electricLevel')})", flush=True)
                else:
                    write(tgt[0], tgt[1])
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
    threading.Thread(target=main_loop, args=(observe,), daemon=True).start()
    print(f"zendure-follow up ({'observe' if observe else 'control'}), status on :{STATUS_PORT}/status.json", flush=True)
    ThreadingHTTPServer(("0.0.0.0", STATUS_PORT), Handler).serve_forever()
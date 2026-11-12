#!/usr/bin/env python3
"""zenki-watch: READ-ONLY logger for the Zendure AC+ report vs the sv01 law.

Purpose (Robert, 12-11-2026: 'ik wil zenki live zien'): the zendure_follow
controller is in --observe (writes nothing), ZENKI-AI/HEMS steers the battery
instead. This watcher polls the local AC+ report and logs:

  CHG  - every change in limit/direction/flow-STATE/electricLevel/minSoc.
         Because the observe-controller NEVER writes (writes counter stays 0),
         any limit/direction change here is ZENKI/HEMS (or the device itself).
  HB   - 60 s heartbeat: SoC, flow watts, P1 (HomeWizard, same Iskra telegram),
         minSoc, the law's would-be target and current price — the ZENKI-vs-law
         comparison in one log.

Read-only: never POSTs to the device. View: `journalctl -u zenki-watch -f`.
"""
import json
import time
import urllib.request

BAT_URL = "http://192.168.68.229/properties/report"
P1_URL = "http://192.168.68.221/api/v1/data"
STATUS_URL = "http://192.168.1.10:8891/status.json"
POLL_S = 2.0
HB_S = 60.0

KEYS = ("inputLimit", "outputLimit", "acMode", "smartMode", "electricLevel", "minSoc")


def get(url, timeout=6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as f:
            return json.load(f)
    except Exception:
        return None


def flow_state(p):
    gi, oh = p.get("gridInputPower") or 0, p.get("outputHomePower") or 0
    if gi > 50:
        return "charge"
    if oh > 50:
        return "discharge"
    return "idle"


def law_line(st):
    if not st:
        return "law=? price=?"
    tgt = st.get("target") or {}
    return "law out={}W in={}W price={}".format(
        tgt.get("outputLimit"), tgt.get("inputLimit"), st.get("price_now"))


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(kind, msg):
    print("{} {} {}".format(kind, ts(), msg), flush=True)


prev_sig, hb_at, last_warn = None, 0.0, 0.0
last_gi, last_oh = 0, 0
while True:
    loop = time.time()
    p = get(BAT_URL)
    if not p:
        if loop - last_warn > 60:
            last_warn = loop
            log("WARN", "battery report unreadable")
    else:
        props = p.get("properties", {})
        last_gi = props.get("gridInputPower") or 0
        last_oh = props.get("outputHomePower") or 0
        cur = {k: props.get(k) for k in KEYS}
        cur["flow"] = flow_state(props)
        st = get(STATUS_URL) or {}
        writes = st.get("writes")
        sig = dict(cur)
        if prev_sig is not None and sig != prev_sig:
            diff = " | ".join(
                "{} {}->{}".format(k, prev_sig.get(k), cur.get(k))
                for k in KEYS + ("flow",) if prev_sig.get(k) != cur.get(k))
            hw = (get(P1_URL) or {}).get("active_power_w")
            log("CHG", "{} | P1={:+.0f}W | {} | writes={}".format(
                diff, hw if hw is not None else -9999.0, law_line(st), writes))
            hb_at = loop  # heartbeat reset on activity
            prev_sig = sig
        elif prev_sig is None:
            prev_sig = sig
            hw = (get(P1_URL) or {}).get("active_power_w")
            log("INIT", "{} | P1={:+.0f}W | {} | writes={}".format(
                json.dumps(cur, separators=(",", ":")),
                hw if hw is not None else -9999.0, law_line(st), writes))
            hb_at = loop
        elif loop - hb_at >= HB_S:
            hw = (get(P1_URL) or {}).get("active_power_w")
            log("HB", "soc={} {} gi={}W oh={}W P1={:+.0f}W smartMode={} acMode={} minSoc={} | {} | writes={}".format(
                prev_sig.get("electricLevel"), prev_sig.get("flow"),
                last_gi, last_oh, hw if hw is not None else -9999.0,
                prev_sig.get("smartMode"), prev_sig.get("acMode"), prev_sig.get("minSoc"),
                law_line(st), writes))
            hb_at = loop
    time.sleep(POLL_S)

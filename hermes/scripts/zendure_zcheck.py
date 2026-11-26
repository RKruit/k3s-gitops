#!/usr/bin/env python3
"""ZENKI-2 momentopname (eenmalige cron-checks 16-11): accu + P1 + prijs +
de observe-wet als referentie. Read-only; 4 regels output."""
import datetime
import json
import urllib.request

def get(url):
    with urllib.request.urlopen(url, timeout=6) as r:
        return json.loads(r.read().decode())

now = datetime.datetime.now()
out = [f"ZENKI-check {now:%d-%m %H:%M}"]
try:
    p = get("http://192.168.68.229/properties/report")["properties"]
    flow = ("LAAD %.0f W" % p["gridInputPower"]) if p.get("gridInputPower", 0) > 50 else (
           ("ONTLAAD %.0f W" % p["outputHomePower"]) if p.get("outputHomePower", 0) > 50 else
           ("order %s %d W — GEEN flow" % ("ontl" if p.get("acMode") == 2 else "laad",
                                          (p.get("outputLimit") or 0))))
    out.append(f"accu: SoC {p.get('electricLevel')}% · vloer {p.get('minSoc', 0)/10:.0f}% · "
               f"limieten {p.get('outputLimit')}/{p.get('inputLimit')} · {flow}")
except Exception as e:
    out.append(f"accu onleesbaar: {type(e).__name__}")
try:
    p1 = get("http://192.168.68.221/api/v1/data")
    out.append("net: %+.0f W (+ = import)" % p1["active_power_w"])
except Exception as e:
    out.append(f"P1 onleesbaar: {type(e).__name__}")
try:
    pr = json.load(open("/var/lib/zendure-follow/prices.json"))
    h = now.hour
    rest = {int(k): round(v, 2) for k, v in sorted(((int(k), v) for k, v in pr["hourly"].items())) if int(k) >= h}
    out.append(f"prijzen vanaf {h}: {rest}")
    law = get("http://192.168.1.10:8891/status.json")
    out.append(f"observe-wet: k={law.get('price_k')} cut={law.get('price_charge_thr')} "
               f"regime={law.get('price_regime')} (writes={law.get('writes')})")
except Exception as e:
    out.append(f"status/prijzen onleesbaar: {type(e).__name__}")
print("\n".join(out))

#!/usr/bin/env python3
"""Zendure plan-delivery: levert het dagplan zodra de morgendag compleet/in de cache
staat (of herbieuwd wordt) - maximaal een keer per prijzenset.

Samenwerking met de cron-chain (15:05 / 15:35 / 16:15 / 16:45 + 20:00-terugvang):
elke run probeert het plan te leveren; zonder nieuwe set is de stdout leeg en
levert de cron niets (zelfde watchdog-semantiek als het oude 20:00-avondplan).

Logica:
- key = sha256 van {date, hourly} in prices-tomorrow.json (wat DE wet rekent -
  een bron, geen tweede mening).
- niet compleet (< 24 uurtjes) -> zwijgen.
- key al in marker -> zwijgen.
- report-script levert niets -> zwijgen, marker niet zetten.
- anders: marker schrijven en het plan als stdout printen (de cron stuurt het).
- zelfde dag, andere key -> revisie-prefix "REVISIE:".
"""
import hashlib
import json
import os
import subprocess
import sys

CACHE = "/var/lib/zendure-follow/prices-tomorrow.json"
REPORT = os.path.expanduser("~/.hermes/scripts/zendure_schedule_report.py")
MARKER = "/var/lib/zendure-follow/plan_delivered.json"


def _key(date_s, hourly):
    payload = json.dumps({"date": date_s, "hourly": hourly},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def main():
    dry = "--dry" in sys.argv
    try:
        with open(CACHE) as f:
            cache = json.load(f)
        hourly = cache.get("hourly") or {}
        date_s = cache.get("date") or ""
        if len(hourly) < 24 or not date_s:
            return  # incompleet -> niets
        key = _key(date_s, hourly)
    except Exception:
        return  # geen leesbare cache -> zwijgen

    delivered = {}
    try:
        with open(MARKER) as f:
            delivered = json.load(f)
    except Exception:
        pass
    if delivered.get("key") == key:
        return  # dezelfde set al eerder geleverd

    r = subprocess.run([sys.executable, REPORT],
                       capture_output=True, text=True, timeout=180)
    out = (r.stdout or "").strip()
    if not out:
        return  # report heeft niets (watchdog) -> zwijgen

    if dry:
        print("[DRY] zou leveren; marker ongemoeid gelaten")
        return

    prefix = ""
    if delivered.get("date") == date_s:
        prefix = "REVISIE (%s):\n" % date_s
    with open(MARKER, "w") as f:
        json.dump({"key": key, "date": date_s}, f)
    print(prefix + out)


if __name__ == "__main__":
    main()

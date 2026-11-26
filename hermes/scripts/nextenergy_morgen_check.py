#!/usr/bin/env python3
"""Eenmalige controle: is het NextEnergy-morgenrapport om 16:00 echt verstuurd?

Leest de dedupe-marker (welke dag/prijsset is geleverd) en de wrapper-log.
Print altijd één statusregel (zodat de cron hem levert).
"""
import datetime
import json
import os
import re

MARKER = os.path.expanduser("~/.hermes/state/nextenergy_morgen_delivered.json")
LOG = os.path.expanduser("~/.hermes/logs/nextenergy-morgen.log")

now = datetime.datetime.now().astimezone()
target = (now.date() + datetime.timedelta(days=1)).isoformat()
DAGEN = ["ma", "di", "wo", "do", "vr", "za", "zo"]
try:
    t = datetime.date.fromisoformat(target)
    label = "%s %02d-%02d" % (DAGEN[t.weekday()], t.day, t.month)
except Exception:
    label = target

delivered = None
try:
    delivered = json.load(open(MARKER))
except Exception:
    pass

sent_lines = []
try:
    for line in open(LOG, encoding="utf-8"):
        if line.startswith(now.date().isoformat()) and "verstuurd naar groep" in line:
            sent_lines.append(line.strip())
except Exception:
    pass

errors = []
try:
    for line in open(LOG, encoding="utf-8"):
        if line.startswith(now.date().isoformat()) and ("mislukt" in line or "Traceback" in line):
            errors.append(line.strip())
except Exception:
    pass

if delivered and delivered.get("date") == target and sent_lines:
    print("✅ NextEnergy 16:00-cron: rapport voor %s is naar de WhatsApp-groep "
          "verstuurd (%s)." % (label, sent_lines[-1].split(" ", 2)[-1]))
elif delivered and delivered.get("date") == target:
    print("⚠️ NextEnergy 16:00-cron: prijsset voor %s is geleverd volgens de "
          "marker, maar er staat geen verzendregel in het log." % label)
else:
    extra = (" Laatste fout: " + errors[-1]) if errors else ""
    print("⚠️ NextEnergy 16:00-cron: voor %s is (nog) niets naar WhatsApp "
          "verstuurd — prijzen waren om %s niet gepubliceerd of de app-bron "
          "faalde; de poll liep tot 16:30.%s"
          % (label, now.strftime("%H:%M"), extra))

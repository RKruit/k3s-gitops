#!/usr/bin/env python3
"""NextEnergy rapport voor MORGEN (16:00-cron -> WhatsApp).

Bron: NextEnergy app-API (persoonlijk contract) — morgenprijzen publiceren
rond 16:00. De cron start om 16:00 en wacht (pollt) tot de data er is,
standaard max 30 minuten; daarna stil (watchdog: lege stdout = niets sturen).

Output: Nederlandse tekst op stdout; lege stdout = niets leveren.
Opties:
  --day today|tomorrow   welke dag (default tomorrow; today = testformaat)
  --max-wait SECONDS     hoe lang pollen op publicatie (default 1800)
  --force                dedupe-marker negeren (test)
  --no-dedupe            marker niet lezen/schrijven
"""
import datetime
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zendure_schedule_report import (  # noqa: E402
    DAGEN, MAANDEN, app_data, eur, spot_hourly, welke_dag, widget_prices,
)

MARKER = os.path.expanduser("~/.hermes/state/nextenergy_morgen_delivered.json")


def parse_day(data):
    """Uurprijzen (klokuren) + gemiddelde + gas uit één app-response."""
    if not data:
        return None, None, None
    lst = (data.get("DataPoints") or {}).get("List") or []
    dp = {int(it["Label"]): float(it["Value"]) for it in lst
          if it.get("Value") is not None}
    if not (20 <= len(dp) <= 24):
        return None, None, None
    # DP-label L hoort bij klok-uur L+1; klok-0 = DP[23] van dezelfde set.
    prices = {}
    if 23 in dp:
        prices[0] = dp[23]
    for h in range(1, 24):
        if (h - 1) in dp:
            prices[h] = dp[h - 1]
    if len(prices) < 20:
        return None, None, None
    try:
        avg = float(data.get("AvgElectricityPrice"))
        avg = avg if avg > 0 else None
    except (TypeError, ValueError):
        avg = None
    if avg is None:
        avg = sum(prices.values()) / len(prices)
    try:
        gas = float(data.get("CurrentGasPrice"))
        gas = gas if gas > 0 else None
    except (TypeError, ValueError):
        gas = None
    return prices, avg, gas


def widget_tomorrow(target):
    """Publieke widget als app-bron faalt; alleen als de bars morgen zijn."""
    bars = widget_prices()  # {label: prijs}, DP-shift
    if len(bars) < 20:
        return None
    if welke_dag(bars) != target:
        return None
    prices = {h + 1: p for h, p in bars.items() if 0 <= h <= 22}
    if 23 in bars:
        prices[0] = bars[23]
    return prices if len(prices) >= 20 else None


def fetch(target, max_wait, allow_widget):
    """Probeer tot max_wait seconden; app eerst, widget alleen op de eerste
    poging (chromium-run is duur en de app-bron is de persoonlijke waarheid)."""
    deadline = time.monotonic() + max(0, max_wait)
    attempt = 0
    while True:
        attempt += 1
        data = app_data(target)
        prices, avg, gas = parse_day(data)
        if prices:
            return prices, avg, gas
        if allow_widget and attempt == 1:
            prices = widget_tomorrow(target)
            if prices:
                return prices, sum(prices.values()) / len(prices), None
        if time.monotonic() + 120 > deadline:
            return None, None, None
        print("poging %d: prijzen nog niet gepubliceerd, 120 s wachten" % attempt,
              file=sys.stderr)
        time.sleep(120)


def build(target, prices, avg, gas, label="morgen"):
    datum = "%s %d %s" % (DAGEN[target.weekday()], target.day, MAANDEN[target.month])
    blocks = []
    for start in range(0, 24, 3):
        vals = [prices[h] for h in range(start, start + 3) if h in prices]
        if vals:
            blocks.append((start, sum(vals) / len(vals)))
    cheapest = min(blocks, key=lambda b: (b[1], b[0]))
    dearest = max(blocks, key=lambda b: (b[1], -b[0]))
    top = max(prices, key=lambda h: (prices[h], -h))
    low = min(prices, key=lambda h: (prices[h], h))
    cheap_hours = sorted(prices, key=lambda h: (prices[h], h))[:5]

    out = ["⚡ Stroomprijzen %s — %s" % (label, datum),
           "NextEnergy all-in, incl. btw · gemiddeld %s/kWh" % eur(avg),
           "",
           "Duurste uur: %s (%02d:00-%02d:00)" % (eur(prices[top]), top, (top + 1) % 24),
           "Goedkoopste uur: %s (%02d:00-%02d:00)" % (eur(prices[low]), low, (low + 1) % 24),
           "",
           "3-uursblokken (€/kWh):"]
    for start, a in blocks:
        mark = ""
        if start == cheapest[0]:
            mark = "  ← goedkoopste"
        if start == dearest[0]:
            mark = "  ← duurste"
        out.append("%02d-%02d  %s%s" % (start, min(start + 3, 24), eur(a), mark))
    out.append("")
    out.append("Goedkoopste uren: " + " · ".join(
        "%02d:00 %s" % (h, eur(prices[h])) for h in cheap_hours))
    if gas:
        out.append("Gasprijs morgen: %s/m³" % eur(gas))
    return "\n".join(out), prices


def main():
    args = sys.argv[1:]
    day = "tomorrow"
    max_wait = 1800
    force = "--force" in args
    no_dedupe = "--no-dedupe" in args
    if "--day" in args:
        day = args[args.index("--day") + 1]
    if "--max-wait" in args:
        max_wait = int(args[args.index("--max-wait") + 1])
    today = datetime.datetime.now().astimezone().date()
    target = today if day == "today" else today + datetime.timedelta(days=1)

    prices, avg, gas = fetch(target, max_wait, allow_widget=(day != "today"))
    if not prices:
        return  # niets gepubliceerd -> stil

    text, prices = build(target, prices, avg, gas,
                         label="vandaag" if day == "today" else "morgen")
    if not no_dedupe:
        key = hashlib.sha256(
            json.dumps([target.isoformat(), sorted(prices.items())]).encode()
        ).hexdigest()
        try:
            with open(MARKER) as f:
                prev = json.load(f)
        except Exception:
            prev = {}
        # Alleen binnen dezelfde dag onderdrukken; een nieuwe dag is altijd nieuw.
        if not force and prev.get("key") == key and prev.get("date") == target.isoformat():
            return
        os.makedirs(os.path.dirname(MARKER), exist_ok=True)
        with open(MARKER, "w") as f:
            json.dump({"key": key, "date": target.isoformat()}, f)
    print(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("NextEnergy-morgenrapport: fout (%s)" % str(exc)[:160])
        sys.exit(1)

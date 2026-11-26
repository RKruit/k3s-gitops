#!/usr/bin/env python3
"""NextEnergy dagrapport: goedkoopste/duurste 3-uursblokken + hoogste uurprijs.
Scrapes the public OutSystems widget page (mijn.nextenergy.nl/Website_CW/MarketPrices)
with headless chromium --dump-dom, parses Highcharts aria-labels + headline values.
Usage: nextenergy_report.py   (no args; prints report, exits 0 even on failure)
"""
import datetime
import re
import subprocess
import sys
import tempfile
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zendure_schedule_report import app_prices as app_dp, app_data as app_full

WIDGET_URL = "https://mijn.nextenergy.nl/Website_CW/MarketPrices"

def fetch_dom():
    for budget in (15000, 25000):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [
                "chromium-browser", "--headless=new", "--disable-gpu",
                "--no-sandbox", "--no-first-run", "--disable-crash-reporter",
                "--user-data-dir=" + os.path.join(tmp, "profile"),
                "--virtual-time-budget=" + str(budget),
                "--window-size=1280,900", "--dump-dom", WIDGET_URL,
            ]
            try:
                out = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=90
                ).stdout
            except subprocess.TimeoutExpired:
                continue
        bars = {(int(m.group(1)), float(m.group(2).replace(",", ".")))
                for m in re.finditer(
                    r'aria-label="(\d+), (\d+(?:\.\d+)?)(?:,\d+)?\. (?:Stroom|Electricity)\.', out)}
        if len(bars) >= 20:
            return out, bars
    return None, {}

def grab(out, label):
    m = re.search(label + r".{0,300}?€[^0-9]{0,30}?(\d+,\d+)", out, re.S)
    return m.group(1) if m else "?"

def main():
    out, bars = fetch_dom()
    if not out:
        print("NextEnergy-rapport: scrape mislukt (widget-pagina niet leesbaar, probe morgen opnieuw).")
        return
    today = datetime.datetime.now().astimezone()
    days_nl = ["maandag", "dinsdag", "woensdag", "donderdag",
               "vrijdag", "zaterdag", "zondag"]
    months_nl = ["", "januari", "februari", "maart", "april", "mei", "juni",
                 "juli", "augustus", "september", "oktober", "november", "december"]
    datum = days_nl[today.weekday()] + " " + str(today.day) + " " + months_nl[today.month]

    # DP-labels lopen 1 uur vóór op de klok (factuur-bevestigd 03-11-2026):
    # klok-uur 0 = DP[23] van gisteren, klok-uur h = DP[h-1] van vandaag.
    hours = {}
    app_today = app_full(today.date())
    dp_t, gas = None, None
    if app_today:
        lst = (app_today.get("DataPoints") or {}).get("List") or []
        dp = {int(it["Label"]): float(it["Value"]) for it in lst
              if it.get("Value") is not None}
        if 20 <= len(dp) <= 24:  # zelfde kwartier-wachter als app_prices
            dp_t = dp
        try:
            gas = float(app_today.get("CurrentGasPrice"))  # persoonlijke all-in
        except (TypeError, ValueError):
            gas = None
    dp_y = app_dp(today.date() - datetime.timedelta(days=1)) if dp_t else None
    if dp_t:
        p0 = (dp_y or {}).get(23)
        if p0 is not None:
            hours[0] = p0
        for h in range(1, 24):
            if (h - 1) in dp_t:
                hours[h] = dp_t[h - 1]
    if len(hours) < 20:
        hours = {h + 1: p for h, p in bars.items() if 0 <= h <= 22}
    if len(hours) < 20:
        print("NextEnergy-rapport: geen uurprijzen gevonden (app + widget).")
        return

    block_avgs = []
    for start in range(0, 24, 3):
        vals = [hours[h] for h in range(start, start + 3) if h in hours]
        if vals:
            block_avgs.append((start, sum(vals) / len(vals), len(vals)))
    cheapest = min(block_avgs, key=lambda b: (b[1], b[0]))
    dearest = max(block_avgs, key=lambda b: (b[1], -b[0]))
    top_hour = max(hours, key=lambda h: (hours[h], -h))

    def eur(x):
        return ("€%.2f" % x).replace(".", ",")

    lines = []
    lines.append("⚡ NextEnergy — " + datum + " (all-in, incl. btw)")
    cur_h = today.hour
    if cur_h in hours:
        lines.append("Stroom nu (%02d:00-%02d:00):  %s / kWh"
                     % (cur_h, cur_h + 1, eur(hours[cur_h])))
    else:
        lines.append("Stroom nu:         €" + grab(out, r"Huidige stroomprijs")
                     + " / kWh (kop loopt soms ±1 uur achter op de balken)")
    avg = None
    if app_today:
        try:
            avg = float(app_today.get("AvgElectricityPrice"))
        except (TypeError, ValueError):
            avg = None
    if avg is not None:
        lines.append("Gemiddeld vandaag: " + eur(avg) + " / kWh")
    else:
        lines.append("Gemiddeld vandaag: €" + grab(out, r"Gemiddelde stroomprijs") + " / kWh")
    if gas is not None:
        lines.append("Gasprijs vandaag:  " + eur(gas) + " / m³")
    else:
        lines.append("Gasprijs vandaag:  €" + grab(out, r"Gasprijs (?:vanaf 06:00|vandaag)")
                     + " / m³ (widget-generiek — app-bron faalde)")
    lines.append("")
    lines.append("3-uursblokken (avg €/kWh):")
    for start, avg, n in block_avgs:
        end = min(start + 3, 24)
        mark = ""
        if start == cheapest[0]:
            mark = "  <- goedkoopste blok"
        if start == dearest[0]:
            mark = "  <- duurste blok"
        lines.append("%02d-%02d  %s%s" % (start, end, eur(avg), mark))
    lines.append("")
    lines.append("Hoogste uurprijs: %s (%02d:00-%02d:00)"
                 % (eur(hours[top_hour]), top_hour, top_hour + 1))
    print("\n".join(lines))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never crash the cron; report in NL
        print("NextEnergy-rapport: fout (" + str(exc)[:120] + ")")
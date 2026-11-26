#!/usr/bin/env python3
"""Avondrapport: laad-/ontlad-schema van de komende dag (cron 20:00 daily).

Bronprioriteiten, in volgorde:
1. NextEnergy app-API (persoonlijk contract; morgen-data, actief na koppeling
   op 01-11-2026) — /srv/sv01-secrets/nextenergy.env + nextenergy_app.json.
2. Publieke NextEnergy-widget (mijn.nextenergy.nl/Website_CW/MarketPrices) —
   toont normaal alleen vandaag; als de bars na markt-clearing de komende dag
   vertegenwoordigen, gebruik dan die (dag-vaststelling via correlation met
   SMARD/energy-charts spotdata).
Anders: onbekend -> lege output (cron levert niets; watchdog-patroon).

Planlogica = leesbaar live uit zendure_follow.py (doorloopt in sync):
  prijs <= P30 -> LAAD tot CHARGE_TARGET (max IN_CAP)
  prijs >= P70 -> ONTLAAD (zero-export, tot device-vloer)
  anders       -> wacht;  vlakke dag (spread < MIN_SPREAD) -> geen cyclus
  + DISCHARGE_NOT_BEFORE_DATE/HOUR tweak van de dag zelf.

Output: NL tekst; exit 0 altijd (lege stdout = niets leveren).
"""
import datetime
import http
import json
import http.cookiejar
import math
import urllib.parse
import urllib.request
import os
import re
import statistics
import subprocess
import sys
import tempfile

Tz = datetime.timezone(datetime.timedelta(hours=1))
CONTROLLER = "/opt/sv01-config/sv01/scripts/zendure_follow.py"
WIDGET_URL = "https://mijn.nextenergy.nl/Website_CW/MarketPrices"
SPOT_URL = "https://api.energy-charts.info/price?bzn=NL&start={start}&end={end}"
APP_AUTH_FILE = "/srv/sv01-secrets/nextenergy.env"
APP_CFG_FILE = os.path.expanduser("~/.hermes/zendure/nextenergy_app.json")
BAR_RE = re.compile(r'aria-label="(\d+), (\d+(?:\.\d+)?)(?:,\d+)?\. (?:Stroom|Electricity)\.')

DAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MAANDEN = ["", "januari", "februari", "maart", "april", "mei", "juni",
           "juli", "augustus", "september", "oktober", "november", "december"]


def leer_controller():
    """Lees de stuur-constanten uit zendure_follow.py zodat het plan altijd
    in sync is met wat de regelaar echt doet."""
    src = open(CONTROLLER).read()
    def num(name, default):
        m = re.search(rf"^{name}\s*=\s*([\d.]+)", src, re.M)
        return float(m.group(1)) if m else default
    consts = {
        "charge_pct": num("CHARGE_PCT", 0.30),
        "discharge_pct": num("DISCHARGE_PCT", 0.70),
        "min_spread": num("MIN_SPREAD", 0.03),
        "charge_target": int(num("CHARGE_TARGET", 90)),
        "in_cap": int(num("IN_CAP", 800)),
        "pack_kwh": num("PACK_KWH", 5.46),
        "eff_c": num("CHARGE_EFF", 0.92),
    }
    m = re.search(r'DISCHARGE_NOT_BEFORE_DATE\s*=\s*"([\d-]+)"', src)
    consts["not_before_date"] = m.group(1) if m else None
    m = re.search(r"DISCHARGE_NOT_BEFORE_HOUR\s*=\s*(\d+)", src)
    consts["not_before_hour"] = int(m.group(1)) if m else None
    return consts


def widget_prices():
    """Haal de widget-bars op via headless chromium (proven recipe)."""
    for budget in (15000, 25000):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["chromium-browser", "--headless=new", "--disable-gpu", "--no-sandbox",
                   "--no-first-run", "--disable-crash-reporter",
                   "--user-data-dir=" + os.path.join(tmp, "profile"),
                   "--virtual-time-budget=" + str(budget),
                   "--window-size=1280,900", "--dump-dom", WIDGET_URL]
            try:
                dom = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
            except Exception:
                continue
        bars = {(int(m.group(1)), float(m.group(2).replace(",", ".")))
                for m in BAR_RE.finditer(dom)}
        if len(bars) >= 20:
            return dict(bars)
    return {}


def spot_hourly(day):
    """SMARD/energy-charts NL spot (EUR/kWh) per uur; None wanneer onbekend."""
    start = int(datetime.datetime.combine(day, datetime.time(0), tzinfo=Tz).timestamp())
    try:
        d = json.loads(urllib.request.urlopen(
            SPOT_URL.format(start=start, end=start + 24 * 3600), timeout=25).read())
    except Exception:
        return None
    acc = {}
    for i, t in enumerate(d.get("unix_seconds", [])):
        h = datetime.datetime.fromtimestamp(t, Tz)
        if h.date() == day:
            acc.setdefault(h.hour, []).append(d["price"][i] / 1000.0)
    return {h: statistics.mean(v) for h, v in acc.items() if len(v) == 4} or None


def app_data(day):
    """NextEnergy app-API (persoonlijk contract) — recept in nextenergy_app.json.

    Werkt zo: login (ActionDoLogin, X-CSRFToken mag leeg pre-login) →
    moduleversioninfo voor de actuele moduleVersion → prijs-DataAction met
    het vaste body-sjabloon (AuxContractProposition erin; de datum gaat via
    clientVariables.PriceDate). Label in DataPoints = kalenderuur.
    Faalt of geeft leeg -> None (widget-valback pakt het over).
    """
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
            ts = int(datetime.datetime.now().timestamp() * 1000)
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
        body = json.loads(json.dumps(cfg["price_body"]))  # deep copy
        body["versionInfo"]["moduleVersion"] = modver
        body["clientVariables"]["PriceDate"] = day.isoformat()
        body["clientVariables"]["PriceCostLevel"] = cfg["cost_level"]
        h2 = dict(H)
        h2["X-CSRFToken"] = crf
        h2["Referer"] = base + "/Mobile_EnergyNext/MarketPrices"
        resp = json.loads(opener.open(urllib.request.Request(
            base + cfg["price_path"], data=json.dumps(body).encode(),
            headers=h2, method="POST"), timeout=25).read())
        if resp.get("exception"):
            return None
        data = resp.get("data") or {}
        return data or None
    except Exception:
        return None


def app_prices(day):
    """Uurprijzen (electricity) uit de app-response van app_data(day)."""
    d = app_data(day)
    if not d:
        return None
    lst = (d.get("DataPoints") or {}).get("List") or []
    prices = {int(it["Label"]): float(it["Value"]) for it in lst
              if it.get("Value") is not None}
    # afrekening is per uur (NextEnergy: uurprijs = gemiddelde van de 4
    # kwartierprijzen). Krijgt deze call ooit 96 punten terug (kwartier-
    # afrekening live), dan NIET stilletjes verwerken maar de widget-
    # valback pakken en de kwartier-steering opnieuw ontwerpen.
    return prices if 20 <= len(prices) <= 24 else None


def dp_to_clock(dp_today, dp_prev):
    """DP-label L bevat de prijs van klok-uur L+1 (factuur-bevestigd 03-11-2026
    door Robert: maandag 06-07 = DP[5] 0,42, 14-15 = DP[13] 0,37).
    Klok-uur 0 van dag D = DP[23] van DIEZELFDE dag D (geroteerde labeling —
    15-11-2026 bewezen met de spot-toeslag-band en Robert's app-lezing; de oude
    'DP[23] van D-1'-regel was een leesfout en zette uur-0 altijd een dag terug)."""
    if not dp_today:
        return None
    prices = {}
    p0 = dp_today.get(23)
    if p0 is not None:
        prices[0] = p0
    for h in range(1, 24):
        v = dp_today.get(h - 1)
        if v is not None:
            prices[h] = v
    return prices or None


def welke_dag(bars_hourly):
    """Welke dag vertegenwoordigen de widget-bars? Correlatie met spot-vectors."""
    now = datetime.date.today()
    best, best_corr = now, -2.0
    for day in (now, now + datetime.timedelta(days=1)):
        spot = spot_hourly(day)
        if not spot or len(spot) < 20:
            continue
        # DP-shift: bars[h] hoort bij klok-uur h+1 -> tegen spot[h+1] testen
        pairs = [(bars_hourly[h] - spot[h + 1], spot[h + 1])
                 for h in sorted(spot) if h in bars_hourly and (h + 1) in spot]
        if len(pairs) < 20:
            continue
        # adder-consistentie: laagste stdev van (bars - spot) wint
        devs = [p[0] for p in pairs]
        sd = statistics.pstdev(devs)
        if sd < best_corr:
            best, best_corr = day, sd
    return best


def plan_regimes(prices, consts, target):
    vals = sorted(prices.values())
    c_thr = vals[max(int(round(consts["charge_pct"] * len(vals))), 1) - 1]
    d_thr = vals[min(int(round(consts["discharge_pct"] * len(vals))), len(vals)) - 1]
    spread = d_thr - c_thr
    # Rang-keuze voor het laden (15-11-2026, 'pak de laagste bij het laden'):
    # de k goedkoopste uren van de komende dag halen het doel vanaf de
    # device-vloer (10%); LAAD hours = die k-tal. De regelaar rekent live
    # met de echte SoC — bij een lege accu pakt hij extra resterende uren.
    need_gross = max(0.0, (consts["charge_target"] - 10.0) / 100.0
                     * consts["pack_kwh"] / consts["eff_c"])
    k = max(1, min(int(math.ceil(need_gross / (consts["in_cap"] / 1000.0))),
                   len(vals)))
    cut = vals[k - 1]
    laad_hours = set(sorted(prices, key=lambda h: (prices[h], h))[:k])
    regimes = {}
    for h, p in prices.items():
        not_before_applies = (consts["not_before_date"] == target.isoformat()
                              and consts["not_before_hour"] is not None and h < consts["not_before_hour"])
        if h in laad_hours:
            regimes[h] = "LAAD"
        elif p >= d_thr and not not_before_applies:
            regimes[h] = "ONTLAAD"
        elif p >= d_thr and not_before_applies:
            regimes[h] = f"even wachten (tot {consts['not_before_hour']}:00)"
        else:
            regimes[h] = "—"
    return c_thr, d_thr, spread, regimes, cut, k


def eur(x):
    return ("€%.2f" % x).replace(".", ",")


def main():
    target = datetime.date.today() + datetime.timedelta(days=1)
    consts = leer_controller()

    src = None
    dp_t = app_prices(target)
    dp_prev = app_prices(target - datetime.timedelta(days=1)) if dp_t else None
    prices = dp_to_clock(dp_t, dp_prev) if dp_t else None
    if prices:
        src = "NextEnergy app (persoonlijk contract)"
    if not prices:
        bars = widget_prices()
        if len(bars) >= 20:
            day = welke_dag(bars)
            if day == target:
                # DP-shift: balk L is klok-uur L+1; label 23 = de geroteerde
                # middernacht-h (klok 0 van diezelfde dag — zie dp_to_clock)
                prices = {h + 1: p for h, p in bars.items() if 0 <= h <= 22} or None
                if 23 in bars:
                    prices = dict(prices or {}); prices[0] = bars[23]
                src = "publieke NextEnergy-widget (DP-shift, klok-0 onbekend)"

    if not prices or len(prices) < 20:
        return  # morgenprijzen onbekend: niets leveren

    c_thr, d_thr, spread, regimes, cut, k = plan_regimes(prices, consts, target)
    datum = DAGEN[target.weekday()] + " " + str(target.day) + " " + MAANDEN[target.month]

    if spread < consts["min_spread"]:
        print("🔋 Zendure-plan %s — vlakke dag (spread %s < %s): geen laad-/"
              "ontladcyclus; de accu blijft in rust." % (datum, eur(spread), eur(consts["min_spread"])))
        print("Bron: " + src)
        return

    print("🔋 Zendure-plan %s (bron: %s)" % (datum, src))
    print("Laaddrempel (rang-keuze, %d uur): %s/kWh · Ontlaadrempel: %s/kWh · "
          "Laden tot %d%%, max %d W"
          % (k, eur(cut), eur(d_thr), consts["charge_target"], consts["in_cap"]))
    print("")
    for h in sorted(p for p in prices):
        label = regimes.get(h, "—")
        print("%02d:00-%02d:00  %s  %s" % (h, (h + 1) % 24, eur(prices[h]),
              "⚡ LAAD" if label == "LAAD" else ("🔌 ONTLAAD" if label == "ONTLAAD" else "— " + label if label != "—" else "—")))
    lad = [h for h, v in sorted(regimes.items()) if v == "LAAD"]
    ont = [h for h, v in sorted(regimes.items()) if v == "ONTLAAD"]
    print("")
    def venster(u):
        if not u:
            return "geen"
        runs, s, prev = [], u[0], u[0]
        for h in u[1:]:
            if h != prev + 1:
                runs.append((s, prev))
                s = h
            prev = h
        runs.append((s, prev))
        return ", ".join("%02d-%02d" % (a, (b + 1) % 24) for a, b in runs)
    print("Laadvenster: " + venster(lad) + " · Ontlaadvenster: " + venster(ont))
    print("Zero-export blijft de harde rand; de regelaar rekent live met de echte SoC (bij een lege accu pakt hij extra resterende uren); bijsturen kan door hier te antwoorden (bv. 'laad tot 85%', 'sla 16-17 over', 'max 90%').")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
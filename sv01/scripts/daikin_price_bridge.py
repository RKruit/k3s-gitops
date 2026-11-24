#!/usr/bin/env python3
"""Daikin brandstofprijs per uur -> P1/P2-bus, via Homey (optie A).

Keten: dit script -> MQTT `homey/p1p2/cmd` -> Homey-flow
"Daikin prijs -> P1P2 bus (sv01)" -> `P1P2/W/P1P2MQTT/bridge0` -> bridge -> Daikin.

Geen taalmodel in de keten: platte python, alleen de constanten hieronder bepalen
het gedrag. De Daikin-manager blijft het vangnet: valt sv01, Homey of de bridge
weg, dan blijft de laatst geschreven brandstofprijs staan en regelt de unit door.

Werking: de hybride manager kiest stroom of gas op de verhouding
    omslag-COP = actieve stroomprijs / brandstofprijs
De stroomprijs is NIET bus-schrijfbaar (veldinstelling C-0C/0D/0E, alleen op het
display), dus dit script stuurt de ene knop die overblijft: de brandstofprijs.
    brandstofprijs = actieve stroomprijs / gewenste omslag
Payload: E3D001F<4 hex> met waarde = brandstofprijs * 10000 (0,21 -> 2100 = 0x0834).

Handmatig aanpassen: alleen de constanten hieronder. Daarna niets herstarten —
de timer leest dit bestand elke run opnieuw.
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- instellingen
OMSLAG_CHEAP = 1.6    # goedkoop uur: pomp moet winnen (lagere omslag = meer pomp)
OMSLAG_NORMAL = 1.9   # normaal uur
OMSLAG_PEAK = 2.4     # duur uur: gas eerder in beeld (hogere omslag = meer gas)
P_CHEAP = 30          # percentile van de dagprijzen waaronder = goedkoop uur
P_PEAK = 70           # percentile waarboven = duur uur
FUEL_MIN_EUR = 0.05   # veiligheidsklemmen op de brandstofprijs
FUEL_MAX_EUR = 0.50
FALLBACK_EL_EUR = 0.45  # als de bus de stroomprijs even niet levert
REFRESH_MIN = 30      # ook zonder wijziging elke 30 min opnieuw sturen

# Stooklijn-deviatie (aanvoertemperatuur) naar buitentemperatuur.
# Getest 24-11-2026: -2 K bij -2 C buiten gaf COP +0,2, ketelbedrijf 39->28%,
# kamer bleef constant. Bij echte vorst is het vermogensverlies te groot.
DEV_MILD_OUTSIDE_C = -3.0   # boven deze buitentemperatuur: volle winst pakken
DEV_COLD_OUTSIDE_C = -8.0   # onder deze: deviatie terug naar 0
DEV_MILD_K = -2.0           # deviatie bij mild weer
DEV_MID_K = -1.0            # deviatie tussen de twee grenzen
DEV_COLD_K = 0.0            # deviatie bij strenge vorst

# --------------------------------------------------------------------- paden
PRICES = Path("/var/lib/zendure-follow/prices.json")   # zendure_follow vult dit
STATE = Path("/var/lib/daikin-price/last.json")
STATUS = Path("/var/lib/daikin-price/status.json")
SECRETS = Path("/srv/sv01-secrets/mosquitto.env")

BROKER = "192.168.1.10"
BROKER_PORT = "1883"
TOPIC_CMD = "homey/p1p2/cmd"                      # Homey-flow luistert hierop
TOPIC_EL = "P1P2/P/P1P2MQTT/bridge0/S/0/Electricity"
TOPIC_GAS = "P1P2/P/P1P2MQTT/bridge0/S/0/Gas"
TOPIC_OUTSIDE = "P1P2/P/P1P2MQTT/bridge0/T/1/Temperature_Outside"
MQTT_USER = "hermes"


def log(msg):
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def mqtt_creds():
    for line in SECRETS.read_text().splitlines():
        if line.startswith("HERMES_MQTT_PASS="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("HERMES_MQTT_PASS niet gevonden in secrets")


def mqtt_sub(topic, wait=6):
    """Laatste (retained/nieuwe) waarde van een topic, of None."""
    try:
        out = subprocess.run(
            ["mosquitto_sub", "-h", BROKER, "-p", BROKER_PORT, "-u", MQTT_USER,
             "-P", mqtt_creds(), "-t", topic, "-C", "1", "-W", str(wait)],
            capture_output=True, text=True, timeout=wait + 4,
        )
        val = out.stdout.strip()
        return val or None
    except Exception as exc:  # noqa: BLE001
        log(f"lezen {topic} mislukt: {exc}")
        return None


def mqtt_pub(topic, payload):
    res = subprocess.run(
        ["mosquitto_pub", "-h", BROKER, "-p", BROKER_PORT, "-u", MQTT_USER,
         "-P", mqtt_creds(), "-t", topic, "-m", payload, "-q", "1"],
        capture_output=True, text=True, timeout=15,
    )
    if res.returncode != 0:
        raise RuntimeError(f"publish mislukt: {res.stderr.strip()}")
    log(f"-> {topic}: {payload}")
    return payload


def deviation_k(outside_c):
    """Gekozen stooklijn-deviatie (K) op basis van de buitentemperatuur."""
    if outside_c is None:
        return DEV_MILD_K          # geen meting: de veilige, geteste stand
    if outside_c >= DEV_MILD_OUTSIDE_C:
        return DEV_MILD_K
    if outside_c >= DEV_COLD_OUTSIDE_C:
        return DEV_MID_K
    return DEV_COLD_K


def deviation_payload(kelvin):
    """E360008%04X, deviatie x10, negatief als two's complement."""
    tenths = int(round(kelvin * 10))
    raw = tenths if tenths >= 0 else 65536 + tenths
    return "E360008%04X" % raw


def percentile(values, pct):
    vals = sorted(values)
    if not vals:
        return 0.0
    k = (len(vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return float(vals[lo] + (vals[hi] - vals[lo]) * (k - lo))


def read_prices():
    data = json.loads(PRICES.read_text())
    today = datetime.now().strftime("%Y-%m-%d")
    if data.get("date") != today:
        log(f"prijzen zijn van {data.get('date')}, vandaag is {today} — niets doen")
        return None
    hourly = {int(h): float(p) for h, p in data.get("hourly", {}).items()}
    if len(hourly) < 12:
        log("te weinig uurprijzen — niets doen")
        return None
    return hourly


def decide(price_now, prices, el_eur):
    p_cheap = percentile(list(prices.values()), P_CHEAP)
    p_peak = percentile(list(prices.values()), P_PEAK)
    if price_now <= p_cheap:
        kind, target = "cheap", OMSLAG_CHEAP
    elif price_now >= p_peak:
        kind, target = "peak", OMSLAG_PEAK
    else:
        kind, target = "normal", OMSLAG_NORMAL
    fuel = min(max(el_eur / target, FUEL_MIN_EUR), FUEL_MAX_EUR)
    return kind, target, p_cheap, p_peak, fuel


def main():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    hourly = read_prices()
    if not hourly:
        STATUS.write_text(json.dumps({"ok": False, "ts": time.time(),
                                      "error": "geen prijzen"}, ensure_ascii=False))
        return 0

    price_now = hourly.get(now.hour, hourly[max(hourly)])

    el_raw = mqtt_sub(TOPIC_EL, wait=8)
    try:
        el_eur = float(el_raw) / 100.0 if el_raw else FALLBACK_EL_EUR
    except ValueError:
        el_eur = FALLBACK_EL_EUR

    kind, target, p_cheap, p_peak, fuel = decide(price_now, hourly, el_eur)
    payload = "E3D001F%04X" % round(fuel * 10000)

    # buitentemperatuur -> stooklijn-deviatie
    out_raw = mqtt_sub(TOPIC_OUTSIDE, wait=6)
    try:
        outside_c = float(out_raw) if out_raw else None
    except ValueError:
        outside_c = None
    dev_k = deviation_k(outside_c)
    dev_payload = deviation_payload(dev_k)

    last = {}
    if STATE.exists():
        try:
            last = json.loads(STATE.read_text())
        except Exception:  # noqa: BLE001
            last = {}
    age_min = (time.time() - last.get("ts", 0)) / 60.0
    changed = last.get("payload") != payload

    dev_changed = last.get("dev_payload") != dev_payload

    sent = False
    if changed or age_min >= REFRESH_MIN:
        # L1 is nodig voordat de bridge writes accepteert; idempotent.
        mqtt_pub(TOPIC_CMD, "L1")
        time.sleep(1)
        mqtt_pub(TOPIC_CMD, payload)
        sent = True
        log(f"verzonden ({'gewijzigd' if changed else 'verversing'}): "
            f"uurprijs €{price_now:.3f} ({kind}) stroom €{el_eur:.3f} "
            f"-> omslag {target} -> brandstof €{fuel:.4f}")
    else:
        log(f"geen wijziging (payload {payload}, {age_min:.0f} min oud)")

    if dev_changed:
        mqtt_pub(TOPIC_CMD, "L1")
        time.sleep(1)
        mqtt_pub(TOPIC_CMD, dev_payload)
        sent = True
        log(f"stooklijn-deviatie {dev_k:+.1f} K (buiten "
            f"{outside_c if outside_c is not None else '?'} °C) -> {dev_payload}")

    # ts hoort bij de laatste ECHTE verzending (ook een verversing), niet bij
    # de laatste payload-wijziging: anders blijft ts uren stilstaan terwijl de
    # brug elke 30 min ververst, en slaat de watchdog onterecht alarm.
    # Zonder deze update bleef age_min ook doorlopen en werd er elke 5 min
    # (timer-interval) opnieuw naar de bus geschreven i.p.v. elke 30 min.
    if sent:
        last = {"payload": payload, "dev_payload": dev_payload,
                "ts": time.time(), "fuel_eur": round(fuel, 4),
                "deviation_k": dev_k}
        STATE.write_text(json.dumps(last))

    STATUS.write_text(json.dumps({
        "ok": True, "ts": time.time(), "hour": now.hour,
        "price_now_eur": price_now, "kind": kind, "omslag": target,
        "electricity_eur": round(el_eur, 4), "fuel_eur": round(fuel, 4),
        "payload": payload, "p_cheap": round(p_cheap, 4), "p_peak": round(p_peak, 4),
        "last_sent_ts": last.get("ts"),
        "outside_c": outside_c, "deviation_k": dev_k, "dev_payload": dev_payload,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

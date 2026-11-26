#!/usr/bin/env python3
"""Watchdog voor de Daikin-prijsbrug (optie A).

Stil zolang alles gezond is; print alleen bij een probleem (de cron-job levert
die output aan Telegram). Bron: /var/lib/daikin-price/status.json (geschreven
door daikin_price_bridge.py, elke 5 min) plus de bus-teruglezing van de
brandstofprijs via MQTT.
"""
import json
import subprocess
import time
from pathlib import Path

STATUS = Path("/var/lib/daikin-price/status.json")
SECRETS = Path("/srv/sv01-secrets/mosquitto.env")
MAX_AGE_S = 5400          # 90 min zonder succesvolle verzending = alarm
TOPIC_GAS = "P1P2/P/P1P2MQTT/bridge0/S/0/Gas"


def creds():
    for line in SECRETS.read_text().splitlines():
        if line.startswith("HERMES_MQTT_PASS="):
            return line.split("=", 1)[1].strip()
    return ""


def bus_gas():
    try:
        out = subprocess.run(
            ["mosquitto_sub", "-h", "192.168.1.10", "-u", "hermes",
             "-P", creds(), "-t", TOPIC_GAS, "-C", "1", "-W", "8"],
            capture_output=True, text=True, timeout=14)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def main():
    problems = []
    if not STATUS.exists():
        problems.append("status.json ontbreekt — is daikin-price.service ooit gedraaid?")
        data = {}
    else:
        try:
            data = json.loads(STATUS.read_text())
        except Exception:  # noqa: BLE001
            problems.append("status.json is onleesbaar")
            data = {}

    if data and not data.get("ok", False):
        problems.append(f"laatste run niet ok: {data.get('error', '?')}")

    if data:
        age = time.time() - float(data.get("last_sent_ts") or 0)
        if age > MAX_AGE_S:
            problems.append(f"laatste verzending {age / 60:.0f} min geleden "
                            f"(drempel {MAX_AGE_S / 60:.0f} min)")

    gas = bus_gas()
    if not gas:
        problems.append("geen antwoord op de bus (bridge offline of MQTT stuk)")
    elif data.get("fuel_eur") is not None:
        try:
            want = round(float(data["fuel_eur"]) * 100, 2)
            got = round(float(gas), 2)
            if abs(want - got) > 0.011:
                problems.append(f"bus wijkt af: wenst {want:.2f} ct/kWh, bus zegt {got:.2f}")
        except ValueError:
            problems.append(f"buswaarde onleesbaar: {gas!r}")

    if problems:
        print("Daikin-prijsbrug: " + "; ".join(problems))
        if data:
            print(f"  uurprijs €{data.get('price_now_eur')} "
                  f"({data.get('kind')}) omslag {data.get('omslag')} "
                  f"brandstof €{data.get('fuel_eur')} payload {data.get('payload')}")
        print("  check: systemctl status daikin-price.service; "
              "journalctl -u daikin-price.service -n 20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

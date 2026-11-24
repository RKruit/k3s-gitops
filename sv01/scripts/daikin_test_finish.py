#!/usr/bin/env python3
"""Eenmalige afronding van de Daikin-stooklijntest (deviatie -2 K).

Doet drie dingen:
1. zet de stooklijn-deviatie terug op 0 via MQTT (homey/p1p2/cmd -> Homey -> bus)
2. leest de deviatie terug om te bewijzen dat het gelukt is
3. vergelijkt het baseline- en testvenster uit VictoriaMetrics en stuurt een
   kort rapport naar Telegram

Draaien: eenmalig via systemd-run (zie de unit daikin-test-finish).
"""
import json
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

VM = "http://127.0.0.1:8428"
TZ = timezone(timedelta(hours=1))
SECRETS = "/srv/sv01-secrets/mosquitto.env"
BASE_START = "07:48"
BASE_END = "08:18"
TEST_START = "08:20"
DEV_TOPIC = "P1P2/P/P1P2MQTT/bridge0/S/0/Deviation_Heating"


def secret(key):
    out = subprocess.run(["sudo", "grep", "-oP", f"(?<={key}=).*", SECRETS],
                         capture_output=True, text=True).stdout.strip()
    return out


def at(hhmm, day=None):
    h, m = map(int, hhmm.split(":"))
    d = (day or datetime.now(TZ)).replace(hour=h, minute=m, second=0, microsecond=0)
    return int(d.timestamp())


def series(metric, start, end, step=60):
    url = f"{VM}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": metric, "start": start, "end": end, "step": step})
    try:
        res = json.loads(urllib.request.urlopen(url, timeout=20).read())
        r = res["data"]["result"]
        return [(int(t), float(v)) for t, v in r[0]["values"]] if r else []
    except Exception:  # noqa: BLE001
        return []


def stats(start, end):
    d = {}
    for m in ("daikin_lwt_setpoint_c", "daikin_lwt_c", "daikin_return_c",
              "daikin_cop_realtime", "daikin_flow_lpm", "daikin_outside_c",
              "daikin_gasboiler_active"):
        d[m] = series(m, start, end)
    def avg(rows, flt=None):
        vals = [v for _, v in rows if (flt is None or flt(v))]
        return round(sum(vals) / len(vals), 2) if vals else None
    boiler = d["daikin_gasboiler_active"]
    return {
        "setpoint": avg(d["daikin_lwt_setpoint_c"]),
        "aanvoer": avg(d["daikin_lwt_c"]),
        "retour": avg(d["daikin_return_c"]),
        "cop": avg(d["daikin_cop_realtime"], lambda v: v > 1),
        "flow": avg(d["daikin_flow_lpm"], lambda v: v > 1),
        "buiten": avg(d["daikin_outside_c"]),
        "ketel_pct": round(100 * sum(1 for _, v in boiler if v >= 0.5) / len(boiler), 0) if boiler else 0,
        "n": len(d["daikin_lwt_c"]),
    }


def main():
    hermes = secret("HERMES_MQTT_PASS")
    p1p2 = secret("P1P2_MQTT_PASS")

    # 1. deviatie terug naar 0
    subprocess.run(["mosquitto_pub", "-h", "192.168.1.10", "-u", "hermes",
                    "-P", hermes, "-t", "homey/p1p2/cmd", "-m", "E3600080000"],
                   capture_output=True, text=True, timeout=20)
    time.sleep(70)
    back = subprocess.run(["mosquitto_sub", "-h", "192.168.1.10", "-u", "P1P2",
                           "-P", p1p2, "-t", DEV_TOPIC, "-C", "1", "-W", "8"],
                          capture_output=True, text=True, timeout=25).stdout.strip()

    # 2. vensters vergelijken
    now = int(time.time())
    base = stats(at(BASE_START), at(BASE_END))
    test = stats(at(TEST_START), now)
    bal = {}
    try:
        bal = json.load(open("/var/lib/daikin-price/heat-balance.json"))
    except Exception:  # noqa: BLE001
        pass

    def line(label, key, unit=""):
        b, t = base.get(key), test.get(key)
        if b is None or t is None:
            return f"  {label:14s}  -"
        return f"  {label:14s}  {b}{unit} -> {t}{unit}"

    msg = [
        "Daikin stooklijntest afgerond (deviatie -2 K, 08:18-{}).".format(
            datetime.fromtimestamp(now, TZ).strftime("%H:%M")),
        "",
        "baseline -> test:",
        line("setpoint", "setpoint", " C"),
        line("aanvoer", "aanvoer", " C"),
        line("retour", "retour", " C"),
        line("COP", "cop"),
        line("flow", "flow", " l/min"),
        line("buiten", "buiten", " C"),
        line("ketel actief", "ketel_pct", " %"),
        "",
        f"deviatie nu: {back or '?'} (verwacht 0)",
    ]
    if bal:
        msg.append(f"warmte vandaag: pomp {bal.get('pump_kwh', 0):.1f} kWh / "
                   f"ketel {bal.get('boiler_kwh', 0):.1f} kWh")
    text = "\n".join(msg)

    token = secret("TELEGRAM_BOT_TOKEN")
    if not token:
        try:
            for ln in open("/home/rkruit/.hermes/.env"):
                if ln.startswith("TELEGRAM_BOT_TOKEN="):
                    token = ln.split("=", 1)[1].strip()
        except Exception:  # noqa: BLE001
            pass
    if token:
        data = urllib.parse.urlencode({"chat_id": "7049024558", "text": text}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                                   data=data, timeout=20).read()
        except Exception:  # noqa: BLE001
            pass
    print(text)


if __name__ == "__main__":
    main()

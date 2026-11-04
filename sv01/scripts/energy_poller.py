#!/usr/bin/env python3
# energy-poller: sv01 -> VictoriaMetrics
#
# Host-native systemd service (energy-poller.service) that polls the live
# energy sources every 10 s and pushes a Prometheus-text metric batch to
# VictoriaMetrics (compose 'victoria-metrics', 127.0.0.1:8428):
#   P1 dongle (HomeWizard) ........ http://192.168.68.173/api/v1/data
#   Zendure 2400 AC+ (zenSDK) ..... http://192.168.68.227/properties/report
#   zendure-follow controller ..... http://192.168.1.10:8891/status.json
#   Homey energy exporter ......... http://192.168.1.10:8890/energy.json
#   NextEnergy prices cache ....... /var/lib/zendure-follow/prices.json
# Sources fail independently: each cycle pushes whatever it could scrape
# (stale series simply stop updating). Price hours carry explicit epoch
# timestamps (ms) via /api/v1/import/prometheus so the hourly curve is
# stable; everything else is stored at receive time. stdlib only.
import json
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

P1_URL = "http://192.168.68.173/api/v1/data"
BATTERY_URL = "http://192.168.68.227/properties/report"
CONTROLLER_URL = "http://192.168.1.10:8891/status.json"
HOMEY_URL = "http://192.168.1.10:8890/energy.json"
PRICES_FILE = "/var/lib/zendure-follow/prices.json"
VM_PUSH_URL = "http://127.0.0.1:8428/api/v1/import/prometheus"
INTERVAL_S = 10
TZ = ZoneInfo("Europe/Amsterdam")

REGIME_ID = {"idle": 0, "hold": 1, "discharge": 2, "charge": 3}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def fetch_json(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def scrape():
    lines = []
    vals = {}

    # --- P1 meter ---
    try:
        j = fetch_json(P1_URL)
        vals["p1"] = float(j["active_power_w"])  # +import / -export
        lines += [
            f"p1_power_w {vals['p1']}",
            f"p1_import_kwh {float(j['total_power_import_kwh'])}",
            f"p1_export_kwh {float(j['total_power_export_kwh'])}",
            f"p1_tariff {float(j.get('active_tariff', 0))}",
            f"p1_gas_m3 {float(j.get('total_gas_m3', 0))}",
        ]
    except Exception as e:
        log(f"p1: {e}")

    # --- Zendure battery (local zenSDK; charge = AC-in, discharge = to home) ---
    try:
        props = fetch_json(BATTERY_URL).get("properties", {})
        pairs = [
            ("battery_soc_pct", "electricLevel"),
            ("battery_charge_w", "gridInputPower"),
            ("battery_discharge_w", "outputHomePower"),
            ("battery_pack_discharge_w", "packInputPower"),
            ("battery_solar_input_w", "solarInputPower"),
            ("battery_output_limit_w", "outputLimit"),
            ("battery_input_limit_w", "inputLimit"),
        ]
        for metric, key in pairs:
            if key in props:
                v = float(props[key])
                lines.append(f"{metric} {v}")
                vals[key] = v
        for i, pack in enumerate(props.get("packData", [])[:2]):
            if "socLevel" in pack:
                lines.append(f"battery_pack{i + 1}_soc_pct {float(pack['socLevel'])}")
    except Exception as e:
        log(f"battery: {e}")

    # --- zendure-follow controller state ---
    try:
        j = fetch_json(CONTROLLER_URL)
        lines += [
            f"controller_regime {REGIME_ID.get(j.get('regime'), -1)}",
            f"controller_price_now_eur {float(j.get('price_now', 0))}",
            f"controller_charge_threshold_eur {float(j.get('price_charge_thr', 0))}",
            f"controller_discharge_threshold_eur {float(j.get('price_discharge_thr', 0))}",
            f"controller_error {1 if j.get('last_error') else 0}",
            f"controller_writes {float(j.get('writes', 0))}",  # cumulative counter
        ]
    except Exception as e:
        log(f"controller: {e}")

    # --- Homey (SolarEdge PV via the hub) ---
    pv = None
    try:
        j = fetch_json(HOMEY_URL)
        pv = float(j.get("generated_w", 0))
        lines.append(f"pv_power_w {pv}")
    except Exception as e:
        log(f"homey: {e}")

    # --- derived house load: grid = house + charge - pv - discharge ---
    if pv is not None and "p1" in vals and "gridInputPower" in vals:
        charge = vals["gridInputPower"]
        discharge = vals.get("outputHomePower", 0.0)
        house = vals["p1"] + pv + discharge - charge
        lines.append(f"house_load_w {round(house, 1)}")

    # --- NextEnergy hourly prices (hour-start epoch stamps; idempotent re-push) ---
    try:
        with open(PRICES_FILE) as f:
            pr = json.load(f)
        day, hourly = pr.get("date"), pr.get("hourly", {})
        if day and hourly:
            base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ).timestamp()
            for hstr, price in hourly.items():
                h = int(hstr)
                ts_ms = int((base + h * 3600) * 1000)
                lines.append(f'nextenergy_price_eur{{day="{day}",hour="{h:02d}"}} {float(price)} {ts_ms}')
            cur = datetime.now(TZ).hour
            if str(cur) in hourly:
                lines.append(f"nextenergy_price_current_eur {float(hourly[str(cur)])}")
        if pr.get("fetched"):
            lines.append(f"nextenergy_prices_age_s {int(time.time() - float(pr['fetched']))}")
    except Exception as e:
        log(f"prices: {e}")

    return lines


def push(lines):
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(
        VM_PUSH_URL, data=body, method="POST",
        headers={"Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read()


def main():
    log("energy-poller start")
    while True:
        t0 = time.time()
        try:
            lines = scrape()
            push(lines)
            push([f"energy_poller_last_push_ts {int(t0)}"])
        except Exception as e:
            log(f"cycle failed: {e}")
        time.sleep(max(1, INTERVAL_S - (time.time() - t0)))


if __name__ == "__main__":
    main()
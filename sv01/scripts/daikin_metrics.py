#!/usr/bin/env python3
"""P1P2MQTT-bus (Daikin warmtepomp) -> VictoriaMetrics.

Leest de MQTT-topics die de P1P2-bridge publiceert en pusht een vaste set
metrieken (prefix `daikin_`) elke 10 s naar VictoriaMetrics, zodat Grafana
er grafieken van kan tekenen. De bridge publiceert alleen bij wijziging; deze
collector houdt de laatst geziene waarde vast en herhaalt hem elke tick
(receive-time series, net als de andere pollers op sv01).

Daarnaast leest hij /var/lib/daikin-price/status.json (de prijsbrug) en pusht
de brandstofprijs, het actieve stroomtarief en de gekozen omslag mee, zodat de
grafieken prijs en gedrag naast elkaar kunnen zetten.

stdlib only. Start via systemd (daikin-metrics.service), nooit handmatig als
losse run — het is een forever loop.
"""
import json
import subprocess
import threading
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BROKER = "192.168.1.10"
PORT = "1883"
USER = "hermes"
SECRETS = "/srv/sv01-secrets/mosquitto.env"
TOPIC = "P1P2/P/P1P2MQTT/bridge0/#"
VM_PUSH_URL = "http://127.0.0.1:8428/api/v1/import/prometheus"
PRICE_STATUS = "/var/lib/daikin-price/status.json"
STATE_CACHE = "/var/lib/daikin-price/metrics-cache.json"
HEAT_STATE = "/var/lib/daikin-price/heat-balance.json"
INTERVAL_S = 10
# soortelijke warmte water: 4,186 kJ/kg.K -> kW = 0,06977 * (l/min) * dT
HEAT_K = 4.186 / 60.0

# topic-achtervoegsel -> (metrieknaam, schaal)
MAP = {
    "T/1/Temperature_Outside": ("daikin_outside_c", 1),
    "T/1/Temperature_Outside_Unit": ("daikin_outside_unit_c", 1),
    "T/1/Temperature_R2T_Leaving_Water": ("daikin_lwt_c", 1),
    "T/1/Temperature_R4T_Return_Water": ("daikin_return_c", 1),
    "T/1/Temperature_R1T_HP2Gas_Water": ("daikin_hp2gas_c", 1),
    "T/1/Temperature_Room": ("daikin_room_c", 1),
    "T/0/Temperature_Room_Wall": ("daikin_room_wall_c", 1),
    "S/1/LWT_Setpoint": ("daikin_lwt_setpoint_c", 1),
    "S/1/LWT_Setpoint_Add": ("daikin_lwt_setpoint_add_c", 1),
    "S/1/Gasboiler_Active": ("daikin_gasboiler_active", 1),
    "S/1/Preferential_Mode": ("daikin_preferential_mode", 1),
    "S/1/Defrost_Active": ("daikin_defrost_active", 1),
    "S/1/DHW": ("daikin_dhw", 1),
    "S/1/DHW_Demand": ("daikin_dhw_demand", 1),
    "S/1/Valve_DHW_Tank": ("daikin_valve_dhw", 1),
    "S/1/Valve_Zone_Main": ("daikin_valve_main", 1),
    "S/1/Valve_Zone_Add": ("daikin_valve_add", 1),
    "S/0/DHW_Boost": ("daikin_dhw_boost", 1),
    "S/0/DHW_Related_Q": ("daikin_dhw_related_q", 1),
    "S/0/Quiet_Mode_Active": ("daikin_quiet_active", 1),
    "S/0/Quiet_Level": ("daikin_quiet_level", 1),
    "S/0/DHW_Setpoint": ("daikin_dhw_setpoint_c", 1),
    "S/0/Altherma_On": ("daikin_altherma_on", 1),
    "C/1/Electricity_Consumed_Total": ("daikin_elec_counter", 1),
    "C/1/Electricity_Consumed_Compressor_Heating": ("daikin_elec_counter_heating", 1),
    "C/9/Consumption_Heatpump": ("daikin_consumption_heatpump", 1),
    "C/9/COP_Lifetime": ("daikin_cop_lifetime", 1),
    "C/9/COP_Realtime": ("daikin_cop_realtime", 1),
    "C/9/Production_Gasboiler": ("daikin_production_gasboiler", 1),
    "C/1/Energy_Produced_Compressor_Heatpump": ("daikin_production_heatpump", 1),
    "C/1/Energy_Produced_Compressor_Heating": ("daikin_production_heating", 1),
    "C/1/Hours_Compressor_Heating": ("daikin_hours_compressor", 1),
    "M/1/Flow": ("daikin_flow_lpm", 1),
    "M/1/ErrorCode1": ("daikin_error_code", 1),
}


def creds():
    with open(SECRETS) as fh:
        for line in fh:
            if line.startswith("HERMES_MQTT_PASS="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("HERMES_MQTT_PASS ontbreekt")


def num(raw):
    """Hex (0x..) of decimaal naar float; None als het geen getal is."""
    raw = raw.strip()
    try:
        if raw.lower().startswith("0x"):
            return float(int(raw, 16))
        return float(raw)
    except ValueError:
        return None


def push(lines):
    if not lines:
        return
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(VM_PUSH_URL, data=body, method="POST",
                                headers={"Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"{datetime.now():%H:%M:%S} VM push mislukt: {exc}", flush=True)


def price_lines():
    try:
        with open(PRICE_STATUS) as fh:
            st = json.load(fh)
    except Exception:  # noqa: BLE001
        return []
    if not st.get("ok"):
        return []
    out = []
    for key, metric in (("fuel_eur", "daikin_fuel_price_eur"),
                        ("electricity_eur", "daikin_el_tariff_eur"),
                        ("omslag", "daikin_omslag_target"),
                        ("price_now_eur", "daikin_hour_price_eur")):
        val = st.get(key)
        if isinstance(val, (int, float)):
            out.append(f"{metric} {val}")
    return out


def heat_balance(latest, state):
    """Integreert flow x dT tot kWh en splitst naar pomp of ketel.

    Benadering: zolang de ketel actief is wordt de hele afgifte aan gas
    toegerekend, anders aan de pomp. Bij gelijktijdig bedrijf is dat een
    bovengrens voor het gasaandeel (de pomp draait dan immers ook mee).
    """
    now = time.time()
    flow = latest.get("daikin_flow_lpm") or 0.0
    lwt = latest.get("daikin_lwt_c")
    ret = latest.get("daikin_return_c")
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state.update({"date": today, "pump_kwh": 0.0, "boiler_kwh": 0.0})

    kw = 0.0
    if lwt is not None and ret is not None and flow > 1.0 and lwt > ret:
        kw = HEAT_K * flow * (lwt - ret)

    last = state.get("last_ts") or now
    dt = min(max(now - last, 0.0), 120.0)
    kwh = kw * dt / 3600.0
    if kwh > 0:
        if (latest.get("daikin_gasboiler_active") or 0) >= 0.5:
            state["boiler_kwh"] = state.get("boiler_kwh", 0.0) + kwh
        else:
            state["pump_kwh"] = state.get("pump_kwh", 0.0) + kwh
    state["last_ts"] = now
    state["heat_kw"] = round(kw, 3)
    return state


def reader(latest, stop):
    """Leest de MQTT-stream en houdt de laatst geziene waarde per metriek bij."""
    while not stop.is_set():
        proc = subprocess.Popen(
            ["mosquitto_sub", "-h", BROKER, "-p", PORT, "-u", USER, "-P", creds(),
             "-t", TOPIC, "-v", "-q", "1"],
            stdout=subprocess.PIPE, text=True, bufsize=1,
        )
        print(f"{datetime.now():%H:%M:%S} collectie gestart op {TOPIC}", flush=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                topic, _, payload = line.rstrip("\n").partition(" ")
                suffix = topic.split("/bridge0/", 1)[1]
            except IndexError:
                continue
            if suffix in MAP:
                val = num(payload)
                if val is not None:
                    latest[MAP[suffix][0]] = val
            if stop.is_set():
                break
        print("mosquitto_sub gestopt — herstart over 5 s", flush=True)
        stop.wait(5)


def main():
    latest = {}
    cache = Path(STATE_CACHE)
    if cache.exists():
        try:
            latest = {k: float(v) for k, v in json.loads(cache.read_text()).items()}
            print(f"{datetime.now():%H:%M:%S} {len(latest)} waarden uit cache geladen", flush=True)
        except Exception:  # noqa: BLE001
            latest = {}

    stop = threading.Event()
    th = threading.Thread(target=reader, args=(latest, stop), daemon=True)
    th.start()

    while True:
        time.sleep(INTERVAL_S)
        lines = [f"{name} {val}" for name, val in latest.items()]
        lwt = latest.get("daikin_lwt_c")
        ret = latest.get("daikin_return_c")
        if lwt is not None and ret is not None:
            lines.append(f"daikin_delta_t {round(lwt - ret, 3)}")
        try:
            hb = json.loads(Path(HEAT_STATE).read_text()) if Path(HEAT_STATE).exists() else {}
        except Exception:  # noqa: BLE001
            hb = {}
        hb = heat_balance(latest, hb)
        lines.append(f"daikin_heat_kw {hb['heat_kw']}")
        lines.append(f"daikin_heat_pump_kwh_today {round(hb.get('pump_kwh', 0.0), 4)}")
        lines.append(f"daikin_heat_boiler_kwh_today {round(hb.get('boiler_kwh', 0.0), 4)}")
        total = (hb.get('pump_kwh', 0.0) or 0) + (hb.get('boiler_kwh', 0.0) or 0)
        lines.append(f"daikin_heat_boiler_share_pct {round(100.0 * (hb.get('boiler_kwh', 0.0) or 0) / total, 2) if total > 0.05 else 0}")
        lines.extend(price_lines())
        push(lines)
        try:
            Path(HEAT_STATE).write_text(json.dumps(hb))
        except Exception:  # noqa: BLE001
            pass
        try:
            cache.write_text(json.dumps(latest))
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)

#!/usr/bin/env python3
# energy-poller: sv01 -> VictoriaMetrics
#
# Host-native systemd service (energy-poller.service) that polls the live
# energy sources every 10 s and pushes a Prometheus-text metric batch to
# VictoriaMetrics (compose 'victoria-metrics', 127.0.0.1:8428):
#   P1 dongle (HomeWizard) ........ http://192.168.68.221/api/v1/data (.221 sinds 08-11-2026)
#   Zendure 2400 AC+ (zenSDK) ..... http://192.168.68.229/properties/report (.229 sinds 08-11-2026)
#   zendure-follow controller ..... http://192.168.1.10:8891/status.json
#   Homey energy exporter ......... http://192.168.1.10:8890/energy.json
#   NextEnergy prices cache ....... /var/lib/zendure-follow/prices.json
#                                   (+ prices-tomorrow.json once the app
#                                    publishes the next day, ±16:00)
# Sources fail independently: each cycle pushes whatever it could scrape
# (stale series simply stop updating). Price hours carry explicit epoch
# timestamps (ms) via /api/v1/import/prometheus so the hourly curve is
# stable; everything else is stored at receive time. stdlib only.
#
# v2 (2026-11-04): savings bookkeeping — cumulative kWh/€ counters for the
# battery and PV, persisted across restarts in
# /var/lib/energy-poller/state.json (systemd StateDirectory). kWh are
# integrated from the 10 s power samples (gaps > 40 s are skipped, never
# extrapolated); € are booked at the hourly NextEnergy price valid at the
# moment of charging/delivery (stale prices -> kWh keeps running, € pauses).
#
# v3 (2026-11-04): electricity-cost books — purchase_eur / export_eur priced
# from the P1's own cumulative kWh counter deltas (exact, gap-immune, no
# integration undercount); the Live dashboard shows today's hourly cost from
# midnight on ('Stroomkosten (vandaag)').
#
# v4 (2026-11-10): NextEnergy gas rate (nextenergy_gas_price_eur, from the
# controller's app price response) + Open-Meteo hourly temperature forecast
# (forecast_temp_c, hour-anchored like the hourly prices; vaste locatie
# Lelystad Airport) for the Verwarming dashboard (gas-tegen-warmtepomp per uur).
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

P1_URL = "http://192.168.68.221/api/v1/data"
BATTERY_URL = "http://192.168.68.229/properties/report"
CONTROLLER_URL = "http://192.168.1.10:8891/status.json"
HOMEY_URL = "http://192.168.1.10:8890/energy.json"
PRICES_FILE = "/var/lib/zendure-follow/prices.json"

# dedupe-note for the homey stale-gate (log on transition or ≥5 min, not per cycle)
HOMEY_NOTE = ""
HOMEY_NOTE_TS = 0.0
TOMORROW_FILE = "/var/lib/zendure-follow/prices-tomorrow.json"
VM_PUSH_URL = "http://127.0.0.1:8428/api/v1/import/prometheus"
VM_DELETE_URL = "http://127.0.0.1:8428/api/v1/admin/tsdb/delete_series"
INTERVAL_S = 10
TZ = ZoneInfo("Europe/Amsterdam")

# bookkeeping
STATE_DIR = "/var/lib/energy-poller"
STATE_PATH = os.path.join(STATE_DIR, "state.json")
WEATHER_FILE = os.path.join(STATE_DIR, "weather.json")
WEATHER_REFRESH_S = 30 * 60      # hourly forecast refetch, at most every 30 min
WEATHER_RETRY_S = 10 * 60        # after a failed fetch, back off 10 min
# vaste locatie: Lelystad Airport / KNMI-obs EHLE (Robert, 10-11-2026)
WEATHER_LAT = "52.4608"
WEATHER_LON = "5.5297"
MAX_GAP_S = 40            # beyond this, skip power integration (never extrapolate)
PRICE_STALE_S = 90 * 60   # older price cache -> kWh keeps running, € pauses
CAPACITY_KWH = 5.28       # nameplate for the cycles metric (see docs)
METER_JUMP_KWH = 100      # counter delta above this = reset/glitch -> skip that delta

REGIME_ID = {"idle": 0, "hold": 1, "discharge": 2, "charge": 3}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def fetch_json(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def vm_delete_series(match):
    """VictoriaMetrics whole-series delete (VM cannot delete a single time
    window). Returns True on success. A marked deletion makes VM DROP samples
    re-imported for the same series name until the deletion is processed —
    callers re-anchor right away (next cycle is idempotent either way)."""
    try:
        q = urllib.parse.urlencode({"match[]": match})
        with urllib.request.urlopen(f"{VM_DELETE_URL}?{q}", timeout=5) as r:
            r.read()
        return True
    except Exception as e:
        log(f"vm delete {match}: {e}")
        return False


WEATHER_PUSHED_GEN = 0.0   # weather cache generation (fetched ts) already anchored
GAS_PUSHED_EUR = None      # gastarief generation already anchored


_NUM_KEYS = ("charge_kwh", "discharge_kwh", "pv_kwh", "charge_eur", "discharge_eur",
             "pv_eur", "purchase_eur", "export_eur",
             "last_p1_import_kwh", "last_p1_export_kwh")


def load_state():
    """Tolerant loader: unknown/missing keys default to 0 without wiping
    existing books (schema evolves across poller versions)."""
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
        for k in _NUM_KEYS:
            st.setdefault(k, 0.0)
        for k in ("ts", "since"):
            if k not in st:
                raise ValueError(f"missing {k}")
        return st
    except Exception as e:
        now = time.time()
        log(f"state: fresh books ({type(e).__name__}: {e})")
        st = {k: 0.0 for k in _NUM_KEYS}
        st.update({"ts": now, "since": now})
        return st


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def hour_price(now):
    """All-in €/kWh valid right now from the controller's price cache.

    None when the cache is for another day or stale (>90 min) — the € books
    pause rather than price at the wrong hour."""
    try:
        with open(PRICES_FILE) as f:
            pr = json.load(f)
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        if pr.get("date") != today:
            return None
        if now - float(pr.get("fetched", 0)) > PRICE_STALE_S:
            return None
        hourly = pr.get("hourly", {})
        return float(hourly[str(datetime.now(TZ).hour)])
    except Exception:
        return None


WEATHER_NOTE = ""
WEATHER_FAIL_TS = 0.0


def refresh_weather():
    """Hourly temperature forecast for TODAY (Open-Meteo), fixed location
    Lelystad Airport (Robert, 10-11-2026 — dichtst bij de KNMI-obs EHLE i.p.v.
    de ipinfo-FTTH-locatie). Cached in the
    StateDirectory, refreshed at most every WEATHER_REFRESH_S; returns the
    cache dict or {} on failure. Weather failing is independent: the rest of
    the batch keeps pushing."""
    global WEATHER_NOTE, WEATHER_FAIL_TS
    now = time.time()
    cache = {}
    try:
        with open(WEATHER_FILE) as f:
            cache = json.load(f)
    except Exception:
        pass
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if (cache.get("date") == today and cache.get("hourly")
            and now - float(cache.get("fetched", 0)) < WEATHER_REFRESH_S):
        return cache
    # failed fetches back off; until then re-serve today's (stale) cache
    if now - WEATHER_FAIL_TS < WEATHER_RETRY_S:
        return cache if cache.get("date") == today and cache.get("hourly") else {}
    lat, lon = WEATHER_LAT, WEATHER_LON
    try:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}"
               f"&longitude={lon}&hourly=temperature_2m"
               f"&timezone=Europe%2FAmsterdam&forecast_days=2")
        meteo = (fetch_json(url, timeout=10) or {}).get("hourly") or {}
        times, temps = meteo.get("time") or [], meteo.get("temperature_2m") or []
        hourly = {}
        for hiso, t in zip(times, temps):
            if t is None or not str(hiso).startswith(today):
                continue
            hourly[int(hiso[11:13])] = round(float(t), 1)
        if len(hourly) < 24:
            raise ValueError(f"open-meteo: {len(hourly)}/24 uur voor {today}")
        cache = {"date": today, "fetched": now, "lat": lat, "lon": lon,
                 "hourly": {str(h): v for h, v in sorted(hourly.items())}}
        tmp = WEATHER_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, WEATHER_FILE)
        if WEATHER_NOTE:
            log("weather: recovered")
        WEATHER_NOTE, WEATHER_FAIL_TS = "", 0.0
        return cache
    except Exception as e:
        note = f"weather: {e}"
        if note != WEATHER_NOTE:
            WEATHER_NOTE = note
            log(note)
        WEATHER_FAIL_TS = now
        return cache if cache.get("date") == today and cache.get("hourly") else {}


def scrape():
    global HOMEY_NOTE, HOMEY_NOTE_TS, GAS_PUSHED_EUR, WEATHER_PUSHED_GEN
    lines = []
    vals = {}

    # --- P1 meter ---
    try:
        j = fetch_json(P1_URL)
        vals["p1"] = float(j["active_power_w"])  # +import / -export
        vals["imp_kwh"] = float(j["total_power_import_kwh"])
        vals["exp_kwh"] = float(j["total_power_export_kwh"])
        lines += [
            f"p1_power_w {vals['p1']}",
            f"p1_import_kwh {vals['imp_kwh']}",
            f"p1_export_kwh {vals['exp_kwh']}",
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
        if j.get("error") or j.get("stale"):
            # exporter lost its hub session and is serving a frozen snapshot:
            # do NOT push it — a frozen PV value (520 W all night) lies worse
            # than a gap; also keeps the kWh/€ books clean
            raise RuntimeError("homey exporter stale: %s" % (j.get("error") or "?"))
        pv = float(j.get("generated_w", 0))
        lines.append(f"pv_power_w {pv}")
        HOMEY_NOTE = ""
    except Exception as e:
        # dedupe: stale-gate note would repeat every 10 s cycle otherwise
        note = f"homey: {e}"
        if note != HOMEY_NOTE or time.time() - HOMEY_NOTE_TS > 300:
            HOMEY_NOTE = note
            HOMEY_NOTE_TS = time.time()
            log(note)

    # --- derived house load: grid = house + charge - pv - discharge ---
    if pv is not None and "p1" in vals and "gridInputPower" in vals:
        house = vals["p1"] + pv + vals.get("outputHomePower", 0.0) - vals["gridInputPower"]
        lines.append(f"house_load_w {round(house, 1)}")

    # --- NextEnergy hourly prices (hour-start epoch stamps; idempotent re-push) ---
    try:
        with open(PRICES_FILE) as f:
            pr = json.load(f)
        day, hourly = pr.get("date"), pr.get("hourly", {})
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        if day and hourly:
            base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ).timestamp()
            for hstr, price in hourly.items():
                h = int(hstr)
                ts_ms = int((base + h * 3600) * 1000)
                lines.append(f'nextenergy_price_eur{{day="{day}",hour="{h:02d}"}} {float(price)} {ts_ms}')
            cur = datetime.now(TZ).hour
            if str(cur) in hourly:
                lines.append(f"nextenergy_price_current_eur {float(hourly[str(cur)])}")
            # label-based full-day price table (bars panel): every hour of today
            # as a receive-time gauge, so future hours (e.g. 23:00) are visible
            # before they start — no future timestamps needed.
            # hour = BARE hour label per Robert ("1" for 01:00); aa_order = the
            # zero-padded twin that keeps the series order chronological (VM
            # sorts by label-name then value: aa_order < hour → padded wins the
            # sort; tested empirically 04-11-2026 — bare-only labels jumble).
            # CURRENT hour: exported with the constant label hour="NU" so the
            # bargauge can highlight it with a STATIC byName override (blue).
            # aa_order keeps it in its slot; the old hour-numbered series for
            # that hour is deliberately not pushed, so bars stay 24 total.
            for hstr, price in hourly.items():
                h = int(hstr)
                lines.append(f'nextenergy_price_hour{{aa_order="{h:02d}",hour="{h}"}} {float(price)}')
            # '◄ nu' marker for the bars panel: STABLE label-set (aa_order="24"
            # sorts after "23", hour="NU" never rotates), pushed every tick, so
            # the panel can select it with aa_order="24" without resurrecting
            # the day's dead label-sets. 13-11-2026: the old rotating
            # aa_order="NN"/hour="NU" row birthed ghost '◄ nu' bars under the
            # whole-day range; the numbered row of the current hour is now
            # pushed too, so all 24 hour-bars stay dense.
            cur_h = datetime.now(TZ).hour
            if str(cur_h) in hourly:
                lines.append(f'nextenergy_price_hour{{aa_order="24",hour="NU"}} {float(hourly[str(cur_h)])}')
        if pr.get("fetched"):
            lines.append(f"nextenergy_prices_age_s {int(time.time() - float(pr['fetched']))}")
        # persoonlijk all-in gastarief (€/m³, dag-based) — the controller
        # copies it from the app's price response. Receive-time gauge PLUS
        # 24 hour-anchored anchors (same value, same ts pattern as
        # forecast_temp_c): the anchors make last_over_time(...[1h]) dense
        # over the whole Verwarming chart-day (receive-time samples alone
        # only start at the first push, which leaves holes in past slots).
        # GOTCHA: at a duplicate timestamp VM's last_over_time resolves the
        # tie to the OLDEST sample — a changed value would freeze the old one
        # in the chart forever. So on ANY value change the whole series is
        # deleted (VM can't delete a window, boeken-repair pattern) and
        # re-anchored from scratch in this same push.
        if pr.get("gas_eur") is not None and day == today_str:
            gas_eur = float(pr["gas_eur"])
            if gas_eur != GAS_PUSHED_EUR and vm_delete_series("nextenergy_gas_price_eur"):
                log(f"gas rate anchored fresh: {gas_eur}")
                GAS_PUSHED_EUR = gas_eur
            lines.append(f"nextenergy_gas_price_eur {gas_eur}")
            gbase = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ).timestamp()
            for h in range(24):
                lines.append(f"nextenergy_gas_price_eur {gas_eur} {int((gbase + h * 3600) * 1000)}")
    except Exception as e:
        log(f"prices: {e}")

    # --- NextEnergy TOMORROW (clock hours; Grafana "morgen" bars panel) ---
    # The controller caches tomorrow's published price set from the app-API
    # (appears daily ±16:00). Same label scheme as the today bars; pushed only
    # while the file's date is really tomorrow, so a yesterday-evening cache
    # can never surface on the wrong day (before publication: empty panel).
    try:
        with open(TOMORROW_FILE) as f:
            pr = json.load(f)
        lines.append(f"nextenergy_prices_tomorrow_age_s "
                     f"{int(time.time() - float(pr.get('fetched', 0)))}")
        tom = datetime.now(TZ).date() + timedelta(days=1)
        hourly_t = pr.get("hourly", {})
        if pr.get("date") == tom.isoformat() and hourly_t:
            for hstr, price in hourly_t.items():
                h = int(hstr)
                lines.append(
                    f'nextenergy_price_tomorrow_hour{{aa_order="{h:02d}",hour="{h}"}} '
                    f"{float(price)}")
    except Exception as e:
        log(f"tomorrow: {e}")

    # --- Temperatuurvoorspelling (Open-Meteo; hour-anchored like the prices) ---
    # Same duplicate-tie gotcha as the gas rate: VM keeps old+new samples at
    # the same hour-anchor and window functions resolve ties to the OLDEST —
    # so on a fresh forecast generation the whole series is deleted first,
    # then re-anchored with the new values.
    w = refresh_weather()
    if w:
        try:
            gen = float(w["fetched"])
            if gen != WEATHER_PUSHED_GEN and vm_delete_series("forecast_temp_c"):
                log("forecast re-anchored")
                WEATHER_PUSHED_GEN = gen
            base = datetime.strptime(w["date"], "%Y-%m-%d").replace(tzinfo=TZ).timestamp()
            for hstr, temp in sorted(w["hourly"].items(), key=lambda kv: int(kv[0])):
                ts_ms = int((base + int(hstr) * 3600) * 1000)
                lines.append(f"forecast_temp_c {float(temp)} {ts_ms}")
            lines.append(f"forecast_weather_age_s "
                         f"{int(time.time() - float(w['fetched']))}")
        except Exception as e:
            log(f"weather push: {e}")

    return lines, vals, pv


def push(lines):
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(
        VM_PUSH_URL, data=body, method="POST",
        headers={"Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read()


def bookkeep(st, vals, pv, price, now):
    """Integrate the last interval into the cumulative books.

    Battery/PV kWh come from power integration: a gap > MAX_GAP_S (poller
    restart, host down) skips the interval — kWh and € are never extrapolated,
    only undercounted. Battery AC-port flows are counted as-is (charged at the
    hour's price); DC panel input into the Zendure bypasses the meter and
    enters the books as free energy. PV counter = SolarEdge via Homey only
    (Zendure panel input is accounted inside the battery books as free charge).

    Purchase/export € come from the P1's own cumulative kWh counters — exact,
    gap-immune deltas priced at the hour's all-in rate (counter resets/glitches
    are skipped, never booked)."""
    # --- electricity-cost books (meter deltas; exact, runs even across gaps) ---
    if price is not None and "imp_kwh" in vals:
        dp = vals["imp_kwh"] - st.get("last_p1_import_kwh", vals["imp_kwh"])
        if 0 <= dp < METER_JUMP_KWH:
            st["purchase_eur"] += dp * price
    if price is not None and "exp_kwh" in vals:
        de = vals["exp_kwh"] - st.get("last_p1_export_kwh", vals["exp_kwh"])
        if 0 <= de < METER_JUMP_KWH:
            st["export_eur"] += de * price
    if "imp_kwh" in vals:
        st["last_p1_import_kwh"] = vals["imp_kwh"]
    if "exp_kwh" in vals:
        st["last_p1_export_kwh"] = vals["exp_kwh"]

    # --- battery/PV books (power integration, gap-gated) ---
    dt = now - st["ts"]
    st["ts"] = now
    if dt <= 0 or dt > MAX_GAP_S:
        if dt > MAX_GAP_S:
            log(f"books: skipped {dt:.0f} s gap (no extrapolation)")
        return
    if "gridInputPower" in vals:
        d = vals["gridInputPower"] * dt / 3.6e6
        st["charge_kwh"] += d
        if price is not None:
            st["charge_eur"] += d * price
    if "outputHomePower" in vals:
        d = vals["outputHomePower"] * dt / 3.6e6
        st["discharge_kwh"] += d
        if price is not None:
            st["discharge_eur"] += d * price
    if pv is not None:
        d = pv * dt / 3.6e6
        st["pv_kwh"] += d
        if price is not None:
            # PV value under saldering: every generated kWh offsets a bought kWh
            st["pv_eur"] += d * price


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    st = load_state()
    log(f"energy-poller start; books since {datetime.fromtimestamp(st['since']):%Y-%m-%d %H:%M}")
    while True:
        t0 = time.time()
        try:
            lines, vals, pv = scrape()
            bookkeep(st, vals, pv, hour_price(t0), t0)
            lines += [
                f"battery_charge_kwh {st['charge_kwh']:.4f}",
                f"battery_discharge_kwh {st['discharge_kwh']:.4f}",
                f"pv_kwh {st['pv_kwh']:.4f}",
                f"battery_charge_eur {st['charge_eur']:.4f}",
                f"battery_discharge_eur {st['discharge_eur']:.4f}",
                f"pv_eur {st['pv_eur']:.4f}",
                f"purchase_eur {st['purchase_eur']:.4f}",
                f"export_eur {st['export_eur']:.4f}",
                f"energy_books_since_ts {int(st['since'])}",
                f"energy_poller_last_push_ts {int(t0)}",
            ]
            push(lines)
            save_state(st)
        except Exception as e:
            log(f"cycle failed: {e}")
        time.sleep(max(1, INTERVAL_S - (time.time() - t0)))


if __name__ == "__main__":
    main()
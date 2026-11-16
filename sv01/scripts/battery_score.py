#!/usr/bin/env python3
"""Scorecard: app-sturing (ZENKI/TOU) vs. het prijsdrempel-plan (zendure_follow-wet).

Per afgeronde kalenderdag, vanaf de boeken-start (2026-11-04):
  WERKELIJK  - exact uit de poller-boeken (VictoriaMetrics counters):
               charge_eur/-kwh en discharge_eur/-kwh, geboekt op de echte
               uurtarieven (AC-kant, inefficiency zit automatisch mee).
  REFERENTIE - wat de wet op DIE dag zou hebben gedaan: P30/P70 over de
               dagprijzen (zelfde quantile-formule + constanten als
               zendure_follow.py), laden 2400 W in de ≤P30-uren tot
               CHARGE_TARGET (chronologisch), ontladen zero-export
               (elk uur de gemiddelde huisbelasting, pack-gedempt) in de
               ≥P70-uren, SoC-vloer 10%, flat-day-gate 3 ct. SoC loopt
               door over de dagen heen; cap wordt gebootstrapd uit de
               boeken + gemeten SoC-sprongen.
  GEDRAG     - in welke uren er écht geladen/ontladen werd (>100 W gemiddeld).

Output: CSV-upsert /var/lib/energy-poller/score.csv (1 regel per dag) +
dag-geankerde samples in VM (battery_score_{actual,reference}_net_eur,
ts = dagmiddernacht + 30 min) + NL-scorecard op stdout. Cron 00:15;
idempotent (herrekent uitsluitend afgeronde dagen). Stdlib only,
alleen-lezen t.o.v. het apparaat.
"""
import json
import math
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request
import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Amsterdam")
VM = "http://127.0.0.1:8428"
CSV = "/var/lib/energy-poller/score.csv"
CONTROLLER_SCRIPT = "/opt/sv01-config/sv01/scripts/zendure_follow.py"
DAY0 = "2026-11-04"          # boeken-start

# fase-grenzen (kalenderdag-based; handmatig bijwerken bij een wissel)
PHASES = [
    ("2026-11-04", "eigen wet"),
    ("2026-11-12", "ZENKI"),
    ("2026-11-15", "Zenki-uit"),
    ("2026-11-16", "ZENKI-2"),   # 15-11 ~23:25: ZENKI opnieuw aan (Robert: 'nog een paar dagen testen')
    ("2026-11-17", "eigen wet 2"),  # 16-11 ~16:35: control terug (Robert: 'Zenki is niet goed'); 16-11 is een gemengde dag, telt bij ZENKI-2
]

# fallback-physics: cap wordt gebootstrapd uit de boeken; eff vast.
CAP_DEFAULT = 5.76     # kWh pack-intern (2 packs)
ETA_C = 0.92           # AC-in -> pack
ETA_D = 0.93           # pack -> AC-out
FLOOR_PCT = 10.0       # device-minSoc


def q_range(expr, start_s, end_s, step_s):
    url = (f"{VM}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": expr, "start": str(int(start_s)), "end": str(int(end_s)),
         "step": str(int(step_s))}))
    with urllib.request.urlopen(url, timeout=25) as r:
        d = json.loads(r.read().decode())
    return d.get("data", {}).get("result", [])


def midnight(day):
    return datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ).timestamp()


def consts():
    """Quantile-frac + doel + fysica uit de LIJVE wet (in-sync-regel)."""
    try:
        src = open(CONTROLLER_SCRIPT).read()
        c = re.search(r"^CHARGE_PCT\s*=\s*([\d.]+)", src, re.M)
        d = re.search(r"^DISCHARGE_PCT\s*=\s*([\d.]+)", src, re.M)
        t = re.search(r"^CHARGE_TARGET\s*=\s*(\d+)", src, re.M)
        s = re.search(r"^MIN_SPREAD\s*=\s*([\d.]+)", src, re.M)
        p = re.search(r"^PACK_KWH\s*=\s*([\d.]+)", src, re.M)
        e = re.search(r"^CHARGE_EFF\s*=\s*([\d.]+)", src, re.M)
        if c and d:
            return (float(c.group(1)), float(d.group(1)),
                    int(t.group(1)) if t else 90,
                    float(s.group(1)) if s else 0.03,
                    float(p.group(1)) if p else 5.46,
                    float(e.group(1)) if e else 0.92)
    except OSError:
        pass
    return 0.30, 0.70, 90, 0.03, 5.46, 0.92


def quantile(vals, frac):
    n = len(vals)
    q = min(max(int(round(frac * n)), 1), n) - 1
    return vals[q]


def hourly_means(metric, day, t_base):
    """Uurgemiddelden 0..23 van een 10-s reeks ([1h]-vensters op :30-stappen)."""
    res = q_range(f"avg_over_time({metric}[1h])", t_base + 1800, t_base + 84900, 3600)
    hours = {}
    for series in res:
        for ts, val in series["values"]:
            h = int((ts - t_base - 1800) // 3600)
            if 0 <= h <= 23:
                hours[h] = float(val)
    return hours


def counter_at(metric, ts):
    """Laatste sample <= ts van een monotone counter."""
    res = q_range(metric, ts - 86400, ts, 300)
    vals = None
    for series in res:
        v = [(t, float(x)) for t, x in series["values"] if t <= ts]
        if v:
            vals = v
    return vals[-1][1] if vals else 0.0


def day_prices(day, t_base):
    """{hour: all-in €} van dag `day` uit de dag-geankerde prijsserie.

    NB uur-0: vóór de 15-11-2026 dp-mappingfix schreef de poller het
    {D,hour=00}-anchor met de VORIGE-dag-midnacht-waarde; de juiste waarde
    voor klok-0 van D staat in het {D+1, hour=00}-anchor (de morgen-push van
    dag D schreef daar DP[23]-van-D = klok-0 van D). Voor dagen voor de fix
    wordt uur-0 daar vandaan gehaald; na de fix is het {D,00}-anchor zelf
    de juiste waarde (de poller her-ankert bij de mappingfix)."""
    res = q_range("max(last_over_time(nextenergy_price_eur[1h]))",
                  t_base + 1800, t_base + 86400 + 1800, 3600)
    hours = {}
    for series in res:
        for ts, val in series["values"]:
            h = int((ts - t_base) // 3600)
            if h == 24:
                if day < "2026-11-16":   # {D+1, 00} = klok-0 van D (pre-fix reconstructie)
                    hours[0] = float(val)
            elif 0 <= h <= 23:
                hours[h] = float(val)
    return hours if len(hours) >= 20 else None


def soc_at(ts):
    """Laatste SoC-sample <= ts (in %)."""
    res = q_range("battery_soc_pct", ts - 3600, ts, 30)
    last = None
    for series in res:
        for t, v in series["values"]:
            if t <= ts:
                last = float(v)
    return last


def soc_hourly(day, t_base):
    """SoC op ieder uurgrens h=0..24 (h=0 = dagstart, 24 = dagmiddernacht)."""
    return [(soc_at(t_base + h * 3600)) for h in range(0, 25)]


def simulate(prices, house, soc_start_pct, charge_pct, discharge_pct,
             charge_target, min_spread, cap, rank_rule=False, cap_w=2400.0):
    """Het plan één dag naspelen (stateless per uur, SoC-walk erdoorheen).

    rank_rule=False: de pre-16-11-wet (laden bij prijs ≤ P30-quantile);
    rank_rule=True: de 15-11-2026-rangkeuze — laden in de k goedkoopste
    resterende uren die het doel van de lopende SoC nog halen (zelfde formule
    als price_regime in zendure_follow.py)."""
    vals = sorted(prices.values())
    c_thr = quantile(vals, charge_pct)
    d_thr = quantile(vals, discharge_pct)
    out = {"c_thr": c_thr, "d_thr": d_thr,
           "flat": (d_thr - c_thr) < min_spread,
           "charge_kwh": 0.0, "charge_eur": 0.0, "disc_kwh": 0.0, "disc_eur": 0.0,
           "soc_end": soc_start_pct}
    if out["flat"]:
        return out  # flat day: de wet idle de hele dag (SoC-bewaring), net = 0
    soc = soc_start_pct
    for h in range(24):
        p = prices.get(h)
        if p is None:
            continue
        charged = False
        if rank_rule:
            if soc < charge_target:
                remaining = range(h, 24)
                pph = cap_w / 1000.0 * ETA_C / cap * 100.0
                k = min(int(math.ceil(max(0.0, charge_target - soc) / pph)),
                        len(remaining))
                ordered = sorted(remaining,
                                 key=lambda x: (prices.get(x, 99.0), x))[:k]
                if h in ordered:
                    charged = True
        elif p <= c_thr and soc < charge_target:
            charged = True
        if charged:
            kwh_g = min(cap_w / 1000.0,
                        (charge_target - soc) / 100.0 * cap / ETA_C)
            out["charge_kwh"] += kwh_g
            out["charge_eur"] += kwh_g * p
            soc += kwh_g * ETA_C / cap * 100.0
        elif p >= d_thr and soc > FLOOR_PCT:
            delivered = min(house.get(h, 0.0) / 1000.0,
                            (soc - FLOOR_PCT) / 100.0 * cap * ETA_D)
            out["disc_kwh"] += delivered
            out["disc_eur"] += delivered * p
            soc -= delivered / ETA_D / cap * 100.0
    out["soc_end"] = max(soc, 0.0)
    return out


def phase_for(day):
    label = PHASES[0][1]
    for start, lab in PHASES:
        if day >= start:
            label = lab
    return label


def range_str(hours):
    if not hours:
        return "-"
    runs, s, prev = [], hours[0], hours[0]
    for h in hours[1:]:
        if h != prev + 1:
            runs.append((s, prev))
            s = h
        prev = h
    runs.append((s, prev))
    return ", ".join("%02d-%02d" % (a, (b + 1) % 24) for a, b in runs)


def main():
    c_pct, d_pct, charge_target, min_spread, pack_law, eff_law = consts()
    now_day = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
    d0 = datetime.date.fromisoformat(DAY0)
    days = []
    cursor = d0
    while cursor.isoformat() < now_day:
        days.append(cursor.isoformat())
        cursor += datetime.timedelta(days=1)
    if not days:
        print("geen afgeronde dagen sinds %s" % DAY0)
        return

    # bootstrap: capaciteit schatten uit SCHONE uurruns (een dagsegment met
    # maar één richting > 100 W en een heldere SoC-sprong; gemengde uren
    # (laden én ontladen) breken de run — zo blijft de schatting zuiver)
    est = []
    for day in days:
        tb = midnight(day)
        soc_h = soc_hourly(day, tb)
        if any(s is None for s in soc_h):
            continue
        hw = hourly_means("battery_charge_w", day, tb)
        dw = hourly_means("battery_discharge_w", day, tb)
        for direction in ("charge", "discharge"):
            run, s = [], None  # (kWh, dsoc) per open run
            best = None
            for h in range(24):
                c_kwh = max(hw.get(h, 0.0), 0.0) / 1000.0
                d_kwh = max(dw.get(h, 0.0), 0.0) / 1000.0
                pure = {  # is dit uur unidirectioneel?
                    "charge": c_kwh > 0.05 and d_kwh <= 0.05,
                    "discharge": d_kwh > 0.05 and c_kwh <= 0.05,
                }[direction]
                if pure:
                    if s is None:
                        s = h
                    run.append(h)
                else:
                    if s is not None and run:
                        best = run
                    s, run = None, []
            if s is not None and run:
                best = run
            if not best:
                continue
            energy = sum((hw if direction == "charge" else dw).get(h, 0.0)
                         for h in best) / 1000.0
            dsoc = soc_h[best[-1] + 1] - soc_h[best[0]]
            if dsoc is None:
                continue
            if direction == "charge":
                if dsoc > 1.5:
                    est.append(energy * ETA_C / (dsoc / 100.0))
            elif dsoc < -1.5 and energy > 0.4:
                est.append(energy / (ETA_D * abs(dsoc) / 100.0))
    cap = min(max(statistics.median(est), 4.5), 7.0) if est else pack_law

    soc = soc_at(midnight(days[0]) - 3600) or 50.0
    rows = {}
    for day in days:
        tb, te = midnight(day), midnight(day) + 85200
        prices = day_prices(day, tb)
        if prices is None:
            rows[day] = {"ok": False}
            continue
        chg = (counter_at("battery_charge_eur", te)
               - counter_at("battery_charge_eur", tb))
        chg_kwh = (counter_at("battery_charge_kwh", te)
                   - counter_at("battery_charge_kwh", tb))
        dis = (counter_at("battery_discharge_eur", te)
               - counter_at("battery_discharge_eur", tb))
        dis_kwh = (counter_at("battery_discharge_kwh", te)
                   - counter_at("battery_discharge_kwh", tb))
        exp_kwh = (counter_at("p1_export_kwh", te)
                   - counter_at("p1_export_kwh", tb))
        exp_eur = (counter_at("export_eur", te)
                   - counter_at("export_eur", tb))
        if not exp_kwh or exp_kwh < 0 or exp_kwh > 50:
            # 0/meter-reset/counter-start-artefact: geen zinvolle daggrens
            exp_kwh, exp_eur = 0.0, 0.0
        hw = hourly_means("battery_charge_w", day, tb)
        dw = hourly_means("battery_discharge_w", day, tb)
        charge_h = [h for h in range(24) if hw.get(h, 0) > 100]
        disc_h = [h for h in range(24) if dw.get(h, 0) > 100]
        house = hourly_means("house_load_w", day, tb)
        ref = simulate(prices, house, soc, c_pct, d_pct, charge_target,
                       min_spread, pack_law,
                       rank_rule=(day >= "2026-11-16"),
                       cap_w=(2400.0 if day >= "2026-11-15" else 800.0))
        soc = ref["soc_end"]
        rows[day] = {
            "ok": True,
            "phase": phase_for(day),
            "charge_kwh": round(chg_kwh, 2), "charge_eur": round(chg, 2),
            "disc_kwh": round(dis_kwh, 2), "disc_eur": round(dis, 2),
            "exp_kwh": round(exp_kwh, 2), "exp_eur": round(exp_eur, 2),
            "actual_net": round(dis - chg, 2),
            "ref_charge_kwh": round(ref["charge_kwh"], 2),
            "ref_charge_eur": round(ref["charge_eur"], 2),
            "ref_disc_kwh": round(ref["disc_kwh"], 2),
            "ref_disc_eur": round(ref["disc_eur"], 2),
            "ref_net": round(ref["disc_eur"] - ref["charge_eur"], 2),
            "delta": round((dis - chg) - (ref["disc_eur"] - ref["charge_eur"]), 2),
            "charge_h": charge_h, "disc_h": disc_h,
            "flat": ref["flat"],
            "c_thr": ref["c_thr"], "d_thr": ref["d_thr"],
            "cap_est": round(cap, 2),
        }

    # CSV-upsert
    os.makedirs(os.path.dirname(CSV), exist_ok=True)
    old = {}
    if os.path.exists(CSV):
        for line in open(CSV):
            try:
                r = json.loads(line)
                old[r["day"]] = r
            except Exception:
                pass
    for day, r in rows.items():
        if r.get("ok"):
            old[day] = r
    with open(CSV, "w") as f:
        for day in sorted(old):
            f.write(json.dumps(old[day], sort_keys=True) + "\n")

    # VM-push: dag-geankerde net-samples (gas-ankerpatroon)
    try:
        lines = []
        for day, r in sorted(old.items()):
            if not r.get("ok"):
                continue
            ts_ms = int((midnight(day) + 1800) * 1000)
            lines.append(f"battery_score_actual_net_eur {r['actual_net']} {ts_ms}")
            lines.append(f"battery_score_reference_net_eur {r['ref_net']} {ts_ms}")
        body = ("\n".join(lines) + "\n").encode()
        urllib.request.urlopen(urllib.request.Request(
            f"{VM}/api/v1/import/prometheus", data=body, method="POST",
            headers={"Content-Type": "text/plain"}), timeout=10).read()
    except Exception as e:
        print(f"[vm push: {e}]", file=sys.stderr)

    # rapport
    def eur(x):
        return ("€%+.2f" % x).replace(".", ",")

    print("🔋 Accu-scorecard — app-sturing vs. het prijsdrempel-plan")
    print(f"plan (nu): laden rang-keuze tot {charge_target}% @ 2400 W · ontladen ≥ P70 "
          f"({d_pct:.0%}) zero-export · vloer 10% · flat-gate {min_spread} ct "
          f"(≤ 14-11 gesimuleerd op de toenmalige P30-wet @ 800 W)")
    print(f"physisch: wet PACK_KWH {pack_law:.2f} kWh · bootstrap-cap {cap:.2f} kWh "
          f"({len(est)} segmenten)", "· η laden", ETA_C, "· η ontladen", ETA_D)
    print()
    print("dag         fase        |  werk:  laad €  ontlaad €  net |  ref:  netto   |   Δ     |  gedrag")
    for day in sorted(rows):
        r = rows[day]
        if not r.get("ok"):
            print(f"{day}              (day-prijzen niet bekend in VM — overgeslagen)")
            continue
        beh = f"laad {range_str(r['charge_h'])} · ontl {range_str(r['disc_h'])}"
        if r.get("exp_kwh", 0) > 0.2:
            beh += f" · export {r['exp_kwh']:.1f} kWh/{eur(r['exp_eur'])}"
        print(f"{day}  {r['phase']:<10} | {eur(r['charge_eur']):9}{eur(r['disc_eur']):11}"
              f"{eur(r['actual_net']):6}| {eur(r['ref_net']):7}  | {eur(r['delta']):7} | {beh}")
    print()
    print("Score per fase (Σ netto €, werk vs. plan):")
    agg = {}
    for day, r in sorted(rows.items()):
        if not r.get("ok"):
            continue
        a = agg.setdefault(r["phase"], {"act": 0.0, "ref": 0.0, "n": 0})
        a["act"] += r["actual_net"]
        a["ref"] += r["ref_net"]
        a["n"] += 1
    for ph, a in agg.items():
        print(f"  {ph:<10} werk {eur(a['act'])}  ·  ref {eur(a['ref'])}  ·  "
              f"Δ {eur(a['act'] - a['ref'])}  ({a['n']} d)")
    tou = agg.get("TOU-test")
    if tou and tou["n"]:
        print()
        print(f"TOU-test: {tou['n']} dag(en) — werk {eur(tou['act'])} vs. plan "
              f"{eur(tou['ref'])} → Δ {eur(tou['act'] - tou['ref'])}")
    print()
    print("aannames: AC-kant geboekt (inefficiency meegerekend); referentie simuleert "
          "zero-export, geen PV-overschot; eendaagse overrides niet nagespeeld; "
          "drijft op dagmiddernacht-ankers (24:00-slot leeg = bekende randgroef).")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)

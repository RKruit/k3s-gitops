---
title: Grafana
type: service
description: "Grafana + VictoriaMetrics on sv01: energy dashboards fed by a 10-second host poller (P1, Zendure, controller, Homey/SolarEdge, NextEnergy prices); provisioning in git, route behind Authelia, databases on NFS."
related: [services/homey.md, services/caddy.md, services/uptime-kuma.md, infra/nfs-storage.md, infra/secrets.md]
---

# Grafana + VictoriaMetrics — energy dashboards

Deployed 2026-11-04. History + dashboards for home energy: grid (P1), Zendure battery, SolarEdge PV (via Homey), the zendure-follow regime, and NextEnergy hourly prices.

## Architecture

```
energy-poller.service (host, every 10 s)
  ├─ P1 dongle ............ http://192.168.68.173/api/v1/data
  ├─ Zendure 2400 AC+ ..... http://192.168.68.227/properties/report
  ├─ controller status .... http://192.168.1.10:8891/status.json
  ├─ Homey exporter ....... http://192.168.1.10:8890/energy.json
  └─ prices caches ........ /var/lib/zendure-follow/prices.json
                            + prices-tomorrow.json (published daily ±16:00)
        │ push (Prometheus text, /api/v1/import/prometheus)
        ▼
victoria-metrics (container, 127.0.0.1:8428, NFS vm-data)
        ▼
grafana (container, datasource uid `victoriametrics`, NFS grafana-data)
        ▲
Caddy route grafana.oostrandpark.com (Authelia forward_auth → anonymous Viewer)
```

- The poller is a host-native systemd service (`sv01/systemd/energy-poller.service`, script `sv01/scripts/energy_poller.py`, User=rkruit, stdlib only — same pattern as `homey-energy.service`). Sources fail independently: a stale source just stops updating its series.
- Grafana has **no published port**: it is reachable only through the Caddy route. Anonymous `Viewer` inside Grafana means any logged-in (Authelia) user sees dashboards without a second login; the Grafana login (admin `rkruit`, password `GRAFANA_ADMIN_PASSWORD` in `/srv/sv01-secrets/sv01.env`) is still there for admin work.
- Data on NFS volumes `grafana-data` (sqlite) and `vm-data` (TSDB), both created 777 upfront per the NFS gotcha.

## Dashboards (provisioned in git)

| Dashboard | uid | Contents |
|---|---|---|
| Energie — Live | `energy-live` | realtime stats (grid, house, PV, SoC, charge/discharge, price), power-flow chart, SoC 24 h, price curve + controller P30/P70 thresholds (panel follows the dashboard range; panel-link "Hele dag ▸" opens the same panel midnight→24:00 incl. upcoming hours via `?from=now/d&to=now/d&viewPanel=11` — Grafana 13.2.5 ignores ALL per-panel time-end config: `panel.time{from,to}`, target `relativeTimeRange`, variables in panel time are no-ops, and legacy `panel.timeFrom` works but always pins the end to "now"; `to=now/d` rounds UP to 24:00, `from=now/d` floors to midnight), electricity cost today (midnight-anchored panel), NextEnergy hourly rates as bar gauge (hour-labeled "1…23", chronological, blue "◄ nu" bar on the current hour, incl. upcoming hours), plus the same bar gauge for TOMORROW (same hour labels, no current-hour bar; filled only once NextEnergy publishes the next day ±16:00, empty before) |
| Energie — Verloop | `energy-daily` | price curve + thresholds, rolling-24 h kWh (import/export/PV/battery), SoC, regime, gas m³ |
| Energie — Besparing | `energy-savings` | cumulative books: net battery saving, discharge value, charge cost, PV value (€ over the selected range), battery cycles + kWh |

Files: `sv01/compose/grafana/provisioning/` (datasource + provider) and `sv01/compose/grafana/dashboards/*.json`, mounted read-only. `allowUiUpdates: true` — UI edits are possible but land in Grafana's own DB, **not** git; export an edit back to git if it must survive a re-provision.

## Metrics (pushed every 10 s)

| Metric | Meaning |
|---|---|
| `p1_power_w` | grid net power, +import / −export |
| `p1_import_kwh` / `p1_export_kwh` | cumulative meter readings → use `increase()` |
| `p1_gas_m3` | cumulative gas counter |
| `p1_tariff` | 1 = dale / 2 = piek |
| `battery_soc_pct` | Zendure SoC (`electricLevel`) |
| `battery_charge_w` | charging flow at the AC port (`gridInputPower`) |
| `battery_discharge_w` | delivery to the house (`outputHomePower`) |
| `battery_pack_discharge_w` / `battery_solar_input_w` | pack discharge / panel input on the Zendure itself |
| `battery_output_limit_w` / `battery_input_limit_w` | currently written limits |
| `battery_pack{1,2}_soc_pct` | per-pack SoC |
| `controller_regime` | 0 idle · 1 hold · 2 discharge · 3 charge |
| `controller_{price_now,charge_threshold,discharge_threshold}_eur` | live price + P30/P70 thresholds |
| `controller_error` | 1 = non-empty `last_error` (recent real failure) |
| `controller_writes` | cumulative write counter → `increase()` |
| `pv_power_w` | SolarEdge inverter via Homey (`generated_w`) |
| `house_load_w` | **derived**: `p1 + pv + discharge − charge` |
| `nextenergy_price_eur{day,hour}` | hourly all-in curve, hour-anchored timestamps (today) |
| `nextenergy_price_current_eur` | this hour's price as instant gauge |
| `nextenergy_prices_age_s` | freshness of the prices cache |
| `energy_poller_last_push_ts` | poller heartbeat |
| `battery_charge_kwh` / `battery_discharge_kwh` | cumulative books: kWh into the AC port / delivered to the house (integrated from the 10 s samples, gaps skipped) |
| `pv_kwh` | cumulative books: SolarEdge generation (Homey `generated_w`) |
| `battery_charge_eur` / `battery_discharge_eur` | cumulative books: € booked at the hourly all-in price valid at the moment of charging/delivery |
| `pv_eur` | cumulative books: PV value at the hourly price (saldering assumption) |
| `purchase_eur` / `export_eur` | electricity-cost books: import/export kWh priced at the hourly all-in rate — exact P1-counter deltas, gap-immune |
| `nextenergy_price_hour{aa_order,hour}` | label-based full-day price table for the bars panel — bare `hour` = display label ("1" for 01:00), zero-padded `aa_order` = the sorting twin (VM sorts by label name then value; bare labels alone lexicographically jumble) |
| `nextenergy_price_tomorrow_hour{aa_order,hour}` | label-based *tomorrow* price table for the morgen bars panel — same scheme as the today bars, no "NU" bar (tomorrow has no current hour); pushed only while the cache's date is really tomorrow, so before the app publishes (±16:00) the series is empty, not stale |
| `nextenergy_prices_tomorrow_age_s` | freshness of the tomorrow price cache (grows until the app publishes the next day, ±16:00) |
| `energy_books_since_ts` | epoch the books started (2026-11-04) |

## Savings bookkeeping (Besparing dashboard, since 2026-11-04)

The poller keeps cumulative kWh/€ books (`/var/lib/energy-poller/state.json`, systemd `StateDirectory` — survives restarts and rebuilds). Assumptions, deliberately explicit:

1. **Discharge value** = delivered kWh × the hourly all-in price (zero-export regime: the delivered kWh replaces a purchase at that hour's price). Correct counterfactual for arbitrage under saldering.
2. **Charge cost** = kWh into the AC port × that hour's price. Round-trip losses enter automatically (charged kWh includes them). DC panel input into the Zendure bypasses the meter and is booked as free energy — shows up as saving inside the battery numbers, not the PV panel.
3. **PV value** = SolarEdge generation (via Homey) × hourly price — valid while saldering lasts; the definition changes when saldering ends (2027). Surplus-AC-charging is booked at the hour price, which *understates* battery savings slightly (that energy would have been exported ≈ same value under saldering).
4. **Wear is not included.** Rough reference if you want to subtract it: AB-series pack ≈ €433 / ~3 kWh / 6000 cycles ≈ €0.02–0.03 per delivered kWh.
5. Stale prices (> 90 min or wrong day) → kWh counters keep running, € counters pause (no wrong-hour pricing enters the books). Gaps > 40 s (restarts/downtime) are skipped, never extrapolated.

"Netto besparing (accu)" = `increase(battery_discharge_eur[$__range]) − increase(battery_charge_eur[$__range])` — cross-check any range against the NextEnergy verbruik page before trusting a policy decision to it.

Electricity-cost books (poller v3): `purchase_eur` and `export_eur` price the P1's own cumulative kWh counter deltas at the hour's all-in rate — exact, gap-immune, and reset-skip (deltas ≥ 100 kWh are treated as counter glitches and never booked). The Live dashboard's "Stroomkosten (vandaag)" panel shows both (`inkoop €/u` and the saldering-style net `inkoop − export €/u`) with its own midnight anchor (`timeFrom now/d`), independent of the dashboard's 6 h range. Saldering nets monthly, so the hourly net line is an approximation of the settlement, while the `inkoop` line is the exact gross purchase.

## Gotchas

- **VictoriaMetrics on NFS is a watch item**: VM's docs are lukewarm on network filesystems; NFS won because it's the standing data-home convention and the write rate is tiny (~5 samples/s). If VM ever logs NFS I/O errors, move `vm-data` to a local volume and back up with its snapshot API.
- `grafana` + `victoria-metrics` float on `:latest` → daily image-update timer applies upgrades (VM storage auto-migrates backward-compatibly).
- `house_load_w` is an identity-derivation, not a meter — if Homey (PV) is stale, the number is stale too.
- Price metric `nextenergy_price_eur` carries the *today* cache only; the controller refreshes it hourly (+2 min) with ONE app login followed by three day-queries (yesterday, today, tomorrow). Tomorrow's hourly prices live in their own label series (`nextenergy_price_tomorrow_hour`): the controller caches them to `prices-tomorrow.json` whenever the app already publishes the next day (±16:00) and the poller pushes them date-gated; before publication the morgen panel is simply empty (no-data is normal there, not a fault).
- SolarEdge is read **via Homey** (inverter app in Homey). Inverter-local Modbus TCP is disabled in the SetApp settings (checked 2026-11-04, no port 1502 on the IoT LAN) — enabling it would only be needed for direct 1–5 s inverter data.
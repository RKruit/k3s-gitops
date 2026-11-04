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
  └─ prices cache ......... /var/lib/zendure-follow/prices.json
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
| Energie — Live | `energy-live` | realtime stats (grid, house, PV, SoC, charge/discharge, price, regime), power-flow chart, SoC 24 h, price curve + controller thresholds |
| Energie — Verloop | `energy-daily` | price curve + thresholds, rolling-24 h kWh (import/export/PV/battery), SoC, regime, gas m³ |

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

## Gotchas

- **VictoriaMetrics on NFS is a watch item**: VM's docs are lukewarm on network filesystems; NFS won because it's the standing data-home convention and the write rate is tiny (~5 samples/s). If VM ever logs NFS I/O errors, move `vm-data` to a local volume and back up with its snapshot API.
- `grafana` + `victoria-metrics` float on `:latest` → daily image-update timer applies upgrades (VM storage auto-migrates backward-compatibly).
- `house_load_w` is an identity-derivation, not a meter — if Homey (PV) is stale, the number is stale too.
- Price metric `nextenergy_price_eur` carries the *today* cache only; the controller refreshes it hourly (+2 min). Tomorrow's prices appear in the evening plan, not in this series (yet).
- SolarEdge is read **via Homey** (inverter app in Homey). Inverter-local Modbus TCP is disabled in the SetApp settings (checked 2026-11-04, no port 1502 on the IoT LAN) — enabling it would only be needed for direct 1–5 s inverter data.
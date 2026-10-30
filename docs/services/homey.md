---
title: Homey Pro
type: service
description: "Homey Pro hub access from sv01 — Athom OAuth client (read/control) + Homey CLI root-scope session for writes, phase-1 reorganisation result, MQTT bridge, energy exporter, remote-control recipes."
related: [services/mosquitto.md, services/homepage.md, services/uptime-kuma.md]
---

# Homey Pro (hub) — access, automation, energy

Hub: **Homey Pro van Robert**, id `66e57ca24da8fc528fb04f47`, firmware 13.5.3, LAN URL `http://192.168.68.128` (plain URL works from sv01; the `*.homey.homeylocal.com` form resolves via mDNS on the hub's own L2 only).

## Access model (two working paths)

1. **OAuth2 Web API client `sv01-hermes`** — tokens rotate in `/srv/sv01-secrets/homey-oauth.json` (1 h access token, rotating refresh, rewrite the whole file via staged `sudo install`). Chain: account `/user/me` → delegation (`POST https://api.athom.com/delegation/token?audience=homey&homeyId=…`) → hub login (`POST /api/manager/users/login`, **no** Authorization header, `X-Homey-ID`) → manager calls. Scopes: read + `device.control` + `flow.start` + logic — **no write scopes are obtainable** for user-created OAuth clients (Athom caps the consent at the client's registered menu; silent scope filtering returns `200` with empty lists, not 401).
   Executor: `~/.hermes/skills/smart-home/homey/scripts/homey_apply.py` (dry-run/inventory, self-refreshing).
2. **Homey CLI** (`homey` 4.6.2, authed as Robert, state in `~/.athom-cli/settings.json`): its hub sessions carry the **root `homey` scope** — the only known path to full writes from outside the Athom UI. Everything read/writable:
   ```bash
   homey api raw -X PUT --path /api/manager/devices/device/<id> --body '{"name":"…"}'
   homey api raw --path /api/manager/zones/zone --jq 'keys'   # --jq needs the jq binary
   ```
   - CLI gotchas: JSON stdout truncates at ~64 KiB (filter with `--jq` or use path 1 for full dumps); **every CLI invocation mints a fresh cloud delegation** — never loop bulk work through the CLI (hub + cloud throttle hard for minutes; recovery = stop calling, single paced sweeps).
   - **Bulk pattern that works** (`/home/rkruit/homey-inventory/phase1_finish.py`): one Python process talks to the local hub REST directly with the CLI's *cached* hub session token (`settings.json → homeyApi/homey-<id>/token`, 114-char), 2 s pacing, 30 s 429-backoff, sweep-based read-back verification.

### Verified write surface (root scope, 2026-10-30)

| Op | Call | Note |
|---|---|---|
| rename/move device | `PUT /api/manager/devices/device/<id> {"name":…}` / `{"zone":…}` (combined works) | 200 |
| zone create/delete | `POST` / `DELETE /api/manager/zones/zone` | 200 / 204→404 |
| flow create/edit/delete | `POST`/`PUT`/`DELETE /api/manager/flow/flow…` | use a full clone of the GET body (strip `id, __v, createdat, updatedat, status, triggerCount, lastTriggered*`, then modify) |
| flow folder | `POST /api/manager/flow/flowfolder {"name":"Buiten"}`; assign via flow-PUT `folder` | 200 |
| flow trigger | `POST /api/manager/flow/flow/<id>/trigger` (per hub schema; advanced: `/advancedflow/<id>/trigger`) | scope `homey.flow.start` |
| logic variables | `PUT /api/manager/logic/variable/<id> {"value":…}` | also writable via OAuth client |
| **app settings** | `GET`/`PUT /api/manager/apps/app/<id>/setting/<name>` (singular `setting`, body `{"value": …}`; whole set without `/:name`), restart via `POST /app/<id>/restart` | scope `homey.app.control`; verify with GET; the earlier "not exposed" note was a wrong path (`/settings` 404s, `setting` works) |

## Phase 1 — reorganisation (COMPLETE, 2026-10-30)

Everything Robert approved was executed and verified live: heater-style renames (36/37 — the 37th, "Nest keuken", had physically disappeared from the hub per GET 404), thermostat move+rename to Woonkamer, zwembad device → zwembad zone, 4 empty zones deleted, folder "Buiten" with 3 flows, fietsenstalling-flow repaired (action `:off`→`:on`, re-enabled), Homey Bridge virtual device deleted, invite-user already absent. End state: 46 devices · 18 zones · 15 flows · 1 user.
Source of truth: `/home/rkruit/homey-inventory/` (`REPORT.md`, inventory JSON dumps 2026-10-28, `PHASE1_PLAN.md`, `PHASE1_RESULT.md`, `PHASE1_EXEC_LOG.jsonl`, `phase1_finish.py`).

## Phase 2 — automation (2026-10-30)

- **MQTT bridge — fully configured via the API (2026-10-30)**: host-native Mosquitto broker on sv01 ([mosquitto](mosquitto.md)); the Homey app `nl.scanno.mqtt` connects as user `homey` (broker `192.168.1.10:1883`, keepalive 60) — settings pushed through `PUT /api/manager/apps/app/<id>/setting/<name>` with keys `url`, `ip_port`, `tls`, `selfsigned`, `keepalive`, `user`, `password` (scanno's app reads exactly those; `PUT` body `{"value": …}`), then `POST /app/<id>/restart`. Bridge flow **"Hermes MQTT bridge (sv01)"** (id `189feb5b-e336-4883-ad82-eef81d30480b`): MQTT trigger card on `homey/hermes/#` → action publishes `echo [[message]] (via [[topic]])` to `homey/hermes/echo` — proof: sv01 publishes to `homey/hermes/test`, echo arrives at sv01. End-to-end verified. Note the scanno app connects **lazily** — only when a trigger/action card exists or another app subscribes; no flow = no client. Token syntax inside flow args is `[[token]]` (`{{token}}` passes through literally), and a flow created via `POST` may read back `enabled: false` — force an extra `PUT … {"enabled": true}`. sv01-side usage: publish commands to `homey/hermes/…` (user `hermes`), trigger flows, or extend the bridge flow with action cards.
- **Energy exporter**: host systemd unit `sv01-homey-energy` (`sv01/systemd/homey-energy.service`, script `sv01/scripts/homey_energy_export.py`) — pulls `GET /api/manager/energy/live` every 60 s with the CLI's cached session (zero cloud calls; heals a dead session with one CLI re-auth per 10 min max) and serves `http://192.168.1.10:8890/energy.json`. The homepage tile (customapi) shows current consumption, solar yield and the top consumer. Kuma watches the endpoint. LAN-only, no auth (read-only power numbers).
- **Remote control from sv01**: prefer MQTT (see above); for direct REST use the CLI one-liners:

```bash
# trigger a flow
homey api raw -X POST --path /api/manager/flow/flow/<flow-id>/trigger
# set a logic variable (id from: homey api raw --path /api/manager/logic/variable)
homey api raw -X PUT --path /api/manager/logic/variable/<id> --body '{"value":"x"}'
```

## Gotchas

- **Rate limits**: see the bulk pattern above — bursts of ~150 calls put hub *and* cloud into `Too many requests` for minutes; even single sweeps got refused during a penalty. One exporter tick per minute locally is fine.
- `/api/manager/apps/app/<id>/settings` = `Cannot GET` (HTML) — app settings are UI-only.
- A 401 on the hub session means the session died (not the hub); mint a new one (CLI re-auth or `fresh_session()` from `homey_apply.py`).
- Insight-logs (~212 loggers) and energy data: read-only via the OAuth client; keep polling ≤ 1 request/min per endpoint.
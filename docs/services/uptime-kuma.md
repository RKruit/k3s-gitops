---
title: Uptime Kuma
type: service
description: "Health checks + Telegram alerts (Kuma v2.5.7, host-net :3001): design choices, monitor set, API gotchas."
related: [runbooks/kuma-monitor.md, services/beszel.md]
---

# Uptime Kuma 2.x

`louislam/uptime-kuma:2` (v2.5.7) — health checks + Telegram alerts. UI on `:3001` and https://kuma.oostrandpark.com.

## Design choices

- **host-net** (`network_mode: host`): lets Kuma monitor loopback-only host services (Hindsight :9177, Postgres :5432). Side effect: :3001 is also reachable directly on the LAN.
- **`UPTIME_KUMA_DB_TYPE: sqlite`**: without this env a fresh boot waits in the setup-database wizard (no socket.io, no REST setup) — headless provisioning quietly deadlocks.
- Data on NFS (`kuma-data` → `/volume1/k8sv2/kuma-data`). The SQLite DB is at `kuma.db`; **copy `kuma.db` + `kuma.db-wal` + `kuma.db-shm` before reading/backing it up**.
- Admin creds: `/srv/sv01-secrets/kuma-admin.env` (user `hermes-monitoring`).

## Monitors (as of 2026-10-29)

komodo · todo-app · beszel · docs site · homepage · it-tools · authelia · kuma-self · hindsight · ollama · pg0 · gh-webhook (hooks-tunnel) · waha (WhatsApp bridge, port)

!!! note
    **sv02 is deliberately not monitored** (Robert's choice). Don't add sv02 monitors without an explicit request.

!!! note
    The hostname-monitors ride *through* the Authelia wall: Kuma runs host-net (192.168.1.10) and Authelia's ACL lets that XFF bypass ([authelia](authelia.md)). Don't gate that rule off without moving Kuma's checks.

## API automation

The v2 socket.io API is picky — the exact payload rules and the idempotent script are documented in [runbook: add Kuma monitor](../runbooks/kuma-monitor.md) (`sv01/scripts/kuma-add-monitors.py`).

## Gotchas

- A monitor added with a wrong `type` value (e.g. `tcp` instead of `port`) returns *Unknown Monitor Type* and that monitor's scheduler hangs — fix the type, don't just re-add.
- Success on monitor-add is visible as immediate up/down beats at the 60 s interval.
- API-added monitors do **not** inherit the isDefault notification: `kuma-add-monitors.py` links Telegram notification 1 idempotently via `editMonitor` — which expects `notificationIDList` as a **dict** (`{'1': True}`), not a JSON string (a string silently inserts id `'0'` and fails the FK constraint).
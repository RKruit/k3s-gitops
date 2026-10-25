# Uptime Kuma 2.x

`louislam/uptime-kuma:2` (v2.5.7) — health checks + Telegram alerts. UI on `:3001` and https://kuma.oostrandpark.com.

## Design choices

- **host-net** (`network_mode: host`): lets Kuma monitor loopback-only host services (Hindsight :9177, Postgres :5432). Side effect: :3001 is also reachable directly on the LAN.
- **`UPTIME_KUMA_DB_TYPE: sqlite`**: without this env a fresh boot waits in the setup-database wizard (no socket.io, no REST setup) — headless provisioning quietly deadlocks.
- Data on NFS (`kuma-data` → `/volume1/k8sv2/kuma-data`). The SQLite DB is at `kuma.db`; **copy `kuma.db` + `kuma.db-wal` + `kuma.db-shm` before reading/backing it up**.
- Admin creds: `/srv/sv01-secrets/kuma-admin.env` (user `hermes-monitoring`).

## Monitors (as of 2026-10-24)

komodo · todo-app · beszel · kuma-self · hindsight · ollama · pg0

!!! note
    **sv02 is deliberately not monitored** (Robert's choice). Don't add sv02 monitors without an explicit request.

## API automation

The v2 socket.io API is picky — the exact payload rules and the idempotent script are documented in [runbook: add Kuma monitor](../runbooks/kuma-monitor.md) (`sv01/scripts/kuma-add-monitors.py`).

## Gotchas

- A monitor added with a wrong `type` value (e.g. `tcp` instead of `port`) returns *Unknown Monitor Type* and that monitor's scheduler hangs — fix the type, don't just re-add.
- Success on monitor-add is visible as immediate up/down beats at the 60 s interval.
- Telegram notifications were set up in the UI by Robert (the bot token path isn't reachable to tooling — keep it that way).
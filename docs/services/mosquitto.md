---
title: Mosquitto (MQTT broker)
type: service
description: "Host-native Mosquitto MQTT broker on sv01 — LAN-only 192.168.1.10:1883, mandatory auth, AppArmor-constrained; feeds the Homey MQTT Client app and sv01-side automation."
related: [services/homey.md, services/uptime-kuma.md, services/homepage.md, infra/nfs-storage.md]
---

# Mosquitto — MQTT broker (host service)

Mosquitto 2.x (`mosquitto` apt package 2.0.22, systemd service `mosquitto`, phase 2, 2026-10-30). Purpose: a private message bus for the house — the **Homey MQTT Client app** (`nl.scanno.mqtt`) publishes device/flow state under `homey/…`, and sv01-side automation (Hermes cron jobs, scripts) publishes commands that Homey flows can react to, plus reads the same telemetry.

## Why this is NOT a container (and must not become one)

Ubuntu's AppArmor on this host confines the `mosquitto` binary to
`/etc/mosquitto`, `/var/lib/mosquitto`, `/var/log/mosquitto` and `/run/mosquitto`
via **per-binary named profiles** (profile `mosquitto`; kernel 7.0 behaviour: the
confinement applies to a `mosquitto` exec *anywhere*, including inside Docker
containers). Every containerized variant therefore dies at startup with
"Error: Unable to open config file" — verified across eclipse-mosquitto 2.1.2 /
2.0.22 and alpine's own package, plus audit-log evidence
(`apparmor="DENIED" … profile="mosquitto"`). If you must reconsider: the
container route only becomes viable by unloading that AppArmor profile, which
weakens the host — not recommended.

## Exposure & auth

- Listens on **192.168.1.10:1883 only** (config `listener 1883 192.168.1.10`). Deliberately **no Caddy route** (raw TCP cannot pass the Authelia wall) and confined to the LAN by design: no tunnel, no public record.
- **Auth is mandatory** (`allow_anonymous false`); hashed creds in `/etc/mosquitto/passwd` (mirrored to `/srv/sv01-secrets/mosquitto.pw`); plaintext secrets for client setup in `/srv/sv01-secrets/mosquitto.env` (`HOMEY_MQTT_PASS`, `HERMES_MQTT_PASS`) — outside git.
- **ACL** (`/etc/mosquitto/acl.file`): user `homey` may read/write `homey/#` only; user `hermes` may read/write everything. Anonymous users: nothing.
- Config is repo-managed under `sv01/mosquitto/` (canonical copies → `/etc/mosquitto/…`; deploy steps in the README there).

## Data

`persistence true` → `/var/lib/mosquitto/persistence.db` (AppArmor path). Deliberate deviation from the NFS-for-persistence rule: retained-message cache only, path pinned by the AppArmor profile; see `sv01/mosquitto/README.md`.

## Integration

- **Homey** (`nl.scanno.mqtt` v3): broker `192.168.1.10`, port `1883`, user `homey` + password from `mosquitto.env`. The app's broker config itself is set once in the Homey app UI (settings are not exposed via the manager API). The app publishes telemetry and can trigger flows from MQTT messages.
- **sv01 / Hermes**: user `hermes` (ACL `#`) for cron jobs and scripts.
- **Homepage**: the energy tile reads `http://192.168.1.10:8890/energy.json` (host-side exporter — see [homey](homey.md)), not MQTT.

## Monitoring

Uptime Kuma: **port monitor** on `192.168.1.10:1883` ("Mosquitto MQTT") plus an **HTTP monitor** for the exporter (`http://192.168.1.10:8890/energy.json`), both idempotent via `sv01/scripts/kuma-add-monitors.py`.

## Verify

```bash
# round trip with auth
mosquitto_pub -h 192.168.1.10 -p 1883 -u hermes -P "$(grep HERMES_MQTT_PASS /srv/sv01-secrets/mosquitto.env | cut -d= -f2)" -t homey/test -m ok
mosquitto_sub -h 192.168.1.10 -p 1883 -u hermes -P "$(grep HERMES_MQTT_PASS /srv/sv01-secrets/mosquitto.env | cut -d= -f2)" -t 'homey/+' -C 1 -W 5
# broker log (connect/subscribe audit + ACL denials)
sudo journalctl -u mosquitto -n 40 --no-pager
```

## Gotchas

- The passwd/acl files must be owned `mosquitto:mosquitto` (mode 640) — the broker logs future-refusal warnings otherwise.
- Regenerate the passwd file with the **host's** `mosquitto_passwd` (in-container versions can write hash formats the host broker refuses — proven 2026-10-30: "Unable to decode password salt").
- After changing the listener IP, remember Kuma + the Homey app both target `192.168.1.10`.
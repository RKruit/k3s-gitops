---
title: Mosquitto (MQTT broker)
type: service
description: "Mosquitto 2.x MQTT broker on sv01 — LAN-only port 1883, mandatory auth, NFS persistence; feeds the Homey MQTT Client app and sv01-side automation."
related: [services/homey.md, services/uptime-kuma.md, services/homepage.md, infra/nfs-storage.md]
---

# Mosquitto — MQTT broker

`eclipse-mosquitto:2` (container `sv01-mosquitto`, phase 2, 2026-10-30). Purpose: a private message bus for the house — the **Homey MQTT Client app** (`nl.scanno.mqtt`) publishes device/flow state here, and sv01-side automation (Hermes cron jobs, scripts) can publish commands back that Homey flows react to, plus read the same telemetry.

## Exposure & auth

- Listens on **192.168.1.10:1883 only** (host bind mirrors the port on the LAN IP). Deliberately **no Caddy route and no published port on 0.0.0.0**: raw TCP cannot go through the Authelia wall, so it stays LAN-only by construction, consistent with the "never public for NVR-ish services" rule.
- **Auth is mandatory** (`allow_anonymous false`). Passwords are hashed into `/srv/sv01-secrets/mosquitto.pw` (outside git, `root:1883`, 640) and mounted read-only. The plaintext secrets live in `/srv/sv01-secrets/mosquitto.env` (`HOMEY_MQTT_PASS`, `HERMES_MQTT_PASS`) for whoever needs to configure a client.
- **ACL** (`sv01/compose/mosquitto/acl.file`): user `homey` may read/write `homey/#` only; user `hermes` may read/write everything. Anonymous users: nothing.

## Data

- `persistence true` → `persistence.db` on the NFS volume `mosquitto-data` (`/volume1/k8sv2/mosquitto-data`), per the persistent-data-on-NFS convention. The broker is a single writer; no SQLite-style locking caveat here.

## Integration

- **Homey** (`nl.scanno.mqtt` v3): broker `192.168.1.10`, port `1883`, user `homey` + password from `mosquitto.env`. The app's broker config itself is set in the Homey app UI once (its settings are not exposed via the manager API). The app publishes telemetry under its configured topic tree and can trigger flows from MQTT messages.
- **sv01 / Hermes**: with user `hermes` (ACL `#`) cron jobs and scripts can publish control topics and subscribe to all telemetry.
- **Homepage**: the energy tile reads `http://192.168.1.10:8890/energy.json` (host-side exporter — see [homey](homey.md)), not MQTT, so it keeps working even when the broker is down.

## Monitoring

Uptime Kuma watches the broker as a **port monitor** on `192.168.1.10:1883` and the exporter as an **HTTP monitor** on `http://192.168.1.10:8890/energy.json` (both idempotently managed by `sv01/scripts/kuma-add-monitors.py`).

## Verify

```bash
# round trip with auth (mosquitto-clients installed on the host)
mosquitto_pub -h 192.168.1.10 -p 1883 -u hermes -P "$(grep HERMES_MQTT_PASS /srv/sv01-secrets/mosquitto.env | cut -d= -f2)" -t homey/test -m ok
mosquitto_sub -h 192.168.1.10 -p 1883 -u hermes -P "$(grep HERMES_MQTT_PASS /srv/sv01-secrets/mosquitto.env | cut -d= -f2)" -t 'homey/+' -C 1 -W 5
# broker logs (connect/subscribe audit)
sudo docker logs --tail 50 sv01-mosquitto
```

## Gotchas

- The passwd file is mounted **read-only**; to add/remove users regenerate it in place with `mosquitto_passwd` (e.g. a one-off `eclipse-mosquitto:2` container) — never edit it by hand.
- The NFS persistence dir was pre-created and opened (777) because the container runs as uid 1883.
- Healthcheck is a bare TCP probe (`nc -z 127.0.0.1 1883`); container has label `autoheal: "true"`.
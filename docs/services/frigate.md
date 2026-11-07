---
title: Frigate
type: service
description: "Frigate NVR on sv01 around the Coral USB EdgeTPU accelerator — detector-only for now (no cameras yet), git-managed config, data on NFS, route behind Authelia."
related: [services/caddy.md, services/authelia.md, services/mosquitto.md, infra/nfs-storage.md, infra/secrets.md]
---

# Frigate NVR (Coral EdgeTPU)

Deployed 2026-11-07. Purpose: give the Coral USB Accelerator its home before any camera exists — Frigate boots with the EdgeTPU detector wired and motion-only recording preconfigured, so adding a camera later is a config-only change.

## Architecture

```
Coral USB Accelerator (motherboard port, 1a6e:089a runtime)
        │
        ▼
frigate (privileged, /dev/bus/usb + /sys/bus/usb, NFS volumes)
        ▲
Caddy route frigate.oostrandpark.com (Authelia forward_auth)
        │
        ▼
frigate-config-init (busybox one-shot, on every recreate):
copies sv01/compose/frigate/config.yml into the frigate-config volume
```

- Config is **git-managed**: `sv01/compose/frigate/config.yml`. The init container copies it into the `/config` volume at every recreate, so git wins at boot (same idea as the k8s `init-config` pattern). Runtime state — `frigate.db` (sqlite), users, `.jwt_secret` — persists in that same NFS volume.
- **No cameras yet**: only the detector config exists. The UI will show an empty camera list until `cameras:` entries are added.
- Detector: `edgetpu`, `device: usb`. Expect ~10 ms inference when a camera eventually feeds it frames. `detection_fps: 0.0` + `skipped_fps == camera_fps` is the normal motion pre-throttle — not a fault (the silent-CPU-fallback fingerprint is consecutive `TPU found` + `XNNPACK delegate for CPU` in the logs; see the coral-usb skill).
- **Recording is motion-only** (Robert's standing preference): no top-level `record.retain.days` → no 24/7 retention. Alert recordings kept 14 d, detection recordings 7 d (`mode: motion`, Frigate 0.17+ `record.alerts`/`record.detections` schema). Snapshots 14 d (0.18 stores clean WebP only).

## Access & auth

- `https://frigate.oostrandpark.com` — Authelia forward_auth gate **plus** Frigate's own login on port 8971 (two walls, deliberately; Kuma/monitor probes from 192.168.1.10 bypass the Authelia gate via the XFF rule, so Frigate's own login is what stops the bypass).
- Frigate admin credentials: `/srv/sv01-secrets/frigate-admin.env` (`FRIGATE_ADMIN_USER` / `FRIGATE_ADMIN_PASSWORD`), outside git like every secret.
- No published ports. Port 5000 (unauthenticated internal API) is only reachable inside the docker network; nothing integrates with it yet.

## Monitoring

Kuma monitor "Frigate UI" (302-tolerant — anonymous `GET /` redirects to the Frigate login behind the Authelia gate). It only proves the route is up, not that detection works; for that, use `docker exec sv01-frigate curl -s http://127.0.0.1:5000/stats` (unauthenticated internal port) and check `detectors.coral.inference_speed`. Monitor added to `sv01/scripts/kuma-add-monitors.py` — runs only after Robert's unbound entry for the hostname.

## Gotchas

- Image floats on `:stable` → the daily image-update timer applies minor upgrades automatically. Frigate migrates config + DB on boot and rewrites the volume copy; the git source is re-applied by the init container on the next recreate — expect the first boot after a bump to log a migration.
- MQTT is **not** wired yet: Frigate runs fine without it at this scale, and Mosquitto is a host-native service. Wiring it later needs a dedicated mosquitto user (see [mosquitto](mosquitto.md)) — decide before the first camera lands, because the MQTT-retained per-camera state topic is a durable trap once a broker is configured.
- No Frigate healthcheck → no `autoheal` label (autoheal only acts on containers with a healthcheck).
- Storage on NFS per convention (`frigate-config`, `frigate-media`, created 777 upfront). If NFS ever stalls Frigate's sqlite writes, `frigate-config` is the first candidate to move to a local volume.
- Adding cameras later: edit `config.yml` (a `cameras:` block per RTSP source — Frigate docs recommend `detect` at ≤5 fps, sub-stream if the camera has one), push; the init container re-applies it. Restream ports (8554/8555) are deliberately not published — add them only when an integrator needs them.

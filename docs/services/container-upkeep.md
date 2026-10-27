---
title: Container-upkeep
type: service
description: "Automated container upkeep on sv01: daily image-update timer, autoheal (unhealthy restarts) and diun (update notifications via Telegram)."
related: [hosts/sv01.md, services/hermes.md, runbooks/deploy.md, infra/webhook-deploy.md]
---

# Container-upkeep (sv01)

Three small components that automate the maintenance of the sv01 stack, each
with its own role — updating, restarting, reporting:

1. **sv01-image-update.timer** — 1×/day `docker compose pull` + `up -d` for all
   services running floating tags.
2. **autoheal** — restarts containers that stay *unhealthy* (healthcheck keeps
   failing): label-driven.
3. **diun** — checks the registries for new image tags and reports them via
   Telegram: useful for the exclusions (hermes) and as intel.

## 1. image-update.timer

- systemd-oneshot `sv01-image-update.service` (User=rkruit) runs
  `sv01/scripts/image-update.sh`: `docker compose pull` + `up -d --remove-orphans`
  with **the same** `--env-file /srv/sv01-secrets/sv01.env` as the sync unit
  (otherwise `up` would rewrite caddy without the CF token).
- Timer: `OnCalendar=*-*-* 04:17:00` + `RandomizedDelaySec=15min`
  (offset/jitter = avoids collisions with the 5-min sync / webhook deploy),
  `Persistent=true`.
- **Exclusions** (in `image-update.sh`): `hermes` and `egress-proxy` are
  deliberately manual (update = Hermes runbook, see [hermes](hermes.md#operations));
  `caddy` is built via the repo Dockerfile and follows git pushes automatically.
  A new service that must also stay out → update the exclusion list in
  `sv01/scripts/image-update.sh`.
- Force a run: `sudo systemctl start sv01-image-update.service`
  (or wait for 04:17+jitter).

## 2. autoheal (unhealthy-watchdog)

- `willfarrell/autoheal:latest` with the docker socket (ro); every 60 s it
  checks for containers with label `autoheal=true` that are unhealthy and
  restarts them.
- The label is only on **services that have a healthcheck** (todo-app and
  uptime-kuma got theirs from the image, `docs` and `it-tools` got one in the
  compose). Add a healthcheck to a service + the label → covered automatically.

## 3. diun (update-watchdog)

- `crazymax/diun:4` with the docker socket; per schedule (`0 5 * * *`,
  so after the updater) it checks all watched images against the registry.
- Watching is **opt-in**: container-label `diun.enable=true` is set on
  uptime-kuma, homepage, it-tools, beszel(+agent), docs, hermes, diun and
  autoheal itself. `todo-app` (private ghcr) and the pinned images (mongo
  `9.0`, komodo `:2` via env, squid pinned) are deliberately not.
- Notifications go via the Telegram bot `hermes_opark_bot` to chat 7049024558
  (same bot as the Kuma alerts); token + chat-id in `/srv/sv01-secrets/diun.env`
  (`DIUN_NOTIF_TELEGRAM_TOKEN` / `DIUN_NOTIF_TELEGRAM_CHATIDS`), not in git.
- State (cache/db) on NFS: volume `diun-data` → `/volume1/k8sv2/diun-data`.
  Losing it is harmless (re-baselines, the first run notifies nothing).
- Test notification: `sudo docker exec sv01-diun diun notif test`.

## Verification

```bash
systemctl list-timers sv01-image-update.timer
systemctl status sv01-image-update.service
sudo docker logs sv01-autoheal --tail 20   # logs the containers it watches
sudo docker logs sv01-diun --tail 20       # logs the first check (baseline)
```

## Gotchas

- diun can't see through the private ghcr account → todo-app updates still
  *land* automatically (updater), but you don't get advance notice.
- autoheal only restarts what's unhealthy — without a healthcheck a container
  is invisible to the watchdog (crashes are already caught by Docker's
  `restart: unless-stopped`).
- Scheduling diun *before* the updater makes little sense: at 05:00 the
  04:17 run has already happened, so you mainly get notices for what you
  must do manually (hermes).
- diun v4.33's strict schema is inconsistent per section (providers.docker =
  camelCase `watchByDefault`; notif.telegram has no `enable` field) → no
  diun.yml; everything is set via env vars.
- The Telegram token came from Kuma (kuma.db notification config,
  `telegramBotToken`) — NOT from the willem profile in the gateway container
  (that's a different bot).
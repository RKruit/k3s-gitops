---
title: Container-upkeep
type: service
description: "Automatische container-upkeep op sv01: dagelijkse image-update timer, autoheal (unhealthy-restart) en diun (update-melding via Telegram)."
related: [hosts/sv01.md, services/hermes.md, runbooks/deploy.md, infra/webhook-deploy.md]
---

# Container-upkeep (sv01)

Drie kleine onderdelen die het beheer van de sv01-stack automatiseren, ieder met
een eigen rol — updateen, herstarten, melden:

1. **sv01-image-update.timer** — 1×/dag `docker compose pull` + `up -d` voor alle
   services met floating tags.
2. **autoheal** — herstart containers die *unhealthy* (healthcheck faalt
   herhaaldelijk) blijven: label-driven.
3. **diun** — kijkt op de registries of er nieuwe image-tags zijn en meldt dat
   via Telegram: handig voor de exclusies (hermes) en als informatie.

## 1. image-update.timer

- systemd-oneshot `sv01-image-update.service` (User=rkruit) draait
  `sv01/scripts/image-update.sh`: `docker compose pull` + `up -d --remove-orphans`
  met **hetzelfde** `--env-file /srv/sv01-secrets/sv01.env` als de sync-unit
  (anders zou `up` caddy zonder CF-token herschrijven).
- Timer: `OnCalendar=*-*-* 04:17:00` + `RandomizedDelaySec=15min`
  (offset/jitter = botsing met de 5-min-sync/webhook-deploy vermijden), `Persistent=true`.
- **Exclusies** (in `image-update.sh`): `hermes` en `egress-proxy` bewust
  handmatig (update = Hermes-runbook, zie [hermes](hermes.md#operations));
  `caddy` bouwt via de repo-Dockerfile en komt vanzelf mee met git-pushen.
  Nieuwe service die ook uitgesloten moet worden → exclusielijst in
  `sv01/scripts/image-update.sh` bijwerken.
- Handmatig afdwingen: `sudo systemctl start sv01-image-update.service`
  (of gewoon wachten tot 04:17+jitter).

## 2. autoheal (unhealthy-watchdog)

- `willfarrell/autoheal:latest` met de docker-socket (ro); kijkt elke 60 s of er
  containers zijn met label `autoheal=true` die unhealthy zijn en herstart die.
- Label staat **alleen op services die een healthcheck hebben** (todo-app en
  uptime-kuma hebben die via het image, `docs` en `it-tools` hebben een
  healthcheck in de compose gekregen). Healthcheck toevoegen aan een service +
  label → automatisch meegenomen.

## 3. diun (update-watchdog)

- `crazymax/diun:4` met docker-socket; checkt volgens schedule (`0 5 * * *`,
  dus ná de updater) alle gewatchte images tegen de registry.
- Watchen = **opt-in**: container-label `diun.enable=true` staat op
  uptime-kuma, homepage, it-tools, beszel(+agent), docs, hermes, diun en
  autoheal zelf. `todo-app` (privé-ghcr) en de gepinde images (mongo
  `9.0`, komodo `:2`-tag via env, squid pinned) staan er bewust niet bij.
- Meldingen gaan via de Telegram-bot `hermes_opark_bot` naar chat 7049024558
  (zelfde bot als Kuma-alerts); token + chat-id in `/srv/sv01-secrets/diun.env`
  (`DIUN_NOTIF_TELEGRAM_TOKEN` / `DIUN_NOTIF_TELEGRAM_CHATIDS`), níet in git.
- State (cache/db) op NFS: volume `diun-data` → `/volume1/k8sv2/diun-data`.
  Kwijtraken is onschuldig (baseline opnieuw, eerste run meldt niets).
- Test-melding: `sudo docker exec sv01-diun diun notif test`.

## Verificatie

```bash
systemctl list-timers sv01-image-update.timer
systemctl status sv01-image-update.service
sudo docker logs sv01-autoheal --tail 20   # logt welke containers hij bekijkt
sudo docker logs sv01-diun --tail 20       # logt de eerste check (baseline)
```

## Gotchas

- diun kan niet door het privé-ghcr-account heen → todo-app-updates zie je dus
  wel automatisch *landen* (updater), maar niet van tevoren als melding.
- autoheal herstart alleen wat unhealthy is — zonder healthcheck is een
  container onzichtbaar voor de watchdog (crashes vangt Docker's
  `restart: unless-stopped` al op).
- diun vóór de updater zetten (schedule) heeft geen zin: op 05:00 is de
  04:17-run al geweest, dus je ziet vooral meldingen voor wat ú handmatig
  moet doen (hermes).
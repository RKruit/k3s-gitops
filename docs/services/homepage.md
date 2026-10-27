---
description: "gethomepage-dashboard op homepage.oostrandpark.com: stateless config in git, status-dots via docker.sock, Kuma-statuspage-widget."
related:
  - services/uptime-kuma.md
  - runbooks/kuma-monitor.md
---

# Homepage — homelab dashboard

`ghcr.io/gethomepage/homepage` — link-dashboard voor alle sv01-sites: https://homepage.oostrandpark.com (Caddy-route, CF DNS-01). Stateless: alle config staat in de repo (`sv01/compose/homepage/`), geen database en geen NFS-volume.

## Wiring

- Service `homepage` in `sv01/compose/docker-compose.yml` (image `:latest`, geen published port) — Caddy `reverse_proxy homepage:3000` over het compose-default-netwerk.
- `HOMEPAGE_ALLOWED_HOSTS: homepage.oostrandpark.com` is verplicht, anders weigert homepage requests.
- **Status-dots**: container status via `/var/run/docker.sock` (ro). `docker.yaml` definieert de server, per card verwijs je met `server: local` + `container: sv01-<naam>`.
- **Kuma-widget** op de Uptime Kuma-card: status-page `sv01` (group `Sites`), fetch via `http://192.168.1.10:3001` (kuma draait host-net; hostname kan ook, maar zo is hij robuust tegen lokale DNS-hikjes).
- Config-wijzigingen: edit repo → sync-timer pull → `docker restart sv01-homepage` (of `docker compose up -d --force-recreate homepage`).

## Kuma-statuspage

- Publiek: https://kuma.oostrandpark.com/status/sv01 — group `Sites` met alle HTTP-site-monitors (Komodo, Todo, Beszel, Kuma, Docs, Homepage, IT-Tools).
- Aanmaken/bijwerken: `sv01/scripts/kuma-add-status-page.py` (idempotent: `addStatusPage` als slug ontbreekt, daarna `saveStatusPage`). `saveStatusPage` VERVANGT de groups/monitors op de page — voeg nieuwe site-monitors hieraan toe en herdraai het script.
- Draai: `/home/rkruit/.hermes/venvs/monitoring/bin/python3 sv01/scripts/kuma-add-status-page.py` (leest `/srv/sv01-secrets/kuma-admin.env` via sudo).
- Homepage-widget velden: `url` + `slug`; de page toont up/down/uptime/incident.

## Monitoring

Kuma-monitor "Homepage" (id 11) staat als regel in `sv01/scripts/kuma-add-monitors.py` en is aangemaakt (26-10-2026): UP met Telegram-alert (`Hermes Telegram` id 1) gekoppeld. Let op bij API-added monitors: `isDefault` op een notificatie geldt alleen als UI-default in het formulier — koppel notificaties altijd expliciet via `editMonitor` met `notificationIDList={"1": true}`.
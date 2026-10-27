---
title: Homepage
type: service
description: "gethomepage dashboard on homepage.oostrandpark.com: stateless config in git, status-dots via docker.sock, Kuma status-page widget."
related: [services/uptime-kuma.md, runbooks/kuma-monitor.md]
---

# Homepage — homelab dashboard

`ghcr.io/gethomepage/homepage` — link dashboard for all sv01 sites: https://homepage.oostrandpark.com (Caddy route, CF DNS-01). Stateless: all config lives in the repo (`sv01/compose/homepage/`), no database and no NFS volume.

## Wiring

- Service `homepage` in `sv01/compose/docker-compose.yml` (image `:latest`, no published port) — Caddy `reverse_proxy homepage:3000` over the compose-default network.
- `HOMEPAGE_ALLOWED_HOSTS: homepage.oostrandpark.com` is required, otherwise homepage refuses requests.
- **Status dots**: container status via `/var/run/docker.sock` (ro). `docker.yaml` defines the server; per card reference it with `server: local` + `container: sv01-<name>`.
- **Kuma widget** on the Uptime Kuma card: status page `sv01` (group `Sites`), fetched via `http://192.168.1.10:3001` (kuma runs host-net; the hostname would work too, but this is robust against local DNS hiccups).
- Config changes: edit repo → sync timer pulls → `docker restart sv01-homepage` (or `docker compose up -d --force-recreate homepage`).

## Kuma status page

- Public: https://kuma.oostrandpark.com/status/sv01 — group `Sites` with all HTTP site monitors (Komodo, Todo, Beszel, Kuma, Docs, Homepage, IT-Tools).
- Create/update: `sv01/scripts/kuma-add-status-page.py` (idempotent: `addStatusPage` if the slug is missing, then `saveStatusPage`). `saveStatusPage` REPLACES the groups/monitors on the page — add new site monitors here and re-run the script.
- Run: `/home/rkruit/.hermes/venvs/monitoring/bin/python3 sv01/scripts/kuma-add-status-page.py` (reads `/srv/sv01-secrets/kuma-admin.env` via sudo).
- Homepage widget fields: `url` + `slug`; the page shows up/down/uptime/incident.

## Monitoring

Kuma monitor "Homepage" (id 11) is listed in `sv01/scripts/kuma-add-monitors.py` and was created (2026-10-26): UP with a Telegram alert (`Hermes Telegram` id 1) attached. Note for API-created monitors: `isDefault` on a notification is only a UI form default — link notifications explicitly via `editMonitor` with `notificationIDList={"1": true}`.
---
title: IT-Tools
type: service
description: "Stateless dev/web toolbox on tools.oostrandpark.com: wiring, history (k3s → compose), Kuma monitor."
related: [services/homepage.md, runbooks/kuma-monitor.md]
---

# IT-Tools — dev/web tools

`corentinth/it-tools` — collection of dev and web tools (hashes, converters, encoders, formatters): https://tools.oostrandpark.com (Caddy route, CF DNS-01). Fully stateless: no volumes, no database, no NFS volume.

## Wiring

- Service `it-tools` in `sv01/compose/docker-compose.yml` (no published port) — Caddy `reverse_proxy it-tools:80` over the compose-default network.
- Container `sv01-it-tools`; homepage tile with status dot (`server: local` + `container: sv01-it-tools`).
- History: from May 2026 IT-Tools ran on the old k3s/ArgoCD setup (`base/it-tools/`, `argocd/argocd-app-it-tools.yaml` — the manifests still exist but are inert). After the Compose migration of 2026-10-20 the Caddy route was gone and the URL dead (browser cache kept that unnoticed for a while). 2026-10-26: re-established as a compose service.
- DNS: `tools.oostrandpark.com → 192.168.1.10` (record lives in the router DNS; the public zone deliberately has no record).

## Monitoring

Kuma monitor "IT-Tools" (http, `https://tools.oostrandpark.com/`) is listed in `sv01/scripts/kuma-add-monitors.py`; status-page group `Sites` via `kuma-add-status-page.py` (`site_names` includes `it-tools`). Notification: Telegram `Hermes Telegram` (id 1) — link explicitly via `editMonitor` with `notificationIDList={"1": true}`; `isDefault` does not apply to API-created monitors (same pitfall as Homepage).
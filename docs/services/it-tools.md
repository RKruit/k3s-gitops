# IT-Tools — dev/web tools

`corentinth/it-tools` — verzameling dev- en webtools (hashes, converters, encoders, formatters): https://tools.oostrandpark.com (Caddy-route, CF DNS-01). Volledig stateless: geen volumes, geen database, geen NFS-volume.

## Wiring

- Service `it-tools` in `sv01/compose/docker-compose.yml` (geen published port) — Caddy `reverse_proxy it-tools:80` over het compose-default-netwerk.
- Container `sv01-it-tools`; homepage-tegel met status-dot (`server: local` + `container: sv01-it-tools`).
- Historie: vanaf mei 2026 draaide IT-Tools op de oude k3s/ArgoCD-setup (`base/it-tools/`, `argocd/argocd-app-it-tools.yaml` — de manifests staan er nog, maar zijn inert). Na de Compose-migratie van 20-10-2026 was de Caddy-route weg en het URL dood (browser-cache maakte dat een tijdje ongemerkt). 26-10-2026 opnieuw opgezet als compose-service.
- DNS: `tools.oostrandpark.com → 192.168.1.10` (record staat in de router-DNS; publieke zone heeft bewust geen record).

## Monitoring

Kuma-monitor "IT-Tools" (http, `https://tools.oostrandpark.com/`) staat als regel in `sv01/scripts/kuma-add-monitors.py`; status-page group `Sites` via `kuma-add-status-page.py` (`site_names` bevat `it-tools`). Notificatie: Telegram `Hermes Telegram` (id 1) — koppel expliciet via `editMonitor` met `notificationIDList={"1": true}`; `isDefault` geldt niet bij API-added monitors (zelfde valkuil als bij Homepage).
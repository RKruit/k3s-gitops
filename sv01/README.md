# sv01 — Lelystad, Docker Compose (config-as-code)

Vervanger van het oude k3s-cluster (dec 2026-achtige architectuur: zie git-historie van deze repo voor de k8s-manifesten).

## Stack
- **Caddy 2.8** — reverse proxy, `tls internal`, poort 80/443
- **todo-app** — `ghcr.io/rkruit/todo-app:latest`, data op NFS

## Config-as-code / GitOps
- Desired state: **deze map in de repo** (`sv01/compose/`)
- Apply: systemd-timer `sv01-config-sync` — elke 5 min `git pull --ff-only` + `docker compose up -d --remove-orphans` (drift wordt weggecorrigeerd)
- Handmatig forceren: `sudo systemctl restart sv01-config-sync.service`

## Locaties
| Wat | Waar |
|---|---|
| Repo clone | `/opt/sv01-config` |
| Secrets | `/srv/sv01-secrets/` (buiten git) |
| todo-data (NFS) | `192.168.1.2:/volume1/k8sv2/todo-app-todo-app-data-pvc-f673b41b-…` |
| Backups oude stack | `/srv/sv01-backup/` (todo.db + manifests) |

## Ops
- App-update: nieuwe image pushen naar GHCR → `docker compose pull` gebeurt niet automatisch — forceer met timer-restart of `docker compose pull && up -d` in de compose-map (image tag = `latest`, pull elke sync *niet* actief: bewust, wijzig tag of voeg `pull` toe aan de unit)
- DNS: Robert beheert handmatig; `todo.oostrandpark.com` → 192.168.1.10
- TLS: intern CA van Caddy; browser vertrouwt root-cert (zelfde als sv02)
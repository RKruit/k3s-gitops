# RKruit/k3s-gitops — homelab config-as-code

Two hosts, all configuration in this repo. Human-readable docs live in [`docs/`](docs/index.md) and are served as an MkDocs Material site at https://docs.oostrandpark.com (container on sv01).

## Layout

| Path | What |
|---|---|
| `sv01/compose/` | sv01 stack: docker-compose.yml, Caddyfile, Dockerfile (caddy + cloudflare DNS), squid.conf |
| `sv01/systemd/` | `sv01-config-sync.{service,timer}` — the apply units |
| `sv01/bootstrap.sh` | idempotent host bootstrap (docker, user group, units, timer) |
| `sv01/scripts/` | maintenance scripts (Kuma monitor automation, …) |
| `sv02/` | sv02 config; applied on sv02 itself via `sv02-docker.service` |
| `docs/` | MkDocs Material documentation source (`../mkdocs.yml` at repo root) |
| `base/`, `argocd/`, `bootstrap/`, `config_k8s/` | legacy k3s/ArgoCD era — deprecated 2026-10-20, kept only for reference/git history |

## Deploy flow (sv01)

1. Edit the working copy, commit, push.
2. Within 5 min the timer pulls and runs `docker compose up -d --remove-orphans`. Force immediately: `sudo systemctl restart sv01-config-sync.service`.
3. Update the relevant `docs/` page in the same commit so docs never drift.

## Docs

- Config: `mkdocs.yml` (repo root), sources under `docs/`.
- Local preview: `python3 -m venv .venv-docs && .venv-docs/bin/pip install mkdocs-material && .venv-docs/bin/mkdocs serve`
- Live site: `ghcr.io/squidfunk/mkdocs-material` container on sv01 with `/opt/sv01-config` bind-mounted read-only; `mkdocs serve` auto-rebuilds when the sync timer pulls changes.

## Secrets

Never in git or docs — only file paths. `/srv/sv01-secrets/` (sv01), `/srv/sv02/secrets/` (sv02). Inventory: [docs/infra/secrets.md](docs/infra/secrets.md).
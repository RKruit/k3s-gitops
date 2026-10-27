# GitHub

The single source of truth is **[RKuit/k3s-gitops](https://github.com/RKuit/k3s-gitops)** — a **private** repo. Everything deploys from it, these docs are part of it, and the [MkDocs container](../index.md) rebuilds itself from it on every sync pull.

## What's in the repo

- `sv01/`, `sv02/` — config-as-code: compose files, Caddyfile, systemd units, bootstrap, scripts
- `docs/` + `mkdocs.yml` — this documentation site
- `base/`, `argocd/`, `bootstrap/`, `config_k8s/` — legacy k3s/ArgoCD manifests (reference only)

**No GitHub Actions, on purpose.** The deploy loop is the 5-minute systemd timer (pull + `docker compose up -d`), and the docs rebuild in-container from the same pull. No CI to maintain — but also: *every push to `main` reaches sv01 within ≤5 minutes.* Don't push WIP/experiments to `main`, and run the [deploy runbook checks](../runbooks/deploy.md) after pushing anything risky. As of October 2026 a GitHub push-webhook makes deploys near-instant (seconds) — see [GitHub push-deploy](webhook-deploy.md); the timer stays as fallback.

## Two clones on sv01

| Clone | Path | Remote (auth) | Role |
|---|---|---|---|
| Working copy | `~/gitops/k3s-gitops` | HTTPS + PAT (`x-access-token:…@github.com`), `credential.helper=store` | edits, commits, **pushes** |
| Applied clone | `/opt/sv01-config` | **SSH**: `git@github.com:rkuit/k3s-gitops.git` (rkruit's key) | pulled by the sync timer; humans don't edit here |

On sv02 the applied clone (`/opt/sv02-config`) lives on sv02 itself, not on sv01.

Rules:

- Edit/commit/push in the working copy only — the timer owns the applied clone (`--ff-only` every 5 min).
- `git pull` on `/opt/sv01-config` must run **as rkruit**: as root it fails on host-key verification (root has no GitHub SSH context in `known_hosts`). Hand-pull: `sudo -u rkruit git -C /opt/sv01-config pull --ff-only`.
- Local preview of docs before pushing: `mkdocs serve` from the `.venv-docs` venv (see the repo README for the exact commands).

## Credentials (paths only — values never in git/docs)

| Secret | Use |
|---|---|
| `/srv/sv01-secrets/github.txt` | GitHub PAT — scopes `repo` + `write:packages` (HTTPS remote auth + tooling) |
| `/srv/sv01-secrets/ghcr.txt` | `docker login ghcr.io` — pull auth for the private `ghcr.io/rkuit/todo-app:latest` image (Komodo periphery gets it via rkruit's `~/.docker` ro-mount) |

!!! warning "Hardening option"
    Today the working copy carries the PAT **twice in plaintext**: inside `.git/config`'s remote URL and in `~/.git-credentials`. Two easy improvements: (1) strip the token from the remote URL (`git remote set-url origin https://github.com/RKuit/k3s-gitops.git`) and let the credential store do auth, or (2) switch the working copy to the SSH remote like the applied clone. Not changed unilaterally — say the word and I'll do it. See also the [gitleaks TODO](secrets.md).
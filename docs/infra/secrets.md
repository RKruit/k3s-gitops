---
description: "Secrets inventory: paths-only policy, per-host secret files and their consumers, hygiene rules."
related:
  - infra/github.md
  - runbooks/new-host.md
---

# Secrets inventory

Secrets live **outside git**, on each host. This page lists **paths only** — never paste values into git or these docs. If a secret value ever lands in the repo: rotate it, don't just delete the commit.

## sv01 — `/srv/sv01-secrets/`

| File | Contains | Consumed by |
|---|---|---|
| `sv01.env` | CF_API_TOKEN, Komodo DB creds, BESZEL_KEY/TOKEN | sync unit `--env-file`, compose env_file (periphery: symlinked as `/etc/komodo/sv01-secrets/sv01.env`) |
| `kuma-admin.env` | Uptime Kuma admin login (`hermes-monitoring`) | Kuma API scripts |
| `beszel-admin.env` | Beszel UI + superuser login | hub CLI/automation |
| `komodo-api.env` | Komodo API key + secret (admin) | resource API calls |
| `ghcr.txt` | GHCR docker login | `docker login` for private todo-image pulls — [GitHub](github.md) |
| `github.txt` | GitHub PAT (scopes `repo`, `write:packages`) | working-copy HTTPS remote + tooling — [GitHub](github.md) |

Backup this dir when rebuilding a host — it is **not** reproducible from git. See [new-host runbook](../runbooks/new-host.md).

## sv02 — `/srv/sv02/secrets/*.env`

sv02's own secret dir (outside git, same policy).

## Hygiene rules

1. Values stay on the host or in the password store — docs/repo get paths only.
2. Same-commit rule: a config change that *adds* a secret reference must also update this page (paths are fine).
3. Cheap guard worth installing: a `gitleaks` pre-commit hook on this repo (TODO — small effort, catches paste accidents).
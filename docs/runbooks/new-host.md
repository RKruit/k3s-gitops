---
description: "Rebuild/bootstrap a host: idempotent sv01 bootstrap, prerequisites (free ports, restored secrets), data survival."
related:
  - infra/nfs-storage.md
  - infra/secrets.md
  - runbooks/backups-restore.md
---

# Runbook: rebuild / bootstrap a host

sv01's bootstrap is idempotent — safe to re-run after a reinstall:

```bash
sudo bash /opt/sv01-config/sv01/bootstrap.sh
```

What it does: installs `docker.io` + `docker-compose-v2`, adds rkruit to the docker group, installs the sync units from the repo, enables the timer, and runs the first sync immediately.

## Prerequisites

- **Ports 80/443 must be free** before the first `compose up` — check `sudo ss -ltnp`. A competing reverse proxy silently breaks the Caddy bind and the sync unit then fails on container start.
- `/opt/sv01-config` must exist (clone the repo).
- `/srv/sv01-secrets/` must be restored **before** the first sync: the unit passes `--env-file /srv/sv01-secrets/sv01.env` and compose env_file references resolve at up-time.

## Post-bootstrap manual steps (host services, not in compose)

1. Ollama + models (`glm-5.3-flash:cloud`) on :11434.
2. Hindsight daemon + `~/.pg0` Postgres.
3. Docker group note: already-running logins don't see the new group — use `sudo docker` until the next login.

## Data survival

Everything persistent lives on NFS (`/volume1/k8sv2/*`); a host reinstall leaves data intact as long as the NFS server survives. Secrets are the only host-side data to back up/restore manually — see [secrets inventory](../infra/secrets.md) and [backups](backups-restore.md).
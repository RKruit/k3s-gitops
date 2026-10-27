---
description: "Deploy a change: edit → push → sync unit applies; GHCR pull rule, rkruit-pull rule, verification checks."
related:
  - infra/github.md
  - infra/webhook-deploy.md
---

# Runbook: deploy a change

## The normal path

1. Edit the working copy (`~/gitops/k3s-gitops`), commit, **push** (auth model and caveats: [GitHub](../infra/github.md)).
2. The sync timer applies within 5 minutes. To apply immediately:
   ```bash
   sudo systemctl restart sv01-config-sync.service
   ```
3. Verify:
   ```bash
   systemctl status sv01-config-sync.service      # every ExecStart line must be 0/SUCCESS
   sudo docker ps                                  # containers healthy
   curl -sk https://<hostname>/ -o /dev/null -w '%{http_code}\n'
   ```

## Rules

- Pull on `/opt/sv01-config` must run as **rkruit** — as root it fails on GitHub host-key verification (root has no rkruit SSH context). The unit already runs as rkruit; only hand-run pulls need the same user:
  ```bash
  sudo -u rkruit git -C /opt/sv01-config pull --ff-only
  ```
- **New GHCR images:** the timer does not `docker compose pull` — a pushed `latest` stays un-applied until you run `docker compose pull` (as rkruit) or bundle a compose-file change that makes the sync recreate the service.
- Keep the docs updated in the **same commit** as the config change.
- If a change refuses to apply, check the unit output first (`journalctl -u sv01-config-sync.service -n 50`) — a failed bind (e.g. port 80/443 occupied) shows up there, not in compose.
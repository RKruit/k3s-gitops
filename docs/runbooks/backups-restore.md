---
title: Backups & restore
type: runbook
description: "Backups & restore: honest current state (no scheduled job yet), NFS data locations, live-sqlite and per-instance-PKI cautions."
related: [infra/nfs-storage.md, runbooks/new-host.md]
---

# Runbook: backups & restore

## Current state (honest)

- **One-time migration backup** only: `/srv/sv01-backup/` (todo-app data tarball + old-stack manifests + `sv01-caddy-root.crt`). There is **no scheduled backup job yet** — TODO.

## Where the data actually lives

All persistent state is on NFS at `192.168.1.2:/volume1/k8sv2/<dir>` ([mapping](../infra/nfs-storage.md)). Backups therefore mean copying those dirs (which live on the NAS anyway — snapshot there if Synology/QNAP snapshots are available).

| Data | NFS dir | Backup notes |
|---|---|---|
| todo-app | `todo-app-todo-app-data-pvc-f673b41b-…` | sqlite/app data |
| Kuma | `kuma-data` | **copy db+wal+shm**, don't back up a live sqlite |
| Beszel + agent | `beszel-data`, `beszel-agent-data` | hub DB (PocketBase) |
| Hermes gateway data | `hermes-data` | profiles, tokens |

## Restore snippets

- **Caddy CA root (per-instance PKI!)** — every Caddy instance has its own private root; sv01's ≠ sv02's:
  ```bash
  docker run --rm -v sv01_caddy-data:/data alpine cat /data/caddy/pki/authorities/local/root.crt
  ```
  Import each instance's root separately on client machines. (`sv01-caddy-root.crt` in `/srv/sv01-backup/` is the exported copy from migration day.)
- **Kuma DB read/backup** (never the live file):
  ```bash
  mkdir -p /tmp/kuma && cp -a /var/lib/docker/volumes/sv01_kuma-data/_data/kuma.db* /tmp/kuma/
  sqlite3 /tmp/kuma/kuma.db …
  ```
- **Full app data restore from the migration tarball**: stop the service, extract into the NFS volume (`docker run --rm -v sv01_todo-data:/d alpine sh -c 'cd /d && tar xzf …'`), start again.
---
title: Disaster recovery
type: infra
description: "What lives where (git vs NFS vs encrypted backup) and how sv01 + Hermes are rebuilt on new hardware; the nightly hermes-backup archive and its passphrase."
related: [infra/secrets.md, infra/nfs-storage.md]
---

# Disaster recovery: rebuilding sv01 and Hermes

This page is the **what and where**; the executable procedure lives in the Hermes
skill `sv01-restore` (a fresh Hermes on new hardware loads that skill and follows
it step by step).

## What is stored where

| Item | Location | Recovered by |
|---|---|---|
| Compose stacks, dashboards, host scripts, systemd units, docs | this repo (`sv01/`) | `git clone` |
| Passwords and tokens | `/srv/sv01-secrets/` **and** encrypted on the NAS | nightly backup archive |
| Hermes: config, `.env`, skills, scripts, memories, cron jobs | `~/.hermes` (local disk) | nightly backup archive |
| Hermes gateway data (container profiles) | NFS `192.168.1.2:/volume1/k8sv2/hermes-data` | NFS (survives sv01) |
| Grafana / VictoriaMetrics / Kuma / Beszel / todo data | NFS `/volume1/k8sv2/*` | NFS (survives sv01) |
| Backups themselves | `/mnt/nas/hermes_shared/backups/hermes/` (NAS 192.168.1.2) | passphrase |
| Homey, Cloudflare, OPNsense, Daikin bus, NextEnergy | external services | manual (see the skill, phase 6) |

## The backup

`sv01/scripts/hermes_backup.sh`, run daily at 03:30 by `hermes-backup.timer`
(host-native systemd, `RequiresMountsFor=/mnt/nas/hermes_shared`).

- Contents: `/srv/sv01-secrets`, `~/.hermes` (config, `.env`, skills, scripts,
  memories, `cron/jobs.json`), `~/.athom-cli`.
- Format: `hermes-YYYYMMDD-HHMM.tar.gz.gpg` — AES-256 symmetric, passphrase from
  `/srv/sv01-secrets/backup-passphrase`, 14 archives kept (~22 MB each).
- Every run decrypts the fresh archive again as a smoke test; a corrupt archive
  exits non-zero instead of passing silently.
- **Only when something changed.** `hermes_backup_fingerprint.py` hashes the
  *content* of everything in scope (mtimes are ignored) plus a normalised view
  of `cron/jobs.json` — the cron ticker rewrites `last_run_at`/`next_run_at`
  there on every run, which says nothing about what you want to keep. No
  change means no new archive, just a log line; the stored fingerprint lives in
  `/var/lib/sv01-backup/hermes-fingerprint` and is written only after a
  successful, readable archive. Safety net: an archive older than 7 days is
  always refreshed, so a bug in the fingerprint can never leave you without one.
- **The passphrase is the single point of failure.** It must be stored outside
  sv01 (Robert's password manager). Losing it makes every archive worthless.

## Why `~/.hermes` matters

Verified with `findmnt`: `~/.hermes` sits on sv01's **local** disk, not on
NFS. The NAS surviving a dead sv01 therefore does *not* bring back the skills,
memories and cron jobs — only the archive does. That is the reason the backup
exists at all: everything else is either in git or on the NAS.

## Scope limits (deliberate)

- Not in git: secrets, `~/.hermes` state, container volumes — by design.
- Manual after a rebuild: DNS records, Cloudflare tunnel routes, OPNsense
  port-forwards and NAT, the Daikin bridge's MQTT server IP, and (if the session
  expired) the Homey CLI login and the WhatsApp QR scan.
- The repo is the source of truth for *configuration*; the archive is the source
  of truth for *secrets and agent memory*. A rebuild needs both, in that order:
  secrets first, containers second.

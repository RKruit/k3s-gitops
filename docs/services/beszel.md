---
title: Beszel
type: service
description: "Host & container metrics: hub/agent wiring, unix-socket mode, PocketBase quirks, superuser admin."
related: [services/uptime-kuma.md, infra/secrets.md, services/authelia.md]
---

# Beszel — host & container metrics

`henrygd/beszel` hub + agent, both in the sv01 compose project. UI: https://beszel.oostrandpark.com (hub API stays loopback-only: `127.0.0.1:8090:8090`).

## Wiring

- **hub** (`sv01-beszel`): APP_URL=https://beszel.oostrandpark.com, data on NFS (`beszel-data`).
- **agent** (`sv01-beszel-agent`): `network_mode: host`, **unix-socket mode** — `LISTEN=/beszel_socket/beszel.sock` on a shared *local* volume (`beszel-socket`, deliberately **not** NFS: unix socket). Talks to the hub via `HUB_URL=http://localhost:8090`; classic flow (no universal token).
- **Key exchange**: agent's `KEY` env = hub's public key. Get it from the hub's keypair on the beszel-data NFS mount: `ssh-keygen -y -f /beszel_data/id_ed25519`. `BESZEL_KEY`/`BESZEL_TOKEN` live in `/srv/sv01-secrets/sv01.env`.
- Agent's own data on NFS (`beszel-agent-data`).
- Agent mounts `/var/run/docker.sock:ro` for container stats.

## PocketBase quirks (hub is PocketBase-backed)

- Record IDs are **exactly 15 characters** (min AND max!) — generated IDs shorter/longer are rejected.
- `systems.users` relation is required: create a `users` record first, then attach the system.
- After a hub restart it picks up newly registered systems on its own.

## Admin

Superuser CLI: `docker exec sv01-beszel /beszel superuser upsert <email> <password>` — creds live in `/srv/sv01-secrets/beszel-admin.env` (same login works for UI + superuser API).

## Login / SSO (OIDC via Authelia)

Since 2026-10-27 the login page also offers "Login via Authelia" (PocketBase OAuth2 on the `users` collection; client `beszel` defined in `/srv/sv01-secrets/authelia-oidc.yml`, redirect URI `https://beszel.oostrandpark.com/api/oauth2-redirect`).

- The SSO account `rkruit` (email rkuit@gmail.com) **links to the original admin record**: PocketBase auto-links OAuth identities by verified email (the admin record's email was set to `rkuit@gmail.com`, `verified: true`), so the Telegram alert settings and alert rules survive.
- The old password login keeps working in parallel (creds in `beszel-admin.env`) — both paths land on the same record.
- PocketBase-side gotchas (cost an iteration): the provider `name` must be one of PB's reserved custom slots (`oidc`, `oidc2`, `oidc3`); URL fields are `authURL` / `tokenURL` / `userInfoURL` — the lowercase `authUrl` variants are **silently dropped**; and auto-linking matches the provider email against the users record's email only when that record is `verified: true`.
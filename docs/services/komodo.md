---
title: Komodo
type: service
description: "Komodo (mongo/core/periphery): API automation — and why it must never deploy the sv01 stack."
related: [hosts/sv01.md, infra/secrets.md, services/authelia.md]
---

# Komodo — deploy dashboard

`komodo.oostrandpark.com` → `core:9120`. Three services in one compose project: **mongo + core + periphery**.

## Design choices

- **Mongo on a local volume on purpose** (wiredTiger-on-NFS risk), with a 0.25 GB WiredTiger cache cap (RAM headroom).
- **`user: "999:999"` on mongo on purpose**: since the 2026-10-30 upgrade (apparmor 5.0.2 + kernel 7.0.0 + runc 1.4.0, then a reboot) the official mongo entrypoint's `gosu mongodb` step spins forever inside the container (`sched_yield`, 100% CPU, zero mongod output). `id mongodb` runs fine — only the gosu setuid path under the docker-default AppArmor profile hangs; `--security-opt apparmor=unconfined` also works (test only). Running as uid 999 directly skips the entrypoint's root/gosu branch; the db files are already 999-owned, so nothing else changes. Remove `user:` if the host apparmor/runc stack is ever fixed.
- **Key exchange**: shared `komodo-keys` volume + file-based env paths (`KOMODO_PERIPHERY_PUBLIC_KEY=file:/config/keys/periphery.pub` on core; `PERIPHERY_CORE_PUBLIC_KEYS`/`PERIPHERY_PRIVATE_KEY`/`PERIPHERY_CONNECT_AS` on periphery).
- `env_file: /etc/komodo/sv01-secrets/sv01.env` is valid in **both** contexts thanks to the host symlink `/etc/komodo/sv01-secrets → /srv/sv01-secrets`. Don't bind-mount it before the path exists — docker silently creates missing host paths as empty dirs.
- Periphery mounts: `docker.sock`, `/proc`, `/etc/komodo`, `/opt/sv01-config:ro` (stack files), `/srv/sv01-secrets:ro`, and rkruit's `~/.docker:ro → /root/.docker` so compose pulls of the **private GHCR todo-image** authenticate. Don't rely on periphery's own registry config for pulls.

## Deploy rules — read before clicking

!!! danger
    **Never deploy the sv01 stack from Komodo.** A deploy recreates the periphery agent that is executing the deploy itself — the whole stack ends stuck in Created/down. The host-side sync unit is the *only* deploy path for the full stack (it survives a periphery recreate).

- Komodo = dashboard + deliberate deploys of *app* services only. The sync-toml sets `deploy=false`, and poll/auto_update/webhook are off.
- Don't run a Komodo deploy while the 5-minute sync timer can fire (deploys are not `up -d`-idempotent-safe against it simultaneously).

## Login / SSO (OIDC via Authelia)

Since 2026-10-27 Komodo login auto-redirects through Authelia (native OIDC, full-page flow — no popup machinery needed):

- Env in `/srv/sv01-secrets/sv01.env`: `KOMODO_OIDC_ENABLED=true`, `KOMODO_OIDC_PROVIDER=https://auth.oostrandpark.com`, `KOMODO_OIDC_CLIENT_ID/SECRET`, `KOMODO_OIDC_AUTO_REDIRECT=true`; the `komodo` client is registered in `authelia-oidc.yml` (redirect URI `https://komodo.oostrandpark.com/auth/oidc/callback`).
- JIT user creation is on (`KOMODO_ENABLE_NEW_USERS=true`, `KOMODO_DISABLE_USER_REGISTRATION=false`, local sign-up still off via `KOMODO_DISABLE_LOCAL_USER_REGISTRATION=true`). The SSO user lands as **rkruit-PaSiP** (auto-suffix because the `preferred_username` claim `rkruit` collides with the local admin user) and was promoted to **admin** via `/write/UpdateUserAdmin` (endpoint takes `user_id`, not `username`).
- The local admin `rkruit` (super_admin) stays for password login; both accounts exist side by side.
- Gotcha: Komodo core needs to reach the issuer itself — auth.oostrandpark.com resolves fine from the compose network (verified).

## API (automation)

- API key pair: `/srv/sv01-secrets/komodo-api.env` — headers `X-Api-Key` / `X-Api-Secret`, admin level.
- Resource reads/writes: `POST /read/<Type>`, `POST /write/<Type>`. Unknown-type trick: `POST /read/<nonsense>` — the error lists every valid variant.
- Stack `sv01` id: `6ad7d7fd108fb0a9f2de9249` (files_on_host, run dir `/etc/komodo/sv01-config/sv01/compose`, env file `/etc/komodo/sv01-secrets/sv01.env`). Deploy button = idempotent `up -d` (same rules as above apply).
- Actions run as Deno/TS scripts *inside* the core container — not a generic host-shell primitive.
- Account work needs a JWT flow: `POST /auth/login/LoginLocalUser` → `{data.jwt}` → `/auth/manage/UpdatePassword` with `Authorization: <jwt>`.
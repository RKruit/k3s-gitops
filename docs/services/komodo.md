# Komodo — deploy dashboard

`komodo.oostrandpark.com` → `core:9120`. Three services in one compose project: **mongo + core + periphery**.

## Design choices

- **Mongo on a local volume on purpose** (wiredTiger-on-NFS risk), with a 0.25 GB WiredTiger cache cap (RAM headroom).
- **Key exchange**: shared `komodo-keys` volume + file-based env paths (`KOMODO_PERIPHERY_PUBLIC_KEY=file:/config/keys/periphery.pub` on core; `PERIPHERY_CORE_PUBLIC_KEYS`/`PERIPHERY_PRIVATE_KEY`/`PERIPHERY_CONNECT_AS` on periphery).
- `env_file: /etc/komodo/sv01-secrets/sv01.env` is valid in **both** contexts thanks to the host symlink `/etc/komodo/sv01-secrets → /srv/sv01-secrets`. Don't bind-mount it before the path exists — docker silently creates missing host paths as empty dirs.
- Periphery mounts: `docker.sock`, `/proc`, `/etc/komodo`, `/opt/sv01-config:ro` (stack files), `/srv/sv01-secrets:ro`, and rkruit's `~/.docker:ro → /root/.docker` so compose pulls of the **private GHCR todo-image** authenticate. Don't rely on periphery's own registry config for pulls.

## Deploy rules — read before clicking

!!! danger
    **Never deploy the sv01 stack from Komodo.** A deploy recreates the periphery agent that is executing the deploy itself — the whole stack ends stuck in Created/down. The host-side sync unit is the *only* deploy path for the full stack (it survives a periphery recreate).

- Komodo = dashboard + deliberate deploys of *app* services only. The sync-toml sets `deploy=false`, and poll/auto_update/webhook are off.
- Don't run a Komodo deploy while the 5-minute sync timer can fire (deploys are not `up -d`-idempotent-safe against it simultaneously).

## API (automation)

- API key pair: `/srv/sv01-secrets/komodo-api.env` — headers `X-Api-Key` / `X-Api-Secret`, admin level.
- Resource reads/writes: `POST /read/<Type>`, `POST /write/<Type>`. Unknown-type trick: `POST /read/<nonsense>` — the error lists every valid variant.
- Stack `sv01` id: `6ad7d7fd108fb0a9f2de9249` (files_on_host, run dir `/etc/komodo/sv01-config/sv01/compose`, env file `/etc/komodo/sv01-secrets/sv01.env`). Deploy button = idempotent `up -d` (same rules as above apply).
- Actions run as Deno/TS scripts *inside* the core container — not a generic host-shell primitive.
- Account work needs a JWT flow: `POST /auth/login/LoginLocalUser` → `{data.jwt}` → `/auth/manage/UpdatePassword` with `Authorization: <jwt>`.
---
title: Authelia
type: service
description: "SSO provider on auth.oostrandpark.com: one account behind Caddy forward_auth on every UI route, XFF-based Kuma bypass, OIDC live for Beszel."
related: [services/caddy.md, runbooks/caddy-new-site.md, services/uptime-kuma.md, infra/secrets.md]
---

# Authelia — SSO provider

`authelia/authelia:4.39.28` on sv01 — single sign-on for every Caddy-served UI. One account (`rkruit`, in the git users-file), password login, Caddy `forward_auth` in front of each route. The OIDC identity-provider is **live** and Beszel already logs in through it (Komodo follows the same pattern later).

| What | Where |
|---|---|
| Portal | https://auth.oostrandpark.com (Caddy → `authelia:9091`) |
| Config | `sv01/compose/authelia/configuration.yml` + `users_database.yml` (git) |
| Secrets | `/srv/sv01-secrets/authelia.env` (`AUTHELIA_*`; the rkruit password lives here as a comment) |
| Password hash | `users_database.yml` (argon2id, regenerate via `authelia crypto hash generate argon2`) |
| Storage | local volume `authelia-data` on purpose — sqlite + network storage = locking-corruption risk (same call as Komodo's mongo) |
| Sessions | in-memory — **a restart of sv01-authelia means every client re-logs once**; no Redis on purpose |

## How protection works

- `sv01/compose/Caddyfile`: snippet `(authelia)` = Caddy `forward_auth` → `/api/authz/forward-auth` (`copy_headers Remote-User Remote-Groups Remote-Name Remote-Email`). Every protected site does `import authelia` before `reverse_proxy`.
- Authelia ACL: `default_policy: one_factor` — password wall for anything under `*.oostrandpark.com`, finer rules go under `access_control.rules`.
- `hooks.oostrandpark.com` is **not** behind Authelia: it bypasses Caddy entirely (CF tunnel → host receiver on :9100) and GitHub webhooks cannot do a browser login.
- Route naming note: Caddy snippet imports are WITHOUT parens (`import authelia`); `import (authelia)` is parsed as a file import and fails validation.

## Uptime Kuma rides through

Kuma's hostname-monitors (Komodo, todo, beszel, docs, homepage, it-tools) run host-net from 192.168.1.10. Caddy forwards the real client IP as `X-Forwarded-For`, Authelia honors it, and an ACL rule (`domain_regex: '\.oostrandpark\.com$'` + `networks: ['192.168.1.10']` + `bypass`) lets those checks through unauthenticated. Verified in a smoke test: XFF=192.168.1.10 → 200 (bypass), without XFF → 302 to the portal. Rogue XFF presented directly to the raw :9091 LAN port yields at most an empty 200 from the authz endpoint (no app response), and the portal itself stays password-gated.

## OIDC — live (Beszel uses it)

Configured on 2026-10-27 for Beszel's login button; Komodo can follow the same pattern later.

- The provider block (`jwks` + `clients`) deliberately sits **outside git**: `/srv/sv01-secrets/authelia-oidc.yml`, bind-mounted read-only and loaded as a second `--config` file (compose `command`).
- `jwks` = RSA-2048 RS256 key (key_id `sv01rsa`). Validation confirmed 4.39 requires an explicit `jwks` **and** at least one real client — no auto-generated JWKS.
- The `oidc.hmac_secret` env override uses the single-underscore spelling `AUTHELIA_IDENTITY_PROVIDERS_OIDC_HMAC_SECRET`; the double-underscore variant is rejected as "not expected" (both tested).
- `client_secret` is stored argon2-hashed (4.39 warns on plaintext); the plaintext for the app side lives in `/srv/sv01-secrets/beszel-oidc-client.env`.
- Client `beszel`: redirect URI `https://beszel.oostrandpark.com/api/oauth2-redirect`, scopes `openid profile email`, policy one_factor.
- Smoke check: `curl https://auth.oostrandpark.com/.well-known/openid-configuration` (unauthenticated).
- Clients live: `beszel` (redirect `https://beszel.oostrandpark.com/api/oauth2-redirect`, popup flow) and `komodo` (redirect `https://komodo.oostrandpark.com/auth/oidc/callback`, full-page redirect with auto-redirect on login).
- `consent_mode: 'implicit'` on the client means no portal prompt: with a live session the authz endpoint 302s straight to the redirect URI (an `explicit` client shows an interactive consent page instead). Probe gotcha: the `state` parameter must be ≥ 8 chars or Authelia rejects it as too weak.

## Ops

- **Validate before every push:**

  ```bash
  sudo docker run --rm --env-file /srv/sv01-secrets/authelia.env \
    -v /opt/sv01-config/sv01/compose/authelia/configuration.yml:/config/configuration.yml:ro \
    authelia/authelia:4.39.28 authelia validate-config
  ```

- **Password change**: `authelia crypto hash generate argon2` → swap the hash in `users_database.yml`, update the env-file comment, push.
- Health endpoint (no login): `https://auth.oostrandpark.com/api/health` — that is also the Kuma monitor target.
---
title: Caddy
type: service
description: "Reverse proxy + TLS on sv01: custom cloudflare-DNS-01 image, route table, stale-mount and reload gotchas."
related: [runbooks/caddy-new-site.md, infra/network-dns.md]
---

# Caddy — reverse proxy + TLS

Runs as a **custom locally-built image** (`sv01/compose/Dockerfile`): `caddy:2` plus the `caddy-dns/cloudflare` plugin, built with xcaddy on `caddy:2-builder`.

!!! warning "Do not bump the builder tag"
    `caddy:2.8-builder` breaks on its zap deps — stick to `caddy:2-builder`.

## TLS

```
tls {
    dns cloudflare {env.CF_API_TOKEN}
}
```

- DNS-01 challenge via Cloudflare. `CF_API_TOKEN` comes from `/srv/sv01-secrets/sv01.env`, passed by the sync unit (`--env-file`).
- Works with **private-only DNS**: no public A-records needed for certificate issuance.
- Renewal is fully automatic.

## Routes (`sv01/compose/Caddyfile`)

| Hostname | Upstream | Why this upstream |
|---|---|---|
| todo.oostrandpark.com | `todo-app:80` | same docker network |
| komodo.oostrandpark.com | `core:9120` | same docker network |
| kuma.oostrandpark.com | `192.168.1.10:3001` | Kuma runs host-net |
| beszel.oostrandpark.com | `beszel:8090` | same docker network |
| docs.oostrandpark.com | `docs:8000` | this docs site |
| homepage.oostrandpark.com | `homepage:3000` | same docker network |
| tools.oostrandpark.com | `it-tools:80` | same docker network (stateless) |
| auth.oostrandpark.com | `authelia:9091` | Authelia SSO portal — deliberately NO forward_auth (the login page must not require itself) |
| sv02.oostrandpark.com | *(separate Caddy instance on sv02, `tls internal`)* | different host, different PKI |

All seven UI routes sit behind Authelia via the `(authelia)` forward_auth snippet: a new UI route needs `import authelia` before its `reverse_proxy` (snippet imports are without parens). The Authelia wall and its Kuma bypass are documented in [authelia](authelia.md).

!!! warning "COOP breaks OAuth popups"
    PocketBase sends `Cross-Origin-Opener-Policy: same-origin` on every response. On the beszel route that header **nulls `window.opener`** of the cross-origin OIDC popup → the `/api/oauth2-redirect` callback cannot post the code back → SDK shows `oauth2-redirect-failure` and every retry re-opens the popup (endless login loop). Fix (in place since 2026-10-27): `header { -Cross-Origin-Opener-Policy }` in the beszel site block. Watch for this on any other COOP-sending app that gets OIDC popup login.

!!! warning "Bare Authorization tokens crash the authz check"
    PocketBase (and other SPA backends) send `Authorization: <token>` **without a Bearer scheme** on XHR/SSE (e.g. `/api/realtime`). Authelia's forward-auth header authn strategy hard-fails on that parse error even when a perfectly valid session cookie is present — every such request bounces the user back to the portal, so a just-logged-in SPA loops popups forever. Fix (in place since 2026-10-27, all protected routes): `header_up Authorization ""` inside the `(authelia)` snippet — the authz subrequest never sees app-internal tokens; the app itself still receives the original header. Both verified: bare-token + valid cookie → 200 via beszel and komodo.

## Gotchas

- **Mount the whole dir, not the single file.** `- .:/etc/caddy:ro` is deliberate: single-file bind mounts go *stale* when the file is atomically rewritten (every patch tool writes a new inode) — `caddy reload` then happily reads the old file and logs `config is unchanged`.
- **Caddy does not auto-watch the Caddyfile.** After committing a Caddyfile change, either force-recreate (`docker compose up -d --force-recreate caddy`) or reload in place: `docker exec sv01-caddy caddy reload --config /etc/caddy/Caddyfile`.
- **New hostname needs a local DNS entry.** Clients resolve the name only after a manual unbound entry (Robert does DNS by hand). Verify from sv01 first: `curl --resolve <host>:443:127.0.0.1 https://<host> -skI`.
- After a reload, the first HTTPS request to a *new* domain can fail with exit 000 / SSL error for 30–60 s while DNS-01 issuance runs. Wait and retry.

See [runbook: new hostname](../runbooks/caddy-new-site.md).
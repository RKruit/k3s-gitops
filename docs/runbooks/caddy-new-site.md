---
title: New hostname via Caddy
type: runbook
description: "Expose a new hostname: Caddy route, explicit reload, manual unbound entry, DNS-01 timing, monitor hookup."
related: [services/caddy.md, infra/network-dns.md, runbooks/kuma-monitor.md]
---

# Runbook: expose a new hostname via Caddy

1. Add a route to `sv01/compose/Caddyfile`:
   ```caddyfile
   <name>.oostrandpark.com {
       tls {
           dns cloudflare {env.CF_API_TOKEN}
       }
       reverse_proxy <upstream>
   }
   ```
2. Commit + push, let the sync apply (`sudo systemctl restart sv01-config-sync.service` to force).
3. Because of the stale-single-file-mount pitfall, reload explicitly:
   ```bash
   docker exec sv01-caddy caddy reload --config /etc/caddy/Caddyfile
   ```
   (the whole-dir mount makes in-place reloads work; if the mount layout ever changes, `docker compose up -d --force-recreate caddy` is the fallback)
4. DNS (manual, Robert): add the local unbound entry `<name>.oostrandpark.com → 192.168.1.10`. No Cloudflare record needed — DNS-01 issuance works with private-only DNS.
5. Verify **from sv01 first** (clients can't resolve the name until the DNS entry exists):
   ```bash
   curl --resolve <name>.oostrandpark.com:443:127.0.0.1 https://<name>.oostrandpark.com -skI
   ```
   The first request can exit 000 / SSL-error for ~30–60 s while DNS-01 runs — wait and retry.
6. If the upstream is a new service: add it to `docker-compose.yml` in the same commit, and add a [Kuma monitor](kuma-monitor.md).
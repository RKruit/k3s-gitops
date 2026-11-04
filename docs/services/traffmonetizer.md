---
title: Traffmonetizer
type: service
description: "traffmonetizer/cli_v2: bandwidth-sharing CLI (passive income) in a hard sandbox on sv01; no ports, no volumes, no caps, manual image updates only."
related: [hosts/sv01.md, services/container-upkeep.md, services/waha.md]
---

# Traffmonetizer (bandwidth-sharing CLI)

`traffmonetizer/cli_v2:latest` (official image, container `sv01-traffmonetizer`) — sells a slice of idle upstream bandwidth to their proxy pool for ~$0.10/GB (realistic $1–5/month on one always-on device). Headless CLI only: there is no UI on sv01 — earnings/status live in the [Traffmonetizer dashboard](https://traffmonetizer.com), where the device shows up as `sv01`.

## Why the hard sandbox

The vendor is treated as **untrusted third-party software** on purpose:

- Nov 2026: Google's XCPE report (1.3M backdoored Android TV boxes, OEM Videostrong) named the Traffmonetizer SDK among the modules silently enrolling infected devices into proxy networks (no consent screen, API key baked into a botnet loader). No evidence the official app attacks its own users (installers scan clean, 0/91 engines), but the SDK demonstrably circulates via third parties and has not received clear public clarification from the company.
- Ownership: since 2024 under common ownership with DataImpulse/Softoria LLC (large residential-proxy reseller). Payouts are USDT-only, with a spotty 2025–2026 punctuality record and slow support.

Countermeasures on sv01: run only inside a deliberately locked container (below), never pull image updates automatically, and keep the exit-IP trade-off a conscious decision (see below).

## Wiring / isolation

- **Network**: default bridge, no published ports, no `network_mode: host`. The CLI makes only outbound TCP/UDP connections (device is reachable for the network via their relay; nothing listens on the LAN).
- **What it can't touch**: `cap_drop: [ALL]`, `read_only: true` (scratch `/tmp` via tmpfs), **no volumes**, no docker socket, no host mounts.
- **Resources**: 256 MB / 0.5 CPU cap; json-file logs capped at 10 MB × 3.
- **Token**: `traffmonetizer` reads `TRAFFMONETIZER_TOKEN` from `/srv/sv01-secrets/sv01.env` (compose interpolation for the `--token` arg; same pattern as `BESZEL_*`). Outside git. Get the token from the Traffmonetizer dashboard ("Docker" page shows the run command).
- **Device name**: `--device-name sv01` so the dashboard entry is identifiable.
- **Not in Kuma/Caddy/unbound/homepage**: nothing to point a monitor or route at; stats live in the vendor dashboard.

## Known limitation: the exit IP is not isolated

Container isolation protects **sv01 the machine**, not Robert's public IP. All relayed third-party traffic leaves via the home WAN IP behind OPNsense — the same IP any other sv01 egress uses. Public-facing services are unaffected in principle (origin-only behind Cloudflare tunnels), but proxy consumers see the residential IP. Optional follow-up (not implemented): DOCKER-USER nftables rule to block egress toward RFC1918/loopback ranges so the container can only reach public addresses — and/or OPNsense traffic shaping keyed on the container's subnet.

## Updates (manual on purpose)

Excluded from the daily `sv01-image-update.timer` pull (see `sv01/scripts/image-update.sh`, exclusions `hermes|egress-proxy|caddy|traffmonetizer`):

```
sudo docker compose --env-file /srv/sv01-secrets/sv01.env pull traffmonetizer
sudo docker compose --env-file /srv/sv01-secrets/sv01.env up -d traffmonetizer
```

Run both from `/opt/sv01-config/sv01/compose`. Only update after re-checking the current vendor risk picture (Trustpilot, any new supply-chain reports).

## Removal

Delete the `traffmonetizer:` service block from `sv01/compose/docker-compose.yml`, drop it from the exclusions in `sv01/scripts/image-update.sh`, retire this page + the `hosts/sv01.md` row, push, then revoke the device in the Traffmonetizer dashboard. No volumes/data to clean up.
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

Countermeasures on sv01: run only inside a deliberately locked container (below) and keep the exit-IP trade-off a conscious decision (see below). Image updates run automatically with the daily timer (Robert's explicit choice 2026-11-04) with diun reporting new tags; reverting to manual-only updates is a one-line change in `sv01/scripts/image-update.sh`.

## Wiring / isolation

- **Network**: default bridge, no published ports, no `network_mode: host`. The CLI makes only outbound TCP/UDP connections (device is reachable for the network via their relay; nothing listens on the LAN).
- **What it can't touch**: `cap_drop: [ALL]`, **no volumes**, no docker socket, no host mounts; scratch `/tmp` via tmpfs. Rootfs is writable (not read-only): the distroless image ships no shell and writes inside its own directory at startup — the exact path can't be mapped, so after testing `read_only` was dropped. The rootfs stays ephemeral (a `--force-recreate` wipes all state) and the container remains capless and mount-less, which is the protection that matters.
- **Resources**: 256 MB / 0.5 CPU cap; json-file logs capped at 10 MB × 3.
- **Token**: `traffmonetizer` reads `TRAFFMONETIZER_TOKEN` from `/srv/sv01-secrets/sv01.env` (compose interpolation for the `--token` arg; same pattern as `BESZEL_*`). Outside git. Get the token from the Traffmonetizer dashboard ("Docker" page shows the run command).
- **Device name**: `--device-name sv01` so the dashboard entry is identifiable.
- **Not in Kuma/Caddy/unbound/homepage**: nothing to point a monitor or route at; stats live in the vendor dashboard.

## Known limitation: the exit IP is not isolated

Container isolation protects **sv01 the machine**, not Robert's public IP. All relayed third-party traffic leaves via the home WAN IP behind OPNsense — the same IP any other sv01 egress uses. Public-facing services are unaffected in principle (origin-only behind Cloudflare tunnels), but proxy consumers see the residential IP. Optional follow-up (not implemented): DOCKER-USER nftables rule to block egress toward RFC1918/loopback ranges so the container can only reach public addresses — and/or OPNsense traffic shaping keyed on the container's subnet.

## Updates (daily automatic, like other floating tags)

`traffmonetizer` is pulled + applied by the normal `sv01-image-update.timer` (daily 04:17 + jitter) together with the other floating-tag services; diun also reports new image tags on Telegram. Be aware this means trusting the vendor's registry pushes without review — the deliberate-friction alternative stays available:

```
sudo docker compose --env-file /srv/sv01-secrets/sv01.env pull traffmonetizer
sudo docker compose --env-file /srv/sv01-secrets/sv01.env up -d traffmonetizer
```

(run from `/opt/sv01-config/sv01/compose`). To go back to manual-only, add `|traffmonetizer` to the `EXCL` regex in `sv01/scripts/image-update.sh`.

## Removal

Delete the `traffmonetizer:` service block from `sv01/compose/docker-compose.yml`, retire this page + the `hosts/sv01.md` row, push, then revoke the device in the Traffmonetizer dashboard. No volumes/data to clean up.
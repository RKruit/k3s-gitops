---
title: Pawns.app (IPRoyal)
type: service
description: "iproyal/pawns-cli: IPRoyal Pawns.app bandwidth-sharing CLI (passive income) in a hard sandbox on sv01; no ports, no volumes, no caps, auto-updated daily with diun."
related: [services/traffmonetizer.md, hosts/sv01.md, services/container-upkeep.md]
---

# Pawns.app — IPRoyal bandwidth-sharing CLI

`iproyal/pawns-cli:latest` (official IPRoyal image, container `sv01-pawns`) — second bandwidth-sharing app next to [Traffmonetizer](traffmonetizer.md). Pays ~$0.20/GB for a $5 minimum payout (PayPal/crypto) — better rate than Traffmonetizer's $0.10/GB, and notably it was **not** named in Google's Nov-2026 XCPE supply-chain report. IPRoyal publishes its sourcing terms openly. Headless CLI: no UI on sv01 — stats/payouts live in the [Pawns.app dashboard](https://pawns.app).

## Sandbox (same recipe as traffmonetizer)

Untrusted-vendor software from the same product category, treated accordingly:

- **Network**: default bridge, no published ports, no host-net — outbound TCP/UDP only.
- **What it can't touch**: `cap_drop: [ALL]`, **no volumes**, no docker socket, no host mounts; scratch `/tmp` via tmpfs. Rootfs deliberately writable (same distroless-write-profile caveat as traffmonetizer); a `--force-recreate` wipes all state.
- **Resources**: 256 MB / 0.5 CPU cap; json-file logs capped at 10 MB × 3.
- **Credentials**: `PAWNS_EMAIL` + `PAWNS_PASSWORD` in `/srv/sv01-secrets/sv01.env` (outside git), interpolated into the CLI args `-email=… -password=…`. ⚠️ A `$` inside the password must be escaped as `$$` in that file (`sv01.env` is compose-interpolated). If a password contains `$` and causes auth trouble, prefer changing the Pawns account password to one without `$`.
- **Device identity**: `-device-name=sv01`, `-device-id=sv01` — the dashboard entry is identifiable; `-accept-tos` accepts the ToS headlessly.

## Same limitation: the exit IP is not isolated

All relayed traffic still exits via the home WAN IP behind OPNsense — container isolation protects sv01 the machine, not the IP. Two bandwidth-sharing apps on one connection means the residential IP serves that much more third-party traffic; that trade-off is accepted deliberately.

## Updates

Fully automatic: floating `:latest` tag pulled + applied daily by `sv01-image-update.timer` (04:17 + jitter), diun reports new tags on Telegram. Manual variant (same commands as traffmonetizer, service `pawns`).

## Removal

Delete the `pawns:` service block from `sv01/compose/docker-compose.yml`, retire this page + the `hosts/sv01.md` row, push, then delete the `sv01` device in the Pawns dashboard. No volumes/data to clean up.
---
title: Honeygain
type: service
description: "honeygain/honeygain bandwidth-sharing CLI (passive income) in a hard sandbox on sv01; no ports, no volumes, no caps, creds via sv01.env, auto-updated daily."
related: [services/traffmonetizer.md, services/pawns.md, services/mystnodes.md, hosts/sv01.md, services/container-upkeep.md]
---

# Honeygain — bandwidth-sharing CLI

`honeygain/honeygain:latest` (official image, container `sv01-honeygain`) — bandwidth-sharing CLI next to [Traffmonetizer](traffmonetizer.md), [Pawns.app](pawns.md) and [MystNodes](mystnodes.md). Egress-only CLI: no UI on sv01 — stats, earnings and the **$20 payout threshold** live in the [Honeygain dashboard](https://dashboard.honeygain.com). Payouts go through Tipalti → PayPal (or crypto) and lose ~10–15% to provider fees in practice (Trustpilot consensus).

## Vetting verdict (2026-11-04) — deployed on Robert's explicit call

The **worst-documented vendor in this class**: Google Threat Intelligence's XCPE botnet report (Nov 2026) names Honeygain's own proxy SDK — **AppSalt**, ownership chain Honeygain → Oxylabs (rebranded *Source*, Oct 2026, $3.6B valuation) — and states it does **silent third-party enrollment**: the SDK calls `optIn()` without any consent screen on pre-infected TV boxes, and its code skips execution when the host app comes from the official Play Store. Oxylabs gave no reply when contacted; no public statement was found at deploy time. Compare: Traffmonetizer was merely *linked* to the report's SDK list, and Pawns was not named at all.

**Decision 2026-11-04**: Robert chose to deploy anyway (fourth income app on the same WAN IP) — the exposure trade-off is consciously accepted. Revisit the standing decision if Honeygain/Oxylabs ever publish a statement or a sourcing audit.

## Sandbox (same recipe as traffmonetizer)

Untrusted-vendor software from the same category, treated accordingly:

- **Network**: default bridge, no published ports, no host-net — outbound TCP/UDP only; nothing listens on the LAN.
- **What it can't touch**: `cap_drop: [ALL]`, **no volumes**, no docker socket, no host mounts; scratch `/tmp` via tmpfs. The image is Debian-based with its own non-root user (not distroless), but the rootfs stays ephemeral regardless — a `--force-recreate` wipes all state.
- **Resources**: 256 MB / 0.5 CPU cap; json-file logs capped at 10 MB × 3.
- **Credentials**: `HONEYGAIN_EMAIL` + `HONEYGAIN_PASSWORD` in `/srv/sv01-secrets/sv01.env` (outside git), interpolated into the CLI args `-email … -pass …`. Two auth traps: 1) a literal `$` in the password must be doubled to `$$` in `sv01.env` (compose would otherwise start interpolating); 2) an account created via **Google sign-in cannot authenticate the CLI until a password is set in the dashboard** — this image has no token mode, only `-email`/`-pass`/`-device` (verified against the packaged binary).
- **Device identity**: `-device sv01` — dashboard entry identifiable. **Only one device per IP earns**: `sv01` is that device for Honeygain; additional devices only help elsewhere on different IPs (account allows up to 10 devices across different IPs).

## Same limitation: the exit IP is not isolated

All relayed traffic still exits via the home WAN IP behind OPNsense — container isolation protects sv01 the machine, not the IP. Four bandwidth-sharing apps on one connection mean the residential IP serves that much more third-party traffic; that trade-off is accepted deliberately.

## Updates

Fully automatic: floating `:latest` tag pulled + applied daily by `sv01-image-update.timer` (04:17 + jitter), diun reports new tags on Telegram. Manual variant: `sudo docker compose --env-file /srv/sv01-secrets/sv01.env --project-name sv01 -f /opt/sv01-config/sv01/compose/docker-compose.yml pull honeygain && sudo docker compose … up -d --force-recreate --remove-orphans`.

## Removal

Delete the `honeygain:` service block from `sv01/compose/docker-compose.yml`, retire this page + the `hosts/sv01.md` row, push. No firewall/NFS/cert state to clean up. Also consider removing the `sv01` device in the Honeygain dashboard.
---
title: ProxyRack (peer program)
type: service
description: "proxyrack/pop peer client (passive income bandwidth sharing) in a hard sandbox on sv01; $0.50/GB, demand-gated; no caps, no volumes, no ports, auto-updated daily."
related: [services/honeygain.md, services/traffmonetizer.md, services/pawns.md, services/mystnodes.md, hosts/sv01.md, services/container-upkeep.md]
---

# ProxyRack — peer program

`proxyrack/pop:latest` (official image, container `sv01-proxyrack`) — bandwidth-sharing peer client next to [Honeygain](honeygain.md), [Traffmonetizer](traffmonetizer.md), [Pawns.app](pawns.md) and [MystNodes](mystnodes.md). Egress-only: no UI on sv01 — bandwidth/earnings live in the [ProxyRack dashboard](https://proxyrack.com/dashboard). Pays **$0.50/GB** (highest published rate in the class), **$5 minimum**, PayPal (via Tremendous) / wire / gift card.

## Vetting verdict (2026-11-04)

- **Not named** in Google's XCPE botnet report, and its terms state the SDK is distributed only through owned apps with user consent.
- **Trustpilot 4.3 "Excellent"** (71 reviews / 12 months); recent reviews report payouts arriving within ~3 days. Older material has peer payout complaints; treat the 0.50 rate as vendor-stated.
- Chosen by Robert as the fifth app on the same WAN IP (after honeygain, traffmonetizer, myst; pawns blocked by the vendor) — exposure consciously accepted.

## Container caveat — runtime-fetched client code

The official image is deliberately thin: its entrypoint **downloads and runs the latest PoP client from Proxyrack's servers at every boot** ("automatic update of the client script"). The binary that actually runs is therefore whatever the vendor serves that minute — an inherent supply-chain wildcard of this vendor. The containment is the sandbox below: anything fetched executes with no capabilities, no volumes, no host mounts and no network reach beyond egress, and the rootfs is wiped on every `--force-recreate`.

## Sandbox (same recipe as the other share-apps)

- **Network**: default bridge, no published ports, no host-net — outbound TCP/UDP only; nothing listens on the LAN.
- **What it can't touch**: `cap_drop: [ALL]`, **no volumes**, no docker socket, no host mounts; scratch `/tmp` via tmpfs; mem/cpu cap 256 MB / 0.5 CPU; json-file logs 10 MB × 3.
- **Identity/env**: `UUID` (`PROXYRACK_UUID` in `/srv/sv01-secrets/sv01.env` — self-generated 64-hex per the official instructions; losing it orphans the dashboard device), `API_KEY` (`PROXYRACK_API_KEY` — dashboard key; the wrapper self-registers the device at boot with a retry loop), `DEVICE_NAME=sv01`.
- **Note**: the official image does NOT work on Raspberry Pi/ARM — amd64 only (sv01 is amd64).

## First-boot observations (2026-11-04)

- Registration is self-managed by the wrapper: `POST peer.proxyrack.com/api/device/add` (Api-Key header) retries every minute until the response drops `"status": "error"`, then writes the marker `/app/api.cfg` and stops. Observed: a few errored first attempts, then success within ~2 minutes; the marker file being present = registered.
- The client ("home" connection, ID `1793791294`) connected successfully at boot. It ALSO logs recurring vendor-endpoint noise: `connect ECONNREFUSED …:80` (121.91.235.x, 149.51.229.148) and `dial tcp 144.202.45.251:443: i/o timeout`. Verified: those endpoints time out exactly the same from the **sv01 host itself** — vendor-side unreachability, not a sandbox egress fault. Robert confirmed the device shows **online in the ProxyRack dashboard** the same day. If online-state is ever in doubt, the dashboard is the source of truth; log chatter alone does not mean the stack is broken.
- Each boot re-fetches the client artifacts (`script.js` + `PoP_go` binary, from `app-updates.sock.sh`) — see the runtime-fetched code caveat above.

## Earnings expectations

Demand-gated: ProxyRack's clients decide when to use the connection, so stretches of near-zero usage are normal (peer reviews consistently report this). Realistic band: $1–5/month like the rest of the class; the 0.50 rate only matters on weeks with actual demand.

## Same limitation: the exit IP is not isolated

All relayed traffic still exits via the home WAN IP behind OPNsense — container isolation protects sv01 the machine, not the IP. Five share-apps on one connection means the residential IP serves that much more third-party traffic; the trade-off is accepted deliberately.

## Updates

Fully automatic: floating `:latest` tag pulled + applied daily by `sv01-image-update.timer` (04:17 + jitter), diun reports new tags on Telegram. The entrypoint re-fetches the client script at every boot by itself.

## Removal

Delete the `proxyrack:` service block from `sv01/compose/docker-compose.yml`, retire this page + the `hosts/sv01.md` row, push. No firewall/NFS/cert state to clean up. Also remove the `sv01` device in the ProxyRack dashboard and rotate the API key there (it grants device-management).
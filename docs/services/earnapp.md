---
title: EarnApp (Bright Data)
type: service
description: "acoilier/earnapp container of the Bright Data EarnApp Linux agent (passive income) in a hard sandbox on sv01, device identity on NFS; per-IP monthly model, IP-reputation gated."
related: [services/proxyrack.md, services/honeygain.md, services/traffmonetizer.md, services/pawns.md, services/mystnodes.md, hosts/sv01.md, services/container-upkeep.md]
---

# EarnApp — Bright Data bandwidth sharing

`acoilier/earnapp:latest` (community container, container `sv01-earnapp`) running the official **Bright Data EarnApp** Linux agent — the sixth bandwidth app next to [ProxyRack](proxyrack.md), [Honeygain](honeygain.md), [Traffmonetizer](traffmonetizer.md), [Pawns.app](pawns.md) and [MystNodes](mystnodes.md). Egress-only: no UI on sv01 — earnings live in the [EarnApp dashboard](https://earnapp.com/i/dashboard). Model (since Aug 2025): pays **by time, not per GB** — "up to $10/IP/month for US IPs, up to $5 elsewhere", demand-weighted. **$2.50 minimum** (PayPal/Wise) or $50 gift card — the lowest threshold in the class.

## Vetting verdict (2026-11-04) — correction of the earlier board

- **Linux support is back.** Our earlier "rules out home servers/Linux Docker" verdict came from one comparison page plus a *temporary* vendor "internal changes" disable (Trustpilot). The official [install instructions](https://help.earnapp.com/hc/en-us/articles/10261224561553-Installation-instructions-and-hardware-software-requirements) now document Linux x64/arm again — Robert's correction, adopted.
- **Vendor ownership**: Bright Data (ex-Luminati) — the largest data-collection/proxy company; EarnApp is its consumer bandwidth product. **Not named** in Google's XCPE report. Standing watch: Bright Data's SDK is publicly cited as an example of quietly-bundled consent-heavy SDK distribution (consent-abuse class risk at company level — revisit if any disruption report names them).
- **Trustpilot**: pays real (5-year track record, vendor actively answers reviews); mixed on IP-quality complaints — some accounts got "IP blocked". **That is a live risk here**: EarnApp gates on IP reputation, and our home IP now serves five other proxy networks — it may be judged low-quality and never earn. If it flags the IP: accept, remove the device (the app's own dashboard tells you; a blocked flag is not fixable server-side by us). Honest ceiling on a stacked IP: well under the $5/month cap.
- The **official install path is a root systemd service on the host** — rejected for this stack (host-native untrusted-vendor software breaks the class invariant). The container below runs the identical official installer inside the sandbox instead.

## Why this container (acoilier/earnapp) and not another image

Chosen for auditability, per the class's distrust rules: it is a minimal Ubuntu-24.04 wrapper around the **public BrightData installer, whose SHA256 is pinned at build time** — an upstream installer change makes their public CI build fail instead of silently shipping new code; images are built only by public GitHub Actions (Trivy-scanned, cosign-signed, published digests), and they run a daily upstream check that opens an issue when the installer changes (so `:latest` stays the last *reviewed* build even if Bright Data swaps artifacts). Limitation that stays true: the agent binaries themselves are fetched at first run from vendor infrastructure — container hardening is the containment, not a guarantee about the vendor's code.

## Sandbox (like the other share-apps, with one justified deviation)

- **Network**: default bridge, no published ports, no host-net — outbound TCP/UDP only.
- **What it can't touch**: `cap_drop: [ALL]`, `no-new-privileges:true`, no docker socket, no host mounts; tmpfs `/tmp:exec,mode=1777` + `/run` (acoilier's tested defaults — deliberately NOT the class noexec-tmpfs here); 256 MB / 0.5 CPU; json-file logs 10 MB × 3.
- **The deviation (like myst)**: `/etc/earnapp` **is** a volume — `earnapp-data` (NFS `/volume1/k8sv2/earnapp-data`, dir 777 per NAS root-squash). It holds the node identity (`uuid`, state). Without persistence, every container recreate would mint a NEW unlinked device and orphan dashboard entries. Only state on this volume is the identity — earnings are server-side at Bright Data.
- **Identity env**: `EARNAPP_UUID` (`sdk-node-<32 hex>`, pre-generated into `/srv/sv01-secrets/sv01.env`). Changing it later does NOT change a running node (state file exists — swap requires removing `/etc/earnapp/uuid` first).

## Activation — Robert's one click (after first boot)

1. Read the UUID locally (it is a device identity — treat as pre-link credential, don't paste around): `sudo grep ^EARNAPP_UUID /srv/sv01-secrets/sv01.env`
2. While logged in on [earnapp.com](https://earnapp.com), open `https://earnapp.com/r/<the sdk-node-… value>` in the browser → "Device successfully linked" appears; the device shows under **Easy Profits**.
3. Sandbox verification equivalent: `sudo docker exec sv01-earnapp earnapp status` should report `Current status: enabled`; the dashboard shows linked + earning.

## Same limitation: the exit IP is not isolated

All relayed traffic still exits via the home WAN IP behind OPNsense — container isolation protects sv01 the machine, not the IP. Six share-apps on one connection means the residential IP serves that much more third-party traffic; the trade-off is accepted deliberately.

## Updates

Fully automatic: floating `:latest` tag pulled + applied daily by `sv01-image-update.timer` (04:17 + jitter); diun reports new tags on Telegram; the image's own HEALTHCHECK feeds autoheal. NOTE the pinned-installer property above: if Bright Data changes the installer, upstream publishes a reviewed release before a new tag exists — a missing-image-update during such a window is expected behaviour, not breakage.

## Removal

Delete the `earnapp:` service block + `earnapp-data` volume definition from `sv01/compose/docker-compose.yml`, retire this page + the `hosts/sv01.md` row, push. NFS dir `/volume1/k8sv2/earnapp-data` can be deleted after; also delete the `sdk-node-…` device in the EarnApp dashboard.
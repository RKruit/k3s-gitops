---
description: "Hermes gateway + squid egress: full isolation (no route, public-web-only proxy), host Ollama via bridge gateway."
related:
  - hosts/sv01.md
  - infra/network-dns.md
---

# Hermes gateway + egress (squid)

`sv01-hermes` (`nousresearch/hermes-agent:stable`, `gateway run`) + `sv01-hermes-egress` (`ubuntu/squid:6.6-24.04_edge`) — a fenced environment for the Telegram-agent gateway, isolated from the rest of the network.

!!! warning
    The `ubuntu/squid` tag `6.10-24.04_edge` from upstream docs no longer exists — stay on `6.6-24.04_edge`.

## Isolation model

- Hermes runs only on network `hermes-internal` `(internal: true, 172.28.0.0/24)` — **no default route, no internet, no LAN reachability**. No published ports, **no docker socket**, one mount only: NFS `hermes-data` (`/volume1/k8sv2/hermes-data` → `/opt/data`, `HERMES_UID/GID=1000` so files are rkruit-owned).
- **All egress goes through squid** via `HTTP(S)_PROXY=http://egress-proxy:3128` (+ lowercase variants).
- **LLM traffic** goes directly to the **host's Ollama** at `172.28.0.1:11434` (the bridge gateway is reachable from an internal-network container even without a route — the host end of the veth answers). `NO_PROXY=localhost,127.0.0.1,172.28.0.1` — without the NO_PROXY exceptions, Ollama calls would loop through squid and die.
- Resource caps: 4 GB RAM / 2 CPU, log rotation 20 MB × 3.

## Egress policy (squid)

- **Public web open**: HTTPS CONNECT on :443, HTTP :80, web-search endpoints.
- **Private ranges denied**: 10/8, 172.16/12, 192.168/16, 127/8, 100.64/10 (Tailscale), 169.254/16, IPv6 ULA/link-local. The dst-ACL resolves hostnames too — a name that resolves to a private IP is refused as well.
- squid.conf is mounted **as one full file** (no conf.d includes: upstream's default config whitelists "localnet" HTTP — 172.x falls under that).
- Egress to the LAN is *impossible* even with a rogue config: no route + ACL double layer.

## Operations

- Update: `sudo docker compose pull` manually — the timer pulls no images.
- Profiles/users/pairing and the verification battery are Hermes-side runbooks (kept with the Hermes installation, not in this repo).
- This page covers the sv01 gateway container; see [hosts/sv01.md](../hosts/sv01.md) for ports and paths.
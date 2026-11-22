---
title: MystNodes (Mysterium node)
type: service
description: "mysteriumnetwork/myst dVPN node for bandwidth earnings on sv01: dedicated network, NET_ADMIN + tun, published WG range UDP 61000-61100 (custom — default collides with a stale forward), UI LAN-only on :4449."
related: [services/traffmonetizer.md, services/pawns.md, services/container-upkeep.md, hosts/sv01.md, infra/network-dns.md]
---

# MystNodes — Mysterium dVPN node

`mysteriumnetwork/myst:latest` (container `sv01-myst`) — the third passive-income bandwidth app on sv01, next to [Traffmonetizer](traffmonetizer.md) and [Pawns.app](pawns.md). Unlike those two, a MystNodes node is a real **dVPN peer**: it *accepts inbound sessions* (B2B data transfer / scraping / public traffic — 4 types, selectable in the Node UI) and routes them, so it earns meaningfully more per GB. Stats, earnings and settlement live in the [MystNodes dashboard](https://my.mystnodes.com); the local UI is for onboarding and settings.

Deployed per the official [MystNodes docker article](https://help.mystnodes.com/en/articles/3777670-running-a-mystnodes-as-docker-image-on-linux-host) (`service --agreed-terms-and-conditions`).

## Isolation — the honest deviation from the traffic-sandbox recipe

Same untrusted-vendor category, but this workload is architecturally different from the egress-only CLI apps, so the sandbox is intentionally weaker where *the product itself* requires it:

| Control | The share-apps (traffmonetizer/pawns) | myst | Why |
|---|---|---|---|
| Capabilities | `cap_drop: [ALL]` | `cap_drop: [ALL]` re-added `NET_ADMIN`, `SETUID`, `SETGID` | Vendor requirement: the node builds tun/WireGuard interfaces inside the container (docs: NET_ADMIN required) |
| Device access | none | `/dev/net/tun` only | Same requirement |
| Published ports | none | `4449/tcp` (Node UI) + `61000-61100/udp` (WG listener range) | Inbound sessions are the product; the UI also lives on 4449 |
| Network | default bridge | **own network `myst-net`** | No reachability to any other sv01 container (and none back) |
| docker socket / host mounts | none | none | Data dir is a single **local** volume (`myst-data-local` → `/var/lib/mysterium-node`) |
| Resources | 256 MB / 0.5 CPU | 512 MB / 1.0 CPU | Session relay needs a bit more; still capped |
| Logs | 10 MB × 3 | 10 MB × 3 | Same |
| Updates | daily timer + diun | daily timer + diun (floating `:latest`) | Robert's standing choice for these apps |

Keystore/identity + node database live on **local disk** (`/srv/sv01-local/myst-data`, bound into the compose volume `myst-data-local`; the keystore is encrypted by the node itself).

Why local and not NFS: the node stores its database in bbolt, which reads the file through `mmap`. On the NFS datadir that produced a fatal Go `SIGBUS` after a few days uptime (2026-11-22, `fatal error: fault` inside `bbolt.(*Cursor).search`), which crashed the process — the dashboard showed the node offline for the ~1 minute until the restart policy brought it back. Same class of problem as Komodo's MongoDB, which is local for the same reason. The old NFS dir `/volume1/k8sv2/myst-data` is left in place as a cold backup; it is no longer mounted.

Same exit-IP caveat as the other share-apps: relayed third-party traffic leaves via the home WAN IP; container isolation protects sv01 the machine, not the IP.

## Ports + OPNsense (already applied 2026-11-04)

- **WAN → firewall forward**: `udp 61000-61100` on `wanip` → `192.168.1.10` (same ports), NAT rule **"MystNodes sv01 (dVPN WG listener range)"** with a registered pass rule, created via the OPNsense API (`/api/firewall/d_nat/add_rule`, sequence 110). This is the earnings-critical setting: without forwarded WG ports the node reports Strict NAT and most sessions fail.
- **Why not the default range 56000-56100**: an old WAN forward `udp 10000-60000 → 192.168.1.3` (the decommissioned old-sv02 box) overlapped it, so the node's range was set to `61000-61100` via `--wireguard.listen.ports`. That stale forward was deleted on 2026-11-04 (Robert's request); the custom range stays — changing it back would only churn the firewall and the node config for nothing.
- **Flag, corrected 2026-11-22 (myst 1.40.0)**: `--wireguard.listen.ports` is now marked DEPRECATED ("use `--udp.ports`") and is ignored in practice — with it the node bound a random port from the default `10000:60000` range (only UDP socket in the container was e.g. `33127`, none in `61000-61100`), so the forwarded range had no listener behind it and the firewall rule was dead weight. The app-level `--udp.ports 61000:61100` (placed **before** `service`; after it = `flag provided but not defined`) makes the node listen inside the forwarded range. Verify after any myst upgrade by reading the container's UDP sockets (`docker exec sv01-myst cat /proc/net/udp`, hex port in column 2) — a socket inside 61000-61100 must exist.
- **Firewall path verified end-to-end (2026-11-22)**: with `--udp.ports 61000:61100` in place the node draws its pool ports from the forwarded range again (`Supplying port 61014` / `61034`) and its own check against `echo.mysterium.network:4589` reports `All ports [61034 61014] are reachable` + `NAT-traversal-method Method:manual Success:true` — before the fix that check failed (`local port not reachable`) and only holepunching was left. Independent confirmation of the OPNsense side: external UDP probes (check-host.net) to 31.184.106.54:61000/61050/61100 arrive on `eno1`, get DNAT'd to 192.168.1.10 and appear in the container's netns, and the host answers ICMP port-unreachable when nothing listens. Note: the NAT rule has logging off (`log=0`) and the d_nat `set_rule` API returns HTTP 500 on this build, so per-rule logging can only be enabled in the Web UI — use `tcpdump -i any udp dst portrange 61000-61100` for evidence instead.
- **Node UI is deliberately NOT WAN-forwarded** (and has no Caddy route): it is plain HTTP with no login on `http://192.168.1.10:4449` — LAN access only. Reach it from any LAN segment (server LAN + IoT/main LAN). If remote UI access is ever needed, an SSH tunnel (`ssh -L 4449:127.0.0.1:4449 sv01`) is the safe pattern, or a Caddy+Authelia route if requested.

## IP-type override — why proposals say "residential"

Mysterium's location oracle labels the whole AS50266 (Odido — the ISP behind this Glasoperator FTTH line, rDNS `*.ftth.glasoperator.nl`) as `cellular`, which hides the node from consumers that filter for residential IPs. Since 2026-11-04 the container starts with app-level location flags placed **before** the `service` command (after the subcommand they crash-loop, same class as the `--udp.ports` case): `--location.type manual --location.country NL --location.city Almere --location.ip-type residential`.

Trade-offs: the manual resolver keeps the auto-detected public IP (via `--ip-detector`) and the static country/city, but the published proposals lose the ASN/ISP fields (they come from the oracle). City is set to `Almere` because the image entrypoint execs its arguments unquoted — a space-containing value (`Almere Stad`) word-splits and the binary dies (`No help topic for 'Stad'`), so single-word values only. Accepted for now — remove the override if Mysterium support re-classifies AS50266, which also restores full location metadata.

## Onboarding (Robert's part)

1. Open the [MyMystNodes setup page](https://my.mystnodes.com/setup-node) → choose Docker (Linux host) → connect the node by pasting the node's link/credentials from the UI at `http://192.168.1.10:4449`.
2. In the Node UI set the traffic types / bandwidth limits as wanted (defaults are fine).
3. **Verify NAT**: the mystnodes.com dashboard / Node UI should show **Open NAT** (not Strict) after linking — that is the end-to-end proof that the forward works (it tests from the internet side; no locally-driven test can).

## Monitoring

Kuma port-monitor "MystNodes UI (dVPN node)": `tcp 192.168.1.10:4449` (proves the container + UI up; session-level health only shows in the MystNodes dashboard because without registered inbound sessions there is nothing to poll).

## Updates

Floating `:latest`, pulled + applied daily by `sv01-image-update.timer` (04:17 + jitter); diun reports new tags on Telegram. The WG-port flag is deprecated upstream (see above) — after major myst upgrades check the container actually starts (`docker logs sv01-myst`).

## Removal

Delete the `myst:` service block + `myst-data` volume definition from `sv01/compose/docker-compose.yml`, delete the OPNsense forward "MystNodes sv01" (+ its registered pass rule) → apply, remove the Kuma monitor, retire this page + the `hosts/sv01.md` row, push. Local data (`/srv/sv01-local/myst-data`) and the old NFS backup (`/volume1/k8sv2/myst-data`) can be deleted after confirming the node is deregistered at mystnodes.com.
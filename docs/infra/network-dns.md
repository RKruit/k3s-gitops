---
title: Network & DNS
type: infra
description: "IP-plan, DNS policy (Cloudflare zones, DNS-01, manual unbound entries), sv01 hostname table, squid isolation ranges."
related: [services/caddy.md, services/hermes.md, hosts/sv02.md]
---

# Network & DNS

## Addresses

| Host | Address | Notes |
|---|---|---|
| sv01 | 192.168.1.10 | main server |
| NFS server | 192.168.1.2 | `/volume1/k8sv2` export, open to 192.168.1.0/24 |
| sv02 | DNS name `sv02.oostrandpark.com` (`.com` TLD) | separate host, own Caddy |
| *(legacy)* Talos | 192.168.1.3:6443 | **gone** — do not target |

## DNS policy

- All zones live on **Cloudflare**; Robert manages DNS **manually**.
- TLS is issued via **DNS-01** (sv01 Caddy + cloudflare plugin) → hostnames **need no public A-record**.
- Local resolution: manual **unbound** entries per hostname. New hostname = one unbound entry; verify from sv01 with `curl --resolve` before touching client machines.

## Hostnames on sv01's Caddy

| Hostname | → | Upstream |
|---|---|---|
| todo.oostrandpark.com | 192.168.1.10 | todo-app:80 |
| komodo.oostrandpark.com | 192.168.1.10 | core:9120 |
| kuma.oostrandpark.com | 192.168.1.10 | :3001 (host-net) |
| beszel.oostrandpark.com | 192.168.1.10 | beszel:8090 |
| docs.oostrandpark.com | 192.168.1.10 | docs:8000 |
| auth.oostrandpark.com | 192.168.1.10 | authelia:9091 (SSO portal) |

sv02.oostrandpark.com points at sv02 itself (its own Caddy, `tls internal` per-instance PKI — each Caddy instance trusts its own root only).

## Isolation ranges (squid egress denies)

Hermes' squid denies outbound to: 10/8, 172.16/12, 192.168/16, 127/8, 100.64/10, 169.254/16, IPv6 ULA/link-local — see [Hermes + egress](../services/hermes.md).
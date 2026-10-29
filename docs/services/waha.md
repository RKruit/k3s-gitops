---
title: WAHA (WhatsApp bridge)
type: service
description: "WAHA core bridge (NOWEB engine): loopback-only WhatsApp send API :3000 for the daily homelab report cronjob; NFS session storage, no Caddy route."
related: [services/uptime-kuma.md, services/beszel.md, infra/secrets.md, infra/nfs-storage.md]
---

# WAHA — WhatsApp bridge

`devlikeapro/waha:latest` (free *core* edition, container `sv01-waha`) — an unofficial WhatsApp gateway on sv01. Use case: a **daily homelab report** to Robert's phone (cronjob at 07:00): Uptime Kuma monitor statuses + Beszel resource load. Send-only for now — incoming messages are not read.

!!! warning
    WAHA drives WhatsApp over an unofficial protocol (NOWEB/Baileys). Meta can ban accounts that use unofficial automation, so the bridge is logged in on a **spare number** — never on a personal one. The daily report is the only traffic it sends.

## Wiring

- **Engine**: `WHATSAPP_DEFAULT_ENGINE=NOWEB` (Baileys-based, no Chrome, lightweight image).
- **Session persistence**: `waha-data` NFS volume mounted at `/app/.sessions` — one QR scan survives restarts and image updates.
- **No public exposure**: REST API + dashboard bind host loopback `127.0.0.1:3000` only. **No Caddy route, no unbound entry** — by design (Robert prefers internal-only for automation bridges).
- **Credentials**: `/srv/sv01-secrets/waha.env` — `WAHA_API_KEY` (REST + dashboard, header `X-Api-Key`), dashboard login, and `WAHA_CHAT_ID` (report target, `<number>@c.us`).
- **Health**: container healthcheck on `GET /ping`; label `autoheal=true` restarts it when unhealthy. Kuma watches the port (`WAHA (WhatsApp bridge)`, `port` monitor on 127.0.0.1:3000) → container down ⇒ Telegram alarm. If the *session* itself is dead while the container is up, the report cronjob falls back to Telegram.
- **Floating tag**: the daily image timer pulls `latest` like the other floating images. A breaking WAHA update can cost a re-scan of the QR.

## Daily report (07:00 local)

- Script: `~/whatsapp/daily-report.py` (host-side), scheduled via the Hermes cronjob.
- Sources: the full monitor set via `kuma.db` + `-wal` + `-shm` copy read by sqlite (Kuma's socket `heartbeatList` pushes are unreliable for scripted reads; the `time` column is UTC — script adds +1 h) and Beszel PocketBase API (auth via `/srv/sv01-secrets/beszel-admin.env`).
- Sends via `POST http://127.0.0.1:3000/api/sendText` (`{session, chatId, text}`, header `X-Api-Key`).
- Send failures go to Telegram (`hermes_opark_bot`) so a dead session is never silent.

## Operations

- Pairing / re-pair: `POST /api/sessions` `{"name": "default"}` → wait for status `SCAN_QR` → `GET /api/sessions/default/auth/qr?format=image` → scan with the spare number → status `WORKING`. Check with `GET /api/sessions`.
- The target chat id: `GET /api/chats` after messaging the bot once from Robert's own number.
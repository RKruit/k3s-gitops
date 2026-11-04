#!/usr/bin/env bash
# EarnApp entrypoint override (sv01) — runs the OFFICIAL Bright Data pair.
#
# Why not the image's own entrypoint: its foreground monitor drives the daemon
# via `earnapp start`, which is a systemctl-only no-op in a systemd-less
# container (spawned-child dies instantly; verified 2026-11-04 in a
# default-caps container too — not a sandbox effect). The daemon only lives
# via the official systemd units' invocations:
#   earnapp.service          -> ExecStart=/usr/bin/earnapp run          (daemon, Type=simple)
#   earnapp_upgrader.service -> ExecStart=/usr/bin/earnapp autoupgrade  (companion poller)
# With both running, stable TLS connections to the vendor appear within ~40 s
# and the image's own healthcheck (`pgrep -x earnapp`) passes for real.
set -euo pipefail

mkdir -p /etc/earnapp /var/log/earnapp

# Same guard as the image's own entrypoint: a fresh container fs (image
# update => recreate) means no binary yet -> run the checksum-pinned
# official installer (fetches the current agent from cdn-earnapp.b-cdn.net).
if ! command -v earnapp >/dev/null 2>&1; then
  echo "[earnapp-safe] EarnApp binary not found; running official installer."
  earnapp-install -y
fi

# Identity: same logic as the image's entrypoint — env wins only on an empty
# uuid file. Identity lives on the NFS volume and survives recreates;
# never overwrite an existing one here.
if [[ -n "${EARNAPP_UUID:-}" && ! -s /etc/earnapp/uuid ]]; then
  echo "[earnapp-safe] Writing provided EARNAPP_UUID to persistent state."
  printf '%s' "$EARNAPP_UUID" > /etc/earnapp/uuid
  chmod 0600 /etc/earnapp/uuid || true
fi

# Re-set the on-disk enabled state like the image's old monitor did each
# cycle (`earnapp start` writes enabled; spawns nothing without systemd).
# Runtime 'earnapp stop' stays possible; a recreate returns to enabled.
earnapp start >/dev/null 2>&1 || true

echo "[earnapp-safe] Starting official pair: autoupgrade (bg) + run (PID 1)."
earnapp autoupgrade >/dev/null 2>&1 &
exec earnapp run

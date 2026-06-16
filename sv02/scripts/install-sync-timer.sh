#!/bin/bash
# Install the systemd timer/service that runs sync.sh every 5 min.
# Runs as root (we need to write to /etc/systemd/system).
set -euo pipefail

REPO_DIR=/opt/sv02-config
SYNC_SCRIPT=$REPO_DIR/sv02/scripts/sync.sh
SERVICE_NAME=sv02-sync.service
PODMAN_USER=${PODMAN_USER:-${SUDO_USER:-podman}}

log() { echo "[install-sync-timer] $(date -Iseconds) $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "must run as root" >&2
    exit 1
fi

# === System service (runs as root) ===
cat > /etc/systemd/system/$SERVICE_NAME <<EOF
[Unit]
Description=sv02 GitOps sync (pull k3s-gitops, restart changed Quadlets)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$SYNC_SCRIPT
User=root
# Allow the script to read /srv/sv02/secrets for hash comparisons
# (it doesn't actually read the secrets content, just stat)
ProtectSystem=full
PrivateTmp=true
EOF

# === System timer (every 5 min) ===
cat > /etc/systemd/system/sv02-sync.timer <<EOF
[Unit]
Description=Run sv02-sync every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Unit=$SERVICE_NAME

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now sv02-sync.timer

log "installed $SERVICE_NAME + sv02-sync.timer"
log "verify with: systemctl list-timers sv02-sync.timer"
log "manual run: systemctl start $SERVICE_NAME"

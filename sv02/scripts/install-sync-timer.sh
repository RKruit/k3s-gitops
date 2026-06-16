#!/bin/bash
# Install the sv02-sync systemd timer.
#
# Pulls the latest sv02/ config from GitHub every 5 minutes and
# restarts the affected containers. This is the GitOps loop.
#
# The sync.sh script is shared with the podman-era install (it just
# runs `git pull` and decides which containers to restart). The
# restart logic is now simpler since everything is one compose
# project: any change to sv02/ triggers `docker compose up -d`.
set -euo pipefail
chmod +x "$0" 2>/dev/null || true

REPO_DIR=/opt/sv02-config
SYNC_SCRIPT=$REPO_DIR/sv02/scripts/sync.sh
SERVICE_NAME=sv02-sync.service
TIMER_NAME=sv02-sync.timer
PODMAN_USER=${PODMAN_USER:-${SUDO_USER:-rkruit}}

if ! id "$PODMAN_USER" &>/dev/null; then
    echo "[install-sync-timer] FATAL: user $PODMAN_USER does not exist" >&2
    exit 1
fi

if [[ ! -x "$SYNC_SCRIPT" ]]; then
    echo "[install-sync-timer] FATAL: $SYNC_SCRIPT not found or not executable" >&2
    echo "Run setup-host.sh first to clone the repo." >&2
    exit 1
fi

# Make sure the script is executable (git can reset the mode bit)
chmod +x "$SYNC_SCRIPT"

# The sync service runs as root so it can write to /opt/sv02-config
# (which is root-owned) and restart the docker compose project.
# It also writes to /srv/sv02 (root-owned).
cat > /etc/systemd/system/$SERVICE_NAME << EOF
[Unit]
Description=sv02 GitOps sync (git pull + docker compose up)
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=$REPO_DIR
ExecStart=$SYNC_SCRIPT
StandardOutput=journal
StandardError=journal
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/$TIMER_NAME << EOF
[Unit]
Description=Run sv02 GitOps sync every 5 minutes
Requires=$SERVICE_NAME

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now $TIMER_NAME

echo "[install-sync-timer] installed. timer status:"
systemctl status $TIMER_NAME --no-pager | head -10
echo "[install-sync-timer] to trigger a sync now: systemctl start $SERVICE_NAME"

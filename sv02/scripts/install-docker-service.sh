#!/bin/bash
# Install the sv02-docker systemd service.
#
# This service runs `docker compose up -d` at boot (and on
# failure) so all 3 containers (frigate, mosquitto, caddy)
# come up automatically.
#
# The compose project lives at /opt/sv02-config/sv02/, and
# runs as user rkruit (not root) — but with the docker group
# so it can talk to the docker socket.
set -euo pipefail
# Self-execute in case git reset the mode bit. (Idempotent.)
chmod +x "$0" 2>/dev/null || true

REPO_DIR=/opt/sv02-config
PODMAN_USER=${SUDO_USER:-${PODMAN_USER:-rkruit}}
SERVICE_NAME=sv02-docker.service
UNIT_PATH=/etc/systemd/system/$SERVICE_NAME

if ! id "$PODMAN_USER" &>/dev/null; then
    echo "[install-docker-service] FATAL: user $PODMAN_USER does not exist" >&2
    exit 1
fi

if ! command -v docker &>/dev/null; then
    echo "[install-docker-service] FATAL: docker not installed (run setup-host.sh first)" >&2
    exit 1
fi

if ! getent group docker >/dev/null; then
    echo "[install-docker-service] FATAL: docker group does not exist" >&2
    exit 1
fi

# Add the user to the docker group (idempotent)
if ! id -nG "$PODMAN_USER" | grep -qw docker; then
    echo "[install-docker-service] adding $PODMAN_USER to docker group"
    usermod -aG docker "$PODMAN_USER"
fi

# Write the systemd unit. We use Type=oneshot with RemainAfterExit=yes
# so systemd tracks the state and the service shows as "active" when
# the compose project is up. Restart=on-failure restarts the whole
# project if any container dies unexpectedly.
echo "[install-docker-service] writing $UNIT_PATH"
cat > "$UNIT_PATH" << EOF
# Systemd unit for the sv02 docker compose project.
# Manages frigate, mosquitto, and caddy as a single service.
#
# After= docker.service ensures docker is up first.
# Wants= network-online.target ensures port forwarding works.
[Unit]
Description=sv02 docker compose (frigate, mosquitto, caddy)
Documentation=https://github.com/RKruit/k3s-gitops/tree/main/sv02
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=$PODMAN_USER
Group=docker
WorkingDirectory=$REPO_DIR/sv02

# Pull the latest images (best-effort), then bring up the project.
# --remove-orphans cleans up any containers from a previous install
# (e.g., the old podman containers that we left behind).
ExecStart=/usr/bin/docker compose --file $REPO_DIR/sv02/docker-compose.yml pull --ignore-pull-failures
ExecStart=/usr/bin/docker compose --file $REPO_DIR/sv02/docker-compose.yml up -d --remove-orphans
ExecStop=/usr/bin/docker compose --file $REPO_DIR/sv02/docker-compose.yml down
ExecStop=/usr/bin/docker compose --file $REPO_DIR/sv02/docker-compose.yml rm -f

# Auto-restart the whole project if any container fails.
# The compose project is treated as a single unit here.
Restart=on-failure
RestartSec=30s
TimeoutStartSec=600

# Compose handles stdout/stderr. We just need the logs to land in journald.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd to pick up the new unit
systemctl daemon-reload

# Enable + start it (start will fail if the user is not yet in the
# docker group for the running session — that's OK, the user can
# start it manually after re-logging in)
echo "[install-docker-service] enabling $SERVICE_NAME"
systemctl enable "$SERVICE_NAME"

# Try to start it now
echo "[install-docker-service] starting $SERVICE_NAME"
if systemctl start "$SERVICE_NAME" 2>&1; then
    echo "[install-docker-service] $SERVICE_NAME is up. Status:"
    systemctl status "$SERVICE_NAME" --no-pager | head -10
else
    echo "[install-docker-service] WARN: $SERVICE_NAME failed to start."
    echo "This is often because the user is not in the docker group"
    echo "for the current session. The user needs to log out and back in,"
    echo "or run: newgrp docker"
    echo "After that: sudo systemctl start $SERVICE_NAME"
fi

echo "[install-docker-service] done."

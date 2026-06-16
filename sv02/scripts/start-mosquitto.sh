#!/bin/bash
# Start, stop, and status helpers for the mosquitto container.
#
# Used by the @reboot cron job on sv02 instead of the Quadlet
# systemd service. The systemd user context has a newuidmap bug
# on this Trixie host (write to uid_map fails with EPERM) that
# makes podman run from a systemd user service fail. Running
# from cron @reboot (or this script directly) uses a normal
# user session where podman works fine.
#
# Usage:
#   start-mosquitto.sh start
#   start-mosquitto.sh stop
#   start-mosquitto.sh status
#   start-mosquitto.sh restart
#
# Environment:
#   HOME must be set (cron provides it).
#   XDG_RUNTIME_DIR is set by pam_systemd at login but may not
#   be set at @reboot time. We export it from the uid.
set -euo pipefail

CONTAINER_NAME=mosquitto
IMAGE=docker.io/library/eclipse-mosquitto:2.0
NETWORK=sv02
DATA_DIR=/srv/sv02/mosquitto/data
SECRETS=/srv/sv02/secrets/mosquitto.env

# XDG_RUNTIME_DIR for the podman user
export XDG_RUNTIME_DIR=/run/user/$(id -u)

cmd="${1:-status}"

case "$cmd" in
    start)
        # Make sure the data dir + files are present and readable.
        # The renderer service handles this on systemd, but cron
        # runs before systemd user services are guaranteed to have
        # run. So we render synchronously here too. The renderer
        # is idempotent.
        sudo -n /opt/sv02-config/sv02/scripts/render-mqtt-passwd.sh

        # Stop any existing instance (idempotent)
        podman rm -f "$CONTAINER_NAME" 2>/dev/null || true

        # Start the container. We use the exact same args as the
        # Quadlet would generate, but invoked directly.
        exec podman run -d \
            --name "$CONTAINER_NAME" \
            --replace \
            --rm \
            --cgroups=split \
            --hostname mosquitto.sv02.internal \
            --network "$NETWORK" \
            --security-opt label=disable \
            -v "$DATA_DIR:/mosquitto/data:z" \
            --publish 1883:1883 \
            --env-file "$SECRETS" \
            --label io.containers.autoupdate=registry \
            "$IMAGE" \
            mosquitto -c /mosquitto/data/mosquitto.conf
        ;;
    stop)
        podman rm -f "$CONTAINER_NAME" 2>/dev/null || true
        ;;
    status)
        if podman ps --filter "name=$CONTAINER_NAME" --format '{{.Names}}' | grep -q "$CONTAINER_NAME"; then
            echo "mosquitto: running"
            podman ps --filter "name=$CONTAINER_NAME"
            exit 0
        else
            echo "mosquitto: NOT running"
            exit 1
        fi
        ;;
    restart)
        "$0" stop
        "$0" start
        ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}" >&2
        exit 2
        ;;
esac

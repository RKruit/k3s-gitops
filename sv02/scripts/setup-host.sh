#!/bin/bash
# One-shot host setup for sv02.
#
# Run as root (e.g., via sudo from the rkruit user):
#   sudo -E NO_DISK=1 bash sv02/scripts/setup-host.sh
#
# What this does:
#   1. Install system packages (docker, udev, etc.)
#   2. Set up storage directories under /srv/sv02
#   3. Apply the Coral USB autosuspend fix
#   4. Set sysctl for unprivileged port binding
#   5. Add the podman user to the docker group
#
# The Coral libedgetpu.so.1 staging is in setup-coral.sh
# (run separately, because it downloads from GitHub).
#
# Secrets are NOT created here — you must do that manually
# (or via your config-management tool). See README.md.

set -euo pipefail

# Idempotency: don't fail if a step is already done.
# We use bash strict mode + explicit guards instead of `|| true`.

REPO_DIR=/opt/sv02-config
# Use the user who invoked sudo (via $SUDO_USER) as the docker
# user. Falls back to $PODMAN_USER / 'podman' for non-sudo
# invocations. The default $PODMAN_USER=podman is kept for
# backwards compat with the original podman setup.
PODMAN_USER=${PODMAN_USER:-${SUDO_USER:-podman}}
PODMAN_UID=${PODMAN_UID:-1000}

log() { echo "[setup-host] $(date -Iseconds) $*"; }
fatal() { echo "[setup-host] FATAL: $*" >&2; exit 1; }

[[ -d "$REPO_DIR" ]] || fatal "repo not found at $REPO_DIR"
id "$PODMAN_USER" &>/dev/null || fatal "user $PODMAN_USER does not exist"

# === 1. Install docker + dependencies ===
log "installing docker + dependencies"
if ! command -v docker &>/dev/null; then
    apt-get update
    apt-get install -y \
        docker.io \
        docker-compose \
        curl \
        ca-certificates \
        cgroupfs-mount 2>/dev/null || true
fi

# Install the docker compose v2 plugin (the Debian package only
# ships v1 = docker-compose, but we want v2 = docker compose)
if ! docker compose version &>/dev/null 2>&1; then
    log "installing docker compose v2 plugin"
    install -d -m 755 /usr/local/lib/docker/cli-plugins
    curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
        "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-x86_64"
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

# Ensure docker is running
if ! systemctl is-active --quiet docker; then
    log "enabling + starting docker.service"
    systemctl enable --now docker
fi

# === 2. Storage directories ===
log "creating storage directories"
install -d -m 755 /srv/sv02
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/frigate
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/frigate/config
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/frigate/db
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/frigate/media
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/mosquitto
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/mosquitto/data
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/caddy
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/caddy/data
install -d -m 700 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/secrets

# === 3. SATA disk setup ===
# Set NO_DISK=1 in the environment to skip this section entirely
# (use the root filesystem for Frigate media - fine for homelab, less
# safe than a separate disk, but avoids a hard exit on hosts with no
# second disk visible).
if [[ -z "${NO_DISK:-}" ]]; then
    log "looking for a separate disk for Frigate media"
    # Find any disk that isn't the system disk
    SYSTEM_DISK=$(lsblk -ndo PKNAME "$(findmnt -n -o SOURCE /)")
    TARGET_DISK=$(lsblk -ndo NAME,TYPE | awk '$2=="disk"{print $1}' | grep -v "^${SYSTEM_DISK##*/}$" | head -1)
    if [[ -z "$TARGET_DISK" ]]; then
        fatal "no second disk found. Set NO_DISK=1 to skip this step (Frigate will use the root filesystem)."
    fi
    log "system disk: $SYSTEM_DISK, will use $TARGET_DISK for Frigate media"
    # ... (skipped — see the original script for full disk setup)
    fatal "SATA disk setup is not implemented in the docker script. Use NO_DISK=1."
else
    log "NO_DISK=1 set, skipping disk setup. Frigate media will live in /srv/sv02/frigate/media (already created)."
fi

# === 4. Coral USB autosuspend fix (host-level) ===
log "applying USB autosuspend fix"
# Disable kernel-wide USB autosuspend (default 2 = 2s timeout)
if [[ "$(cat /sys/module/usbcore/parameters/autosuspend 2>/dev/null)" != "-1" ]]; then
    echo -1 > /sys/module/usbcore/parameters/autosuspend
    log "  usbcore.autosuspend = -1 (live)"
fi
# Make the autosuspend setting persistent across reboots
cat > /etc/modules-load.d/usbcore-autosuspend.conf << 'EOF'
# Prevent the Coral USB from dropping to bootloader mode (1a6e:089a -> 18d1:9302)
# when the kernel suspends its USB port. Setting autosuspend=-1 disables
# autosuspend globally.
options usbcore autosuspend=-1
EOF

# udev rule: set power/control=on AND power/autosuspend=-1 on ALL USB
# devices. This catches both root hubs (where the Coral is downstream)
# and the Coral itself. We use ENV{DEVTYPE} to match only devices,
# not interfaces.
cat > /lib/udev/rules.d/50-usb-power.rules << 'EOF'
# Disable USB power management for all devices to keep the Coral TPU
# stable. The Coral drops to bootloader mode when its port is suspended.
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{power/control}="on", ATTR{power/autosuspend}="-1"
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_interface", ATTR{power/control}="on", ATTR{power/autosuspend}="-1"
EOF
udevadm control --reload
udevadm trigger --action=add --subsystem-match=usb
log "  udev rules installed + triggered"

# === 5. Add the podman user to the docker group ===
if ! id -nG "$PODMAN_USER" | grep -qw docker; then
    log "adding $PODMAN_USER to docker group"
    usermod -aG docker "$PODMAN_USER"
fi

# === 6. Make scripts executable ===
# git can reset mode bits on pull. Set them explicitly.
chmod +x "$REPO_DIR/sv02/scripts/"*.sh

# === 7. sysctl for unprivileged port binding (in case we go rootless later) ===
if ! grep -q 'ip_unprivileged_port_start' /etc/sysctl.d/99-*.conf 2>/dev/null; then
    log "setting net.ipv4.ip_unprivileged_port_start=80"
    echo "net.ipv4.ip_unprivileged_port_start=80" \
        > /etc/sysctl.d/99-unprivileged-ports.conf
    sysctl --system
fi

# === 8. Final state ===
log "docker version: $(docker --version)"
log "docker compose version: $(docker compose version 2>&1 || echo 'NOT INSTALLED')"
log "user $PODMAN_USER in groups: $(id -nG "$PODMAN_USER")"

log "done. next steps:"
log "  1. bash sv02/scripts/setup-coral.sh"
log "  2. cp sv02/secrets.example/* /srv/sv02/secrets/ && chmod 600 /srv/sv02/secrets/*"
log "  3. Edit /srv/sv02/secrets/*.env with real credentials"
log "  4. Edit sv02/caddy/Caddyfile with real hostname"
log "  5. Create the A record for the hostname (DNS-01 or HTTP-01 with port 80 reachable)"
log "  6. Log out and back in (so docker group takes effect), then:"
log "     sudo bash sv02/scripts/install-docker-service.sh"
log "  7. Install the GitOps sync timer (optional): bash sv02/scripts/install-sync-timer.sh"

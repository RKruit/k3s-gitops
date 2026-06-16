#!/bin/bash
# One-shot host setup for sv02.
#
# Assumes a fresh Debian 12 install with:
#   - A non-root user with sudo (e.g., `rkruit`)
#   - SSH enabled
#   - /opt/sv02-config already populated (clone k3s-gitops there)
#   - This script running as root
#
# Does:
#   1. Installs podman + dependencies
#   2. Creates the storage directories (/srv/sv02/...)
#   3. Sets up the SATA disk (partitions + formats + fstab)
#   4. Installs the Coral USB autosuspend fix (host-level)
#   5. Creates a `podman` user for rootless podman
#   6. Enables lingering for the podman user
#
# Does NOT:
#   - Install libedgetpu / libusb (that's setup-coral.sh)
#   - Touch secrets/ contents (you do that)
#   - Start any services (that's bootstrap.sh)
set -euo pipefail

REPO_DIR=/opt/sv02-config
# Use the user who invoked sudo (via $SUDO_USER) as the rootless
# podman user. Falls back to $PODMAN_USER / 'podman' for non-sudo
# invocations. The default $PODMAN_USER=podman with uid 1000 conflicts
# with the first non-root user on most Debian installs (uid 1000 is
# always the install-time user), so we default to SUDO_USER in practice.
PODMAN_USER=${PODMAN_USER:-${SUDO_USER:-podman}}
PODMAN_UID=${PODMAN_UID:-$(id -u "$PODMAN_USER" 2>/dev/null || echo 1000)}

if [[ $EUID -ne 0 ]]; then
    echo "must run as root" >&2
    exit 1
fi

if [[ ! -d "$REPO_DIR/sv02" ]]; then
    echo "REPO_DIR/sv02 not found; clone k3s-gitops to $REPO_DIR first" >&2
    exit 1
fi

log() { echo "[setup-host] $(date -Iseconds) $*"; }

# === 1. Install podman + dependencies ===
log "installing podman + dependencies"
if ! command -v podman &>/dev/null; then
    apt-get update
    apt-get install -y \
        podman \
        uidmap \
        fuse-overlayfs \
        slirp4netns \
        build-essential \
        libusb-1.0-0 \
        udev \
        systemd \
        git \
        curl
else
    log "podman already installed, skipping apt"
fi

# === 2. Storage directories ===
# All directories under /srv/sv02/ are owned by the podman user
# (rkruit for sudo-mode installs) so the rootless podman container
# can write into the bind-mounts without permission errors.
log "creating storage directories"
install -d -m 755 /srv/sv02
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/frigate
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/frigate/config
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/frigate/db
# frigate-media is created/mounted below
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/mosquitto
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/mosquitto/data
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/caddy
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/caddy/data
install -d -m 700 -o "$PODMAN_USER" -g "$PODMAN_USER" /srv/sv02/secrets

# === 3. SATA disk setup ===
# Set NO_DISK=1 in the environment to skip this section entirely
# (use the root filesystem for Frigate media - fine for homelab, less
# safe than a separate disk, but avoids a hard exit on hosts with no
# dedicated recording drive).
if [[ -n "${NO_DISK:-}" ]]; then
    log "NO_DISK=1 set; using root filesystem for Frigate media"
    install -d -m 755 /srv/sv02/frigate/media
else
# The 1 TB SATA disk. We expect it to be /dev/sda (or whatever the
# user has). We DON'T blindly format — we look for an existing
# partition labeled 'frigate-media' first; if absent, we look for
# an unlabeled disk with no partition table and offer to format.
#
# The "safe mode" default: only auto-format if the disk is
# completely empty (no partition table, no filesystem signature).
# Otherwise, abort and ask the user to run by hand.
DISK=${DISK:-/dev/sda}
LABEL=frigate-media
MOUNT=/srv/sv02/frigate/media

if ! [[ -b "$DISK" ]]; then
    log "no block device at $DISK; skipping disk setup"
    log "set DISK=/dev/sdX in the environment to specify the disk"
else
    if blkid -L "$LABEL" >/dev/null 2>&1; then
        PARTITION=$(blkid -L "$LABEL")
        log "found existing $LABEL partition at $PARTITION"
    elif blkid -p "$DISK" 2>/dev/null | grep -q 'PTTYPE'; then
        log "disk $DISK has an existing partition table; NOT auto-formatting"
        log "to format by hand:"
        log "  parted $DISK mklabel gpt"
        log "  parted $DISK mkpart primary ext4 0% 100%"
        log "  mkfs.ext4 -L $LABEL ${DISK}1"
        log "  # then add to fstab: LABEL=$LABEL $MOUNT ext4 nofail,x-systemd.device-timeout=10 0 2"
        exit 1
    else
        log "no partition table on $DISK; creating one"
        parted "$DISK" mklabel gpt
        parted "$DISK" mkpart primary ext4 0% 100%
        PARTITION="${DISK}1"
        mkfs.ext4 -L "$LABEL" "$PARTITION"
        log "formatted $PARTITION as ext4 with label $LABEL"
    fi

    # Mountpoint
    install -d -m 755 "$MOUNT"
    # fstab entry (idempotent)
    if ! grep -q "LABEL=$LABEL" /etc/fstab; then
        echo "LABEL=$LABEL $MOUNT ext4 nofail,x-systemd.device-timeout=10 0 2" >> /etc/fstab
        log "added fstab entry for $LABEL"
    fi
    # Mount now
    mount "$MOUNT" || log "mount failed; will retry on next boot"
fi

fi

# === 4. Coral USB autosuspend fix (host-level) ===
# The Coral USB drops to bootloader mode (1a6e:089a -> 18d1:9302)
# when the kernel suspends its USB port. The fix is host-level:
# disable USB autosuspend globally.
#
# We set BOTH:
#   - /sys/module/usbcore/parameters/autosuspend = -1 (kernel module param,
#     disables autosuspend for all USB devices)
#   - power/control = "on" on every USB device (each device's own
#     suspend state, separate from the module param)
#   - power/autosuspend = -1 on the Coral's port (per-device, belt +
#     suspenders in case the global module param is reset)
#
# The /etc/modules-load.d/ file only takes effect at next boot, so we
# also apply the kernel module param live below (idempotent: re-applying
# the same value is a no-op).
log "installing USB autosuspend fix"
cat > /etc/udev/rules.d/50-usb-power.rules <<'EOF'
# Disable USB autosuspend for all devices
# Required for Google Coral USB Accelerator (1a6e:089a) — autosuspend
# causes the stick to drop to bootloader mode (18d1:9302).
# Also catches the powered-hub power-cycling issue by keeping hubs awake.
ACTION=="add", SUBSYSTEM=="usb", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", ATTR{power/autosuspend}="-1"
EOF
echo "options usbcore autosuspend=-1" > /etc/modules-load.d/usbcore-autosuspend.conf

# Apply live so the fix takes effect immediately, not just at next boot
echo -1 > /sys/module/usbcore/parameters/autosuspend 2>/dev/null || true
# Note: this writes to a kernel parameter that's already loaded.
# If this fails (e.g., readonly after kexec), a reboot will pick it up.

udevadm control --reload
udevadm trigger --action=add --subsystem-match=usb || true

# Verify
log "USB autosuspend: $(cat /sys/module/usbcore/parameters/autosuspend)"
for hub in /sys/bus/usb/devices/usb*/power/control; do
    log "  $hub: $(cat $hub)"
done

# === 5. libedgetpu library staging (just create the dir; the actual
#        install is in setup-coral.sh) ===
install -d -m 755 /opt/coral-libs

# === 6. Create the podman user for rootless container execution ===
# In sudo mode, $SUDO_USER is the real user (rkruit). We re-use that
# account for rootless podman — no separate user is needed for a
# single-tenant homelab. If the user doesn't exist (non-sudo mode),
# create it.
if ! id "$PODMAN_USER" &>/dev/null; then
    log "creating user $PODMAN_USER"
    useradd -m -u "$PODMAN_UID" -s /bin/bash "$PODMAN_USER"
else
    log "reusing existing user $PODMAN_USER (uid $PODMAN_UID) for rootless podman"
fi

# === 7. subuid/subgid for the podman user ===
# /etc/subuid and /etc/subgid control the user-namespace mapping for
# rootless podman. The podman user gets 65536 uids starting at 100000
# (so the in-container root is mapped to host uid 100000).
if ! grep -q "^${PODMAN_USER}:" /etc/subuid; then
    echo "${PODMAN_USER}:100000:65536" >> /etc/subuid
fi
if ! grep -q "^${PODMAN_USER}:" /etc/subgid; then
    echo "${PODMAN_USER}:100000:65536" >> /etc/subgid
fi

# === 8. Enable lingering for the podman user ===
# Without `loginctl enable-linger`, the user manager (and thus
# `systemctl --user`) only runs while the user is logged in. With
# it, systemd manages the user instance at boot.
loginctl enable-linger "$PODMAN_USER" || log "loginctl enable-linger failed (non-fatal)"

# === 8b. Allow unprivileged binding to ports 80/443 ===
# Rootless podman refuses to bind to ports below 1024 by default
# (CAP_NET_BIND_SERVICE is a root capability). Setting
# `net.ipv4.ip_unprivileged_port_start=80` lets the unprivileged
# `podman` user bind directly to :80 and :443, which Caddy needs
# for the Let's Encrypt HTTP-01 challenge.
#
# This is the standard pattern for homelab rootless podman
# deployments that need to serve on the standard HTTP/HTTPS ports.
echo "net.ipv4.ip_unprivileged_port_start=80" \
    > /etc/sysctl.d/99-podman-unprivileged-ports.conf
sysctl --system

# === 8c. Make sure the sysctl actually applied ===
# `sysctl --system` is silent on no-op, but we want to confirm
# the unprivileged port start is at 80 (not the default 1024) so
# Caddy's PublishPort=80:80 works.
if [[ "$(sysctl -n net.ipv4.ip_unprivileged_port_start)" -gt 80 ]]; then
    log "ERROR: net.ipv4.ip_unprivileged_port_start is still $(sysctl -n net.ipv4.ip_unprivileged_port_start), not 80"
    log "Caddy will not be able to bind to port 80. Reboot and re-run."
fi

# === 9. Ensure the real user can sudo ===
if ! command -v sudo >/dev/null; then
    apt-get install -y sudo
fi
REAL_USER=${SUDO_USER:-${REAL_USER:-}}
if [[ -n "$REAL_USER" ]] && ! groups "$REAL_USER" | grep -q '\bsudo\b'; then
    usermod -aG sudo "$REAL_USER"
    log "added $REAL_USER to sudo group"
fi

log "host setup complete"
log "next steps:"
log "  1. bash $REPO_DIR/sv02/scripts/setup-coral.sh"
log "  2. cp $REPO_DIR/sv02/secrets.example/* /srv/sv02/secrets/"
log "  3. chmod 600 /srv/sv02/secrets/*"
log "  4. \$EDITOR /srv/sv02/secrets/*.env"
log "  5. bash $REPO_DIR/sv02/scripts/bootstrap.sh"
log "  6. bash $REPO_DIR/sv02/scripts/install-sync-timer.sh"
log "  7. systemctl --user start caddy mosquitto frigate (as $PODMAN_USER)"

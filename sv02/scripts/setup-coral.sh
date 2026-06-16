#!/bin/bash
# Install Coral USB runtime libraries on the host.
# Stages the bookworm-compatible libedgetpu + libusb into
# /opt/coral-libs/ for Frigate's container to bind-mount.
#
# This is the Bookworm build of libedgetpu 16.0-tf2.19.1, which
# has glibc 2.34 — matching the Frigate image's Debian Bookworm
# base. The Ubuntu 24.04 build (used on sv01) would NOT work here
# because it needs glibc 2.38, which is too new for the Frigate
# image. This is exactly the libstdc++ skew the coral-usb skill
# warns about.
set -euo pipefail

REPO_DIR=/opt/sv02-config
STAGE_DIR=/opt/coral-libs
LIBEDGETPU_VERSION=16.0TF2.19.1-1
LIBEDGETPU_BASE="https://github.com/feranick/libedgetpu/releases/download/${LIBEDGETPU_VERSION}"

# Detect host distro. The .deb we want has to match the host's glibc
# (we're going to bind-mount the .so into a Bookworm-based Frigate
# container). The available feranick builds are:
#   - ubuntu24.04 (needs glibc 2.38) — too new for Bookworm
#   - debian12   (needs glibc 2.34) — matches Bookworm
#   - debian13   (if available, same glibc 2.41 as Trixie, but
#     the container's Bookworm is the constraint, not the host)
# For the Frigate container (which is Bookworm-based), we want the
# debian12 build regardless of host. So we hardcode it.
DEB_SUFFIX=debian12_amd64

log() { echo "[setup-coral] $(date -Iseconds) $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "must run as root" >&2
    exit 1
fi

install -d -m 755 "$STAGE_DIR"
cd /tmp

# === libedgetpu (Bookworm build) ===
# Filename: libedgetpu1-std_16.0tf2.19.1-1.debian12_amd64.deb
# We use the debian12 build because that's what matches the
# Frigate container's Debian Bookworm base (glibc 2.34). The
# host being Trixie doesn't matter — what matters is the
# container's glibc, because the .so we stage will be loaded
# inside the container.
DEB=libedgetpu1-std_${LIBEDGETPU_VERSION}.${DEB_SUFFIX}.deb
if [[ ! -f "$DEB" ]]; then
    log "downloading $DEB"
    curl -fLO "$LIBEDGETPU_BASE/$DEB"
fi
log "extracting libedgetpu"
dpkg-deb -x "$DEB" extracted/

# The deb installs to:
#   ./lib/x86_64-linux-gnu/libedgetpu.so.1
#   ./lib/x86_64-linux-gnu/libedgetpu.so.1.0.0
#   ./lib/udev/rules.d/60-libedgetpu1-std.rules
#   ./usr/lib/x86_64-linux-gnu/libedgetpu.so (symlink)
cp -v extracted/lib/x86_64-linux-gnu/libedgetpu.so.1.0.0 "$STAGE_DIR/libedgetpu.so.1"

# === libusb (extract from the system, since the Frigate image's
#    libusb-1.0.so.0 is already glibc 2.34 compatible) ===
# We DO NOT need to stage libusb separately — we can extract it
# from the Frigate image at build time. But for first-boot
# convenience, copy the host's libusb into the stage dir.
LIBSUSB_SRC=/lib/x86_64-linux-gnu/libusb-1.0.so.0
if [[ -f "$LIBSUSB_SRC" ]]; then
    cp -v "$LIBSUSB_SRC" "$STAGE_DIR/libusb-1.0.so.0"
else
    log "WARN: $LIBSUSB_SRC not found; install libusb-1.0-0"
    apt-get install -y libusb-1.0-0
    cp -v "$LIBSUSB_SRC" "$STAGE_DIR/libusb-1.0.so.0"
fi

# === Permissions ===
chmod 755 "$STAGE_DIR"/*.so*
chown root:root "$STAGE_DIR"/*.so*

# === udev rule (for the Coral) ===
# The Bookworm deb installs the rule to ./lib/udev/rules.d/60-libedgetpu1-std.rules
# but extracting with dpkg-deb -x doesn't run postinst. Copy it by hand.
if [[ -f extracted/lib/udev/rules.d/60-libedgetpu1-std.rules ]]; then
    install -m 644 extracted/lib/udev/rules.d/60-libedgetpu1-std.rules \
        /lib/udev/rules.d/60-libedgetpu1-std.rules
    udevadm control --reload
    log "installed udev rule for Coral"
fi

# === Verify ===
if [[ ! -f "$STAGE_DIR/libedgetpu.so.1" ]]; then
    log "ERROR: $STAGE_DIR/libedgetpu.so.1 not present after install"
    exit 1
fi

log "Coral libraries staged at $STAGE_DIR:"
ls -la "$STAGE_DIR"
log "verify with: lsusb | grep '1a6e:089a'"
log "plug in the Coral; it should show up as runtime (089a, not 9302)"

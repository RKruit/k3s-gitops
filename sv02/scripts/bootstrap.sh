#!/bin/bash
# Bootstrap sv02: symlink the GitOps repo into the Quadlet search
# path, install the Caddyfile symlink, and reload systemd.
set -euo pipefail

REPO_DIR=/opt/sv02-config
PODMAN_USER=podman

log() { echo "[bootstrap] $(date -Iseconds) $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "must run as root" >&2
    exit 1
fi

# === 1. Symlink Quadlet search path ===
QUADLET_DIR=/etc/containers/systemd
install -d -m 755 "$QUADLET_DIR"
# The Quadlet search path is /etc/containers/systemd (for system
# units) AND ~/.config/containers/systemd (for user units, when
# using `systemctl --user`). We use the user-level path because
# we're running rootless.
USER_QUADLET_DIR=/home/$PODMAN_USER/.config/containers/systemd
install -d -m 755 -o "$PODMAN_USER" -g "$PODMAN_USER" "$USER_QUADLET_DIR"

# Symlink each Quadlet file (preserves in-place editing of the repo)
for f in "$REPO_DIR/sv02/quadlets/"*.container \
         "$REPO_DIR/sv02/quadlets/"*.volume \
         "$REPO_DIR/sv02/quadlets/"*.network; do
    [[ -f "$f" ]] || continue
    name=$(basename "$f")
    ln -sf "$f" "$USER_QUADLET_DIR/$name"
    chown -h "$PODMAN_USER:$PODMAN_USER" "$USER_QUADLET_DIR/$name"
done

# === 2. Symlink Caddyfile ===
# Caddy reads /etc/caddy/Caddyfile by default in the container.
# The container's /etc/caddy is bind-mounted from the host's
# /etc/caddy (see caddy.container), and we symlink the host's
# Caddyfile at the GitOps repo.
install -d -m 755 /etc/caddy
ln -sf "$REPO_DIR/sv02/caddy/Caddyfile" /etc/caddy/Caddyfile

# === 3. Reload systemd user instance for the podman user ===
loginctl enable-linger "$PODMAN_USER" 2>/dev/null || true
sudo -u "$PODMAN_USER" \
    XDG_RUNTIME_DIR="/run/user/$(id -u "$PODMAN_USER")" \
    systemctl --user daemon-reload

log "bootstrap complete"
log "quadlets available in $USER_QUADLET_DIR (visible to `systemctl --user` as $PODMAN_USER)"
log "Caddyfile symlinked at /etc/caddy/Caddyfile"
log "next: bash $REPO_DIR/sv02/scripts/install-sync-timer.sh"
log "then: sudo -u $PODMAN_USER systemctl --user start caddy mosquitto frigate"

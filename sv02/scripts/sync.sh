#!/bin/bash
# sv02 GitOps sync
# Polls the k3s-gitops repo, detects changes, and restarts the
# relevant systemd units.
#
# Runs as root (from a systemd timer). Operates on the rootless
# podman user's Quadlet search path via a sudo call — adjust
# `PODMAN_USER` if your rootless user is different.
set -euo pipefail

REPO_DIR=/opt/sv02-config
PODMAN_USER=podman
LOG_PREFIX="[sv02-sync]"

log() { echo "$LOG_PREFIX $(date -Iseconds) $*"; }

# 1. Pull the latest
if ! git -C "$REPO_DIR" pull --ff-only 2>/dev/null; then
    log "git pull failed; skipping sync"
    exit 0
fi

# 2. Compute hashes of relevant paths BEFORE we know what's changed
QUADLETS_DIR=$REPO_DIR/sv02/quadlets
CADDY_DIR=$REPO_DIR/sv02/caddy
CONFIG_DIR=$REPO_DIR/sv02/config

declare -A BEFORE_AFTER

# Quadlet files: .container, .volume, .network
for f in "$QUADLETS_DIR"/*; do
    [[ -f "$f" ]] || continue
    name=$(basename "$f")
    BEFORE_AFTER[$name]=$(sha256sum "$f" | awk '{print $1}')
done

# Caddyfile
BEFORE_AFTER[Caddyfile]=$(sha256sum "$CADDY_DIR/Caddyfile" | awk '{print $1}')

# Frigate config
BEFORE_AFTER[frigate.yml]=$(sha256sum "$CONFIG_DIR/frigate/frigate.yml" | awk '{print $1}')

# 3. Detect what changed by comparing to the last-known state
#    (persisted in /var/lib/sv02-sync/last-sync.sha256sum)
STATE_FILE=/var/lib/sv02-sync/last-sync.sha256sum
mkdir -p "$(dirname "$STATE_FILE")"
touch "$STATE_FILE"

CHANGED=()
while IFS=$'\t' read -r name old_hash; do
    [[ -z "$name" ]] && continue
    new_hash=${BEFORE_AFTER[$name]:-}
    if [[ "$new_hash" != "$old_hash" ]]; then
        CHANGED+=("$name")
    fi
done < "$STATE_FILE"

# If nothing was in the state file, treat all current files as
# "potentially changed" so the first run after bootstrap does the
# right thing.
if [[ ! -s "$STATE_FILE" ]]; then
    log "first run, treating all files as changed"
    CHANGED=("$(ls "$QUADLETS_DIR" | tr '\n' ' ')" Caddyfile frigate.yml)
fi

if [[ ${#CHANGED[@]} -eq 0 ]]; then
    log "no changes"
    exit 0
fi

log "changed: ${CHANGED[*]}"

# 4. Apply changes
NEED_DAEMON_RELOAD=false
NEED_CADDY_RELOAD=false
RESTART_UNITS=()

for name in "${CHANGED[@]}"; do
    case "$name" in
        *.container|*.network)
            # Symlink the file into the Quadlet search path
            # (it's actually a symlink, see bootstrap.sh, but
            # the path under the search path is what counts)
            NEED_DAEMON_RELOAD=true
            unit="${name%.container}"
            unit="${unit%.network}"
            RESTART_UNITS+=("$unit")
            ;;
        Caddyfile)
            # Caddy reads /etc/caddy/Caddyfile on every reload
            NEED_CADDY_RELOAD=true
            ;;
        frigate.yml)
            # Frigate's config is mounted from a host path, so a
            # file change is picked up on container restart. We
            # don't auto-restart Frigate on config change — too
            # disruptive for an NVR (recordings gap, detection
            # reset). Just log a reminder; restart manually with
            # `systemctl --user restart frigate` after verifying
            # the YAML.
            log "frigate.yml changed — restart Frigate manually:"
            log "  sudo -u $PODMAN_USER XDG_RUNTIME_DIR=/run/user/$(id -u $PODMAN_USER) systemctl --user restart frigate"
            ;;
    esac
done

# 5. Reload systemd (picks up new/changed Quadlets)
if $NEED_DAEMON_RELOAD; then
    log "reloading systemd user instance for $PODMAN_USER"
    sudo -u "$PODMAN_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$PODMAN_USER")" \
        systemctl --user daemon-reload
fi

# 6. Restart changed Quadlet units
for unit in "${RESTART_UNITS[@]}"; do
    # Skip if the unit isn't currently loaded (avoids trying to
    # restart things that haven't been started yet)
    if sudo -u "$PODMAN_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$PODMAN_USER")" \
        systemctl --user is-active --quiet "$unit" 2>/dev/null; then
        log "restarting $unit"
        sudo -u "$PODMAN_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$PODMAN_USER")" \
            systemctl --user try-restart "$unit" || log "  restart of $unit failed (non-fatal)"
    else
        log "skipping restart of $unit (not active)"
    fi
done

# 7. Reload Caddy (if its config changed)
if $NEED_CADDY_RELOAD; then
    log "reloading caddy"
    sudo -u "$PODMAN_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$PODMAN_USER")" \
        systemctl --user reload caddy || log "  caddy reload failed (non-fatal)"
fi

# 8. Save the new state
{
    for name in "${!BEFORE_AFTER[@]}"; do
        printf "%s\t%s\n" "$name" "${BEFORE_AFTER[$name]}"
    done
} > "$STATE_FILE.tmp"
mv "$STATE_FILE.tmp" "$STATE_FILE"

log "sync complete"

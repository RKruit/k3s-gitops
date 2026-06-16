#!/bin/bash
# GitOps sync for sv02.
#
# Pulls the latest sv02/ config from GitHub, detects what changed,
# and restarts the affected containers.
#
# Replaces the podman-era per-Quadlet restart logic with a single
# `docker compose up -d` (compose will only recreate changed services).
#
# On any change to sv02/ (Quadlet files, scripts, secrets, frigate
# config, Caddyfile), we re-render and run `docker compose up -d`.
#
# On a change to sv02/secrets/* (env files), we re-render mosquitto
# by restarting the mosquitto-init + mosquitto services.
#
# On a change to sv02/caddy/Caddyfile, we just `up -d` caddy.
#
# Runs as root via sv02-sync.service.

set -euo pipefail
chmod +x "$0" 2>/dev/null || true

REPO_DIR=/opt/sv02-config
COMPOSE_FILE="$REPO_DIR/sv02/docker-compose.yml"
LOG_TAG="sv02-sync"

log() { logger -t "$LOG_TAG" "$*"; echo "[$LOG_TAG] $*"; }
fatal() { log "FATAL: $*"; exit 1; }

[[ -d "$REPO_DIR/.git" ]] || fatal "$REPO_DIR is not a git repo"

cd "$REPO_DIR"

# Capture the current HEAD so we can detect what changed
OLD_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "none")

log "pulling from origin/main"
if ! git pull --ff-only origin main 2>&1 | tee /tmp/sv02-sync.log; then
    # If fast-forward failed, someone (you) force-pushed or we're
    # behind. Try a hard reset to origin/main (last resort).
    log "ff-only failed, hard-resetting to origin/main"
    git fetch origin main
    git reset --hard origin/main
fi

NEW_HEAD=$(git rev-parse HEAD)
if [[ "$OLD_HEAD" == "$NEW_HEAD" ]]; then
    log "no changes, exiting"
    exit 0
fi

log "synced $OLD_HEAD -> $NEW_HEAD"

# Make sure scripts are executable (git can reset mode bits on pull)
chmod +x "$REPO_DIR/sv02/scripts/"*.sh

# What changed? If anything in sv02/ changed, restart the affected
# services. The simplest correct behavior is: if anything changed,
# run `docker compose up -d` and let compose figure out what to
# recreate. We do NOT touch the secrets/ dir on the host (those
# are 600, owned by rkruit, and the env_file directive in compose
# reads them at container-start time only — so a change to a secret
# file requires restarting the affected service).

CHANGED=$(git diff --name-only "$OLD_HEAD" "$NEW_HEAD" -- sv02/ 2>/dev/null || true)

if [[ -z "$CHANGED" ]]; then
    log "no changes in sv02/, exiting"
    exit 0
fi

log "changed files:"
for f in $CHANGED; do log "  $f"; done

# Always do `docker compose up -d` — compose is smart enough to
# only recreate what's actually different.
cd "$REPO_DIR/sv02"
if docker compose --file "$COMPOSE_FILE" up -d --remove-orphans 2>&1 | tee -a /tmp/sv02-sync.log; then
    log "compose up succeeded"
else
    log "compose up FAILED, see /tmp/sv02-sync.log"
    exit 1
fi

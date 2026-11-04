#!/usr/bin/env bash
# sv01: dagelijkse image-update — floating tags pullen + apply.
# Exclusies: caddy (lokaal build via repo Dockerfile), hermes + egress-proxy
# (bewust handmatig — update-runbook services/hermes.md). Wordt gedraaid door de
# systemd-unit sv01-image-update.timer (dagelijks 04:17 + jitter).
# NIET VERGETEN bij een nieuwe service: exclusielijst hieronder bijwerken als
# die bewust niet automatisch geüpdatet mag worden.
set -euo pipefail

cd /opt/sv01-config/sv01/compose
COMPOSE=(/usr/bin/docker compose --env-file /srv/sv01-secrets/sv01.env --project-name sv01 -f docker-compose.yml)

# services die NIET automatisch geüpdatet mogen worden
EXCL='^(hermes|egress-proxy|caddy)$'
SERVICES=$("${COMPOSE[@]}" config --services | grep -vEv "$EXCL" || true)

if [ -n "$SERVICES" ]; then
  # shellcheck disable=SC2086
  "${COMPOSE[@]}" pull $SERVICES
fi
# recreate alleen containers waarvan de image/config is veranderd
"${COMPOSE[@]}" up -d --remove-orphans
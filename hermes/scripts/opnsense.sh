#!/bin/bash
# OPNsense management via API
# Credentials: /srv/sv01-secrets/opnsense.env (root 600; OPNSENSE_KEY / OPNSENSE_SECRET / OPNSENSE_URL)

ENV_FILE="/srv/sv01-secrets/opnsense.env"
OPNSENSE_URL="https://192.168.68.1"

load_creds() {
  eval "$(sudo -n cat "$ENV_FILE" 2>/dev/null || cat "$ENV_FILE")"
  [ -n "$OPNSENSE_KEY" ] && [ -n "$OPNSENSE_SECRET" ] || { echo "no creds readable in $ENV_FILE" >&2; return 1; }
}

api() {
  load_creds
  curl -sk "${OPNSENSE_URL}${1}" -u "$OPNSENSE_KEY:$OPNSENSE_SECRET" "${@:2}"
}

case "$1" in
  status)          api "/api/core/firmware/status" ;;
  system)          api "/api/core/system/status" ;;
  reboot)          api "/api/core/firmware/reboot" -X POST ;;
  upgrade)         api "/api/core/firmware/upgrade" -X POST ;;
  list-interfaces) api "/api/interfaces/overview/search" ;;
  list-rules)      api "/api/firewall/filter/search" ;;
  api)
    [ -n "$2" ] || { echo "Usage: $0 api <endpoint> [curl args]"; exit 1; }
    api "$2" "${@:3}"
    ;;
  *) echo "Usage: $0 {status|system|reboot|upgrade|list-interfaces|list-rules|api <endpoint>}" ;;
esac
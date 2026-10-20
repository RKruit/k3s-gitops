#!/usr/bin/env bash
# GitOps-trigger: vraagt Komodo om de k3s-gitops sync te draaien.
# Komodo: clone + diff + auto-deploy (deploy=true in komodo.sync.toml) + caddy reload (post_deploy).
set -euo pipefail
source /srv/sv01-secrets/komodo-api.env
K="--resolve komodo.oostrandpark.com:443:127.0.0.1 https://komodo.oostrandpark.com"
curl -fsS $K -H "X-Api-Key: $KOMODO_API_KEY" -H "X-Api-Secret: $KOMODO_API_SECRET" \
  -X POST https://komodo.oostrandpark.com/execute/RunSync \
  -H 'Content-Type: application/json' -d '{"sync":"k3s-gitops"}' >/dev/null
# compose-inhoud-wijzigingen (hash-diff) deployen; no-op als niets veranderd
curl -fsS $K -H "X-Api-Key: $KOMODO_API_KEY" -H "X-Api-Secret: $KOMODO_API_SECRET" \
  -X POST https://komodo.oostrandpark.com/execute/DeployStackIfChanged \
  -H 'Content-Type: application/json' -d '{"stack":"sv01"}' >/dev/null
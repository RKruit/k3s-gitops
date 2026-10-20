#!/usr/bin/env bash
# sv01 bootstrap — idempotent; draai met sudo
set -euo pipefail

# 1. Docker
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2
systemctl enable --now docker

# 2. rkruit mag compose draaien (sync-unit draait als rkruit)
usermod -aG docker rkruit || true

# 3. Systemd units uit de repo installeren
install -m 644 /opt/sv01-config/sv01/systemd/sv01-config-sync.service /etc/systemd/system/
install -m 644 /opt/sv01-config/sv01/systemd/sv01-config-sync.timer /etc/systemd/system/
systemctl daemon-reload

# 4. Timer activeren + eerste directe sync (pull + compose up)
systemctl enable --now sv01-config-sync.timer
systemctl restart sv01-config-sync.service

echo "bootstrap klaar: $(systemctl is-active sv01-config-sync.timer) timer, $(docker ps --format '{{.Names}}: {{.Status}}' | tr '\n' ' ')"
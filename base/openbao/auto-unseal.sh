#!/bin/sh
# Auto-unseal sidecar for OpenBao
# Reads the unseal key from the mounted secret and unseals OpenBao when it starts sealed.

KEY_FILE="/unseal-key/unseal-key"
BAO_ADDR="http://127.0.0.1:8200"
MAX_RETRIES=30
RETRY_DELAY=5

# Wait for OpenBao to at least be listening
echo "[autounseal] Waiting for OpenBao to start..."
for i in $(seq 1 $MAX_RETRIES); do
    if wget -q -O- --timeout=3 "${BAO_ADDR}/v1/sys/health?droperationok=true" 2>/dev/null | grep -q '"initialized"'; then
        echo "[autounseal] OpenBao is responding"
        break
    fi
    echo "[autounseal] Waiting... ($i/$MAX_RETRIES)"
    sleep $RETRY_DELAY
done

# Read the unseal key
if [ ! -f "$KEY_FILE" ]; then
    echo "[autounseal] ERROR: Unseal key file not found at $KEY_FILE"
    exit 1
fi

UNSEAL_KEY=$(cat "$KEY_FILE")
if [ -z "$UNSEAL_KEY" ]; then
    echo "[autounseal] ERROR: Unseal key is empty"
    exit 1
fi

echo "[autounseal] Unseal key loaded, attempting to unseal..."

# Poll until unsealed (OpenBao may still be initializing)
for i in $(seq 1 $MAX_RETRIES); do
    STATUS=$(wget -q -O- --timeout=5 "${BAO_ADDR}/v1/sys/seal-status" 2>/dev/null)
    if echo "$STATUS" | grep -q '"sealed":false'; then
        echo "[autounseal] OpenBao is already unsealed!"
        exit 0
    fi

    echo "[autounseal] OpenBao is sealed, attempting unseal... ($i/$MAX_RETRIES)"
    wget -q -O- --timeout=5 --post-data="{\"key\":\"$UNSEAL_KEY\"}" \
        "${BAO_ADDR}/v1/sys/unseal" 2>/dev/null | grep -o '"sealed":[^,}]*'

    sleep 2
done

echo "[autounseal] WARNING: Gave up waiting for OpenBao to unseal. Will retry on next check."
exit 0
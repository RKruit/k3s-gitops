#!/bin/bash
# Render the Mosquitto config + password file on disk.
# Called by mosquitto.container's ExecStartPre.
#
# Reads MQTT_USER and MQTT_PASSWORD from /srv/sv02/secrets/mosquitto.env,
# writes /srv/sv02/mosquitto/data/mosquitto.conf and passwd.
#
# The passwd is hashed with a one-shot `podman run` because the host
# doesn't have mosquitto_passwd installed (and we don't want to install
# it just for this).
set -euo pipefail

ENV_FILE=/srv/sv02/secrets/mosquitto.env
CONFDIR=/srv/sv02/mosquitto/data

# Sanity check
[[ -r "$ENV_FILE" ]] || { echo "[render-mqtt-passwd] FATAL: cannot read $ENV_FILE" >&2; exit 1; }
# Ensure the dir exists and is world-readable so the mosquitto
# container (running as uid 1883) can read the conf and passwd
# files inside.
install -d -m 755 "$CONFDIR"
chmod 755 "$CONFDIR"

# Load env vars (KEY=value lines, # comments)
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
: "${MQTT_USER:?MQTT_USER not set in $ENV_FILE}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD not set in $ENV_FILE}"

# Render the mosquitto.conf
cat > "$CONFDIR/mosquitto.conf" <<EOF
listener 1883
allow_anonymous false
password_file $CONFDIR/passwd
persistence false
log_dest stdout
log_type all
connection_messages true
EOF
chmod 644 "$CONFDIR/mosquitto.conf"
# passwd file is 644 so the mosquitto container user (uid 1883) can read it
# (it doesn't need to write). The hash itself doesn't grant auth.
chmod 644 "$CONFDIR/passwd"

# Render the passwd file by running mosquitto_passwd in a throwaway
# container. The passwd format is "user:password_hash" where the hash
# is bcrypt (libmosquitto's default).
rm -f "$CONFDIR/passwd"
podman run --rm \
    --network=none \
    -v "$CONFDIR:/work" \
    --entrypoint /bin/sh \
    docker.io/library/eclipse-mosquitto:2.0 \
    -c "/usr/bin/mosquitto_passwd -b -c /work/passwd '$MQTT_USER' '$MQTT_PASSWORD'"
chmod 600 "$CONFDIR/passwd"
chown rkruit:rkruit "$CONFDIR/passwd"

echo "[render-mqtt-passwd] rendered $CONFDIR/mosquitto.conf and passwd for user $MQTT_USER"

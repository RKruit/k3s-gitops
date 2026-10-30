# sv01 Mosquitto — host-native MQTT broker (phase 2)

This broker is deliberately **not** a container. Ubuntu's AppArmor machinery
confines the `mosquitto` binary to a fixed set of paths —
`/etc/mosquitto`, `/var/lib/mosquitto`, `/var/log/mosquitto`, `/run/mosquitto`
(profile name: `mosquitto`; verified 2026-10-30 via audit logs: every
`mosquitto` exec on this host, containerized or host-side, gets `open` denied
outside those paths with `Error: Unable to open config file`). A bind-mounted
`/mosquitto/config` inside a container is therefore never readable by the
broker, regardless of image tag (tested: eclipse-mosquitto 2.1.2, 2.0.22,
alpine's own package).

## Files (canonical in git → deployed to)

| repo | deployed | role |
|---|---|---|
| `sv01/mosquitto/00-sv01.conf` | `/etc/mosquitto/conf.d/00-sv01.conf` | listener 1883 on 192.168.1.10, auth, ACL, logging |
| `sv01/mosquitto/acl.file` | `/etc/mosquitto/acl.file` (`mosquitto:mosquitto` 640) | `homey` → `homey/#` only; `hermes` → everything |
| `/srv/sv01-secrets/mosquitto.pw` | `/etc/mosquitto/passwd` (`mosquitto:mosquitto` 640) | hashed credentials (2 users) |

Secrets (plain, one-time view for client setup): `/srv/sv01-secrets/mosquitto.env`
(`HOMEY_MQTT_PASS`, `HERMES_MQTT_PASS`) — outside git.

## Deploy a config change

```bash
sudo cp sv01/mosquitto/00-sv01.conf /etc/mosquitto/conf.d/00-sv01.conf
sudo cp sv01/mosquitto/acl.file /etc/mosquitto/acl.file && sudo chown mosquitto:mosquitto /etc/mosquitto/acl.file
sudo systemctl restart mosquitto
```

## Rotating a client password

```bash
sudo bash -c 'source /srv/sv01-secrets/mosquitto.env; <edit env first> /usr/bin/mosquitto_passwd -b /etc/mosquitto/passwd homey "$HOMEY_MQTT_PASS"'
sudo cp /etc/mosquitto/passwd /srv/sv01-secrets/mosquitto.pw   # keep staging in sync
sudo systemctl restart mosquitto
```

## Data

`persistence.db` lives in `/var/lib/mosquitto` (AppArmor path). This is a
deliberate deviation from the usual NFS-for-persistence rule: the broker's DB
is retained-message cache only, and the AppArmor confinement pins the path.
If NFS is ever required, it needs a mount (or bind) exactly at
`/var/lib/mosquitto` — not a redirect.
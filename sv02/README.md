# sv02 — podman + Quadlet homelab server

Replaces the Talos k3s node that was running here. Host is Debian 12, rootless
podman with Quadlet for service definitions, Caddy for TLS.

## Layout

```
sv02/
├── README.md                     # this file
├── .gitignore                    # ignore secrets/
├── quadlets/                     # committed, deployed to /etc/containers/systemd/
│   ├── caddy.container
│   ├── caddy.volume              # named-volume declaration for certs
│   ├── frigate.container
│   ├── frigate-config.volume     # bind-mount for /config (frigate.yml lives here)
│   ├── frigate-media.volume      # bind-mount for /media (recordings on the SATA disk)
│   ├── frigate-db.volume         # bind-mount for /db (frigate.db)
│   ├── mosquitto.container
│   ├── mqtt-data.volume
│   └── sv02.network              # rootless podman network (10.89.0.0/24)
├── caddy/
│   └── Caddyfile                 # committed, deployed to /etc/caddy/Caddyfile
├── config/
│   └── frigate/
│       └── frigate.yml           # committed, deployed to /srv/sv02/frigate/config/
├── scripts/
│   ├── setup-host.sh             # one-shot: libedgetpu, udev, autosuspend, fstab, dirs
│   ├── setup-coral.sh            # one-shot: install bookworm libedgetpu, stage .so files
│   ├── bootstrap.sh              # initial git clone + symlink into Quadlet search path
│   ├── sync.sh                   # the git pull → daemon-reload → restart-changed
│   └── install-sync-timer.sh     # installs the systemd timer/service for sync.sh
└── secrets.example/              # committed, documents the layout; actual files are NOT
    │   ├── frigate-cam.env          # FRIGATE_CAM_USER, FRIGATE_CAM_PASS
    │   ├── frigate-mqtt.env         # FRIGATE_MQTT_USER, FRIGATE_MQTT_PASSWORD
    │   └── mosquitto.env            # MQTT_USER, MQTT_PASSWORD (must match frigate-mqtt.env)
```

## Host paths

| Path | Owner | Purpose |
|---|---|---|
| `/opt/sv02-config` | root, mode 755 | clone of `k3s-gitops/sv02/`. `sync.sh` does `git pull` here. |
| `/etc/containers/systemd/` | root, mode 755 | symlink → `/opt/sv02-config/quadlets/`. Quadlet search path. |
| `/etc/caddy/Caddyfile` | root | symlink → `/opt/sv02-config/caddy/Caddyfile`. |
| `/srv/sv02/frigate/config/` | 100000:100000 (rootless mapping) | bind-mounted into Frigate as `/config/`. Contains `frigate.yml`. |
| `/srv/sv02/frigate/media/` | 100000:100000 | bind-mounted as `/media/`. Recordings. **Should be on the SATA disk.** |
| `/srv/sv02/frigate/db/` | 100000:100000 | bind-mounted as `/db/`. `frigate.db`. |
| `/srv/sv02/mosquitto/data/` | 100000:100000 | bind-mounted. `passwd` file + ephemeral state. |
| `/srv/sv02/secrets/` | 100000:100000, mode 700 | env files for container env injection. **NOT in Git.** |

## Storage

The 1 TB SATA disk should be:
- partitioned with one ext4 partition
- labelled `frigate-media` (so the fstab entry uses `LABEL=frigate-media`)
- mounted at `/srv/sv02/frigate/media/` (NOT a separate mountpoint — see setup-host.sh for the bind mount trick)
- in fstab with `nofail,x-systemd.device-timeout=10` so the system boots even if the disk is missing

## GitOps sync

`scripts/sync.sh` runs every 5 min (systemd timer). It:
1. `git pull` in `/opt/sv02-config`
2. copies changed `quadlets/*.container` and `quadlets/*.volume` and `quadlets/*.network` into place (they're already in the search path, so this is a no-op for unchanged files)
3. runs `systemctl --user daemon-reload` and `systemctl --user try-restart <changed-unit>` for any changed Quadlet
4. `validate --noexit` on the Caddyfile and `systemctl --user reload caddy` if it changed

This is intentionally simpler than the k3s setup — there's no controller loop, just a polling sync.

## Bootstrapping a fresh sv02

```bash
# 1. Install Debian 12 (netinst is fine), set up a non-root user, sudo, SSH.
# 2. As root:
apt install -y podman uidmap fuse-overlayfs slirp4netns
# 3. Clone the repo to /opt/sv02-config:
git clone https://github.com/RKruit/k3s-gitops.git /opt/sv02-config
# 4. Run the host setup script:
bash /opt/sv02-config/sv02/scripts/setup-host.sh
# 5. (Optional) Set up the Coral USB:
bash /opt/sv02-config/sv02/scripts/setup-coral.sh
# 6. Create the secrets directory and env files:
install -d -m 700 /srv/sv02/secrets
cp /opt/sv02-config/sv02/secrets.example/* /srv/sv02/secrets/
chmod 600 /srv/sv02/secrets/*
$EDITOR /srv/sv02/secrets/frigate-cam.env   # set your camera creds
$EDITOR /srv/sv02/secrets/frigate-mqtt.env  # set MQTT user/pass (must match mosquitto.env)
$EDITOR /srv/sv02/secrets/mosquitto.env     # set MQTT_USER, MQTT_PASSWORD
# 7. Set up rootless podman for the `podman` user (or whichever non-root user runs the containers):
#    - This script assumes user `podman` with subuid/subgid mappings
#    - loginctl enable-linger podman
# 8. Bootstrap:
bash /opt/sv02-config/sv02/scripts/bootstrap.sh
bash /opt/sv02-config/sv02/scripts/install-sync-timer.sh
# 9. DNS: create A records for the hostnames in Caddyfile pointing at this server
# 10. systemctl --user start caddy frigate mosquitto
```

## Pitfalls

- **Caddy needs port 80** for Let's Encrypt HTTP-01 challenge. If you're behind NAT, port-forward 80 + 443 to this host.
- **Frigate's Coral access needs `/dev/bus/usb` mounted rw** plus the .so files. The Quadlet uses the same pattern as the k3s deployment: privileged + bind-mounts.
- **First-boot Coral state**: even with the autosuspend fix, plug the stick in before starting Frigate the first time. If it comes up as 18d1:9302, run `mdt reset` from a Debian container (see coral-usb skill).
- **SealedSecret → env file mapping**: the k3s deployment used SealedSecrets for camera + MQTT creds. On podman, those become host-side env files at `/srv/sv02/secrets/`. The Quadlets reference them via `EnvironmentFile=`.
- **Rootless user mapping**: when rootless podman bind-mounts `/srv/sv02/frigate/media/`, files written by the container appear as `100000:100000` on the host (the rootless user's "root" inside the userns). This is normal and expected; the disk still works fine for `rsync` to a backup target.

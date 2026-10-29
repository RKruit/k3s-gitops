---
title: NFS storage
type: infra
description: "NFS storage pattern (local-driver volumes + nfs4 opts), live sv01 volume mapping, safe new-subdir creation."
related: [runbooks/new-host.md, services/todo-app.md]
---

# NFS storage

Server `192.168.1.2`, export `/volume1/k8sv2` (open to 192.168.1.0/24).

## The pattern

Docker volumes with the `local` driver + `nfs4` `driver_opts`:

```yaml
volumes:
  my-data:
    driver: local
    driver_opts:
      type: nfs4
      o: addr=192.168.1.2,rw,hard,timeo=600
      device: ":/volume1/k8sv2/<dir>"
```

Robert's standing preference: **persistent data belongs on NFS** — default to it unless there's a specific reason not to.

## Live mapping (sv01)

| Volume | NFS dir | Why this dir |
|---|---|---|
| `todo-data` | `todo-app-todo-app-data-pvc-f673b41b-3742-45ab-81b1-aa8cf6c73588` | same dir as the old k3s PVC — keeps data continuous |
| `hermes-data` | `hermes-data` | |
| `kuma-data` | `kuma-data` | |
| `beszel-data` | `beszel-data` | |
| `beszel-agent-data` | `beszel-agent-data` | |
| `waha-data` | `waha-data` | WhatsApp bridge session store |
| `beszel-socket` | *(local — not NFS)* | unix socket, must be a local FS |
| `komodo-mongo-*` | *(local — not NFS)* | wiredTiger-on-NFS risk |

## Creating a new NFS dir safely

A missing subdir under `device:` gets auto-created **empty by docker with wrong perms** (`0:0 mode 000` on the mountroot) → containers crashloop. Create + chown first:

```bash
# quick: on sv01
sudo mount -t nfs4 -o rw,hard,timeo=600 192.168.1.2:/volume1/k8sv2 /mnt/prep
sudo mkdir -p /mnt/prep/<dir>
sudo chmod 777 /mnt/prep/<dir>          # open up for the mountroot
sudo umount /mnt/prep
```

Then, if the consumer runs as UID 1000 (hermes etc.), chown the contents via the docker volume:

```bash
docker run --rm -v sv01_<volume>:/h alpine sh -c 'chown 1000:1000 /h && chmod 755 /h'
```

!!! tip
    `hard,timeo=600` is deliberate: a stalled NAS must block I/O, not silently zero out data.
---
description: "Private GHCR image at todo.oostrandpark.com; NFS data continuity with the old k3s PVC dir, image update path."
related:
  - infra/nfs-storage.md
  - runbooks/deploy.md
---

# todo-app

`ghcr.io/rkruit/todo-app:latest` (private image) at https://todo.oostrandpark.com.

- Data on NFS, pointed at the **same directory the old k3s PVC used**: `/volume1/k8sv2/todo-app-todo-app-data-pvc-f673b41b-3742-45ab-81b1-aa8cf6c73588`. Data continuity survives the k3s removal — never "clean up" that oddly-named NFS dir.
- `TZ=Europe/Amsterdam`.
- Image pull auth: rkruit's `docker login ghcr.io` (credentials in `/srv/sv01-secrets/ghcr.txt`; periphery gets access via the `~/.docker` ro-mount).
- Updates: push a new `latest` to GHCR, then `docker compose pull` (the timer does **not** re-pull `latest`), or bundle a compose-file change so the sync recreates it.
- Old data backup before the compose migration: `/srv/sv01-backup/todo-app-data-20261014.tar.gz`.
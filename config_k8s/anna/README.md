# di-anna website

Personal styling site for Anna Gigante (Lelystad, NL) — rebuilt fresh from
[www.di-anna.nl](https://www.di-anna.nl), served from a single nginx pod.

## How it's deployed

- **HTML + CSS** live in `configmap-html.yaml` as a single ConfigMap
  (`anna-html`), mounted into nginx at `/usr/share/nginx/html` (full
  directory mount — no `subPath`, so the files are live-updated by
  `kubectl rollout restart`).
- **`default.conf`** lives in `configmap-nginx.yaml` (ConfigMap
  `anna-nginx-config`), mounted at `/etc/nginx/conf.d/default.conf`
  via `subPath` because nginx requires a single file at that path.
- **TLS** is issued by cert-manager via the `letsencrypt-prod`
  ClusterIssuer → `anna-tls` Secret in this namespace.
- **Ingress** is a `networking.k8s.io/v1` Ingress with class `traefik`,
  terminating on `websecure` for `anna.oostrandpark.com`.

## Editing content

1. Edit the `data.index.html` or `data.style.css` block in
   `configmap-html.yaml`.
2. Recompute the checksum:

   ```bash
   HASH=$( (sha256sum configmap-html.yaml configmap-nginx.yaml \
            | awk '{print $1}') | sha256sum | cut -c1-64)
   ```

3. Replace the `checksum/config` annotation in `deployment.yaml` with
   `$HASH`.
4. Commit, push, and ArgoCD will roll the deployment (which forces a
   fresh mount of the ConfigMap and nginx picks up the new files).

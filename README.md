# k3s GitOps

Git-managed Kubernetes manifests for the home lab.

## Structure

```
base/
  openbao/          # OpenBao secrets manager
```

## Apply to cluster

```bash
kubectl apply -k base/openbao
```

## First-time setup

1. Clone this repo
2. Run `kubectl apply -k base/openbao`
3. OpenBao will be available at `http://<node-ip>:30820`

## Adding new apps

1. Add manifests under `base/<app-name>/`
2. Commit and push
3. ArgoCD/Flux will auto-sync (when configured)

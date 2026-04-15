# k3s GitOps

Git-managed Kubernetes manifests for the home lab.

## Structure

```
bootstrap/           # ArgoCD bootstrap (run once)
  kustomization.yaml # Kustomize to deploy ArgoCD + apps
argocd-install/      # ArgoCD install manifests (v2.14.3)
argocd/              # ArgoCD AppProject + Application
base/                # Cluster apps
  netdata/           # Monitoring & metrics
  n8n/               # Workflow automation
  uptime-kuma/       # Uptime monitoring
```

## Apps

| App | URL | Port | Notes |
|-----|-----|------|-------|
| ArgoCD | https://argocd.oostrandpark.com | 30880 | GitOps engine |
| n8n | https://n8n.oostrandpark.com | 5678 | Workflow automation |
| Uptime Kuma | https://kuma.oostrandpark.com | 3002 | Uptime monitoring |
| Netdata | https://netdata.oostrandpark.com | 19999 | System monitoring |

Traefik (ingress) + cert-manager (TLS) handle routing and certificates.

## Quick Start

### 1. Clone this repo
```bash
git clone https://github.com/RKruit/k3s-gitops
cd k3s-gitops
```

### 2. Bootstrap ArgoCD (run once)
```bash
chmod +x BOOTSTRAP.sh && ./BOOTSTRAP.sh
```

Or manually:
```bash
kubectl create namespace argocd
kubectl apply -n argocd -f argocd-install/install.yaml
kubectl patch svc argocd-server -n argocd -p '{"spec":{"type":"NodePort","ports":[{"port":443,"targetPort":8080,"nodePort":30880}]}}'
```

### 3. Get ArgoCD credentials
```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
# Username: admin
# UI: http://<node-ip>:30880
```

### 4. ArgoCD watches this repo automatically
The `argocd/` Application tells ArgoCD to sync `base/` → cluster.

## GitOps Flow

1. Edit or add manifests in `base/<app>/`
2. Commit and push:
   ```bash
   git add . && git commit -m "Update netdata config" && git push
   ```
3. ArgoCD detects changes → syncs to cluster automatically ✓

## Adding New Apps

1. Add manifests under `base/<app-name>/`
2. Add to ArgoCD Application (`argocd/application.yaml`)
3. Commit → push → ArgoCD syncs automatically

## Repository

https://github.com/RKruit/k3s-gitops

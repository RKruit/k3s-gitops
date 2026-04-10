#!/bin/bash
# ArgoCD Bootstrap Script
# Run once to install ArgoCD from GitOps
set -e

REPO="https://github.com/RKruit/k3s-gitops"
BRANCH="main"

echo "==> Cloning repo..."
git clone -b $BRANCH $REPO /tmp/k3s-gitops

echo "==> Creating argocd namespace..."
kubectl apply -f /tmp/k3s-gitops/argocd-install/install.yaml

echo "==> Patching argocd-server to NodePort..."
kubectl patch svc argocd-server -n argocd -p '{"spec":{"type":"NodePort","ports":[{"port":443,"targetPort":8080,"nodePort":30880}]}}'

echo "==> Waiting for ArgoCD to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=120s

echo "==> Getting admin password..."
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d

echo "==> Done! ArgoCD UI: http://<node-ip>:30880"

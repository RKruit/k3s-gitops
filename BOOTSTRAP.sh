#!/bin/bash
# ArgoCD Bootstrap Script
# Run once on a fresh cluster to install ArgoCD and set up GitOps
set -e

REPO="https://github.com/RKruit/k3s-gitops"
BRANCH="main"
BOOTSTRAP_DIR="/tmp/k3s-gitops"

echo "==> Cloning repo..."
if [ -d "$BOOTSTRAP_DIR" ]; then
  cd $BOOTSTRAP_DIR && git pull
else
  git clone -b $BRANCH $REPO $BOOTSTRAP_DIR
fi

echo "==> Installing ArgoCD..."
kubectl create namespace argocd || true
kubectl apply -n argocd -f $BOOTSTRAP_DIR/argocd-install/install.yaml

echo "==> Waiting for ArgoCD server to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=120s

echo "==> Patching argocd-server to NodePort 30880..."
kubectl patch svc argocd-server -n argocd -p '{"spec":{"type":"NodePort","ports":[{"port":443,"targetPort":8080,"nodePort":30880}]}}'

echo "==> Applying bootstrap Application (self-managing GitOps)..."
kubectl apply -f $BOOTSTRAP_DIR/bootstrap/argocd-bootstrap.yaml

echo ""
echo "==> ArgoCD is ready!"
echo ""
ADMIN_PASS=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)
echo "  UI:       http://<node-ip>:30880"
echo "  User:     admin"
echo "  Password: $ADMIN_PASS"
echo ""
echo "==> ArgoCD will now auto-sync all apps in base/ from Git!"
echo "==> Any push to the repo will trigger automatic cluster updates."

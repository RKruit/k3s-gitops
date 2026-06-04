# Coral USB Accelerator — hostPath + libedgetpu shared runtime

This directory deploys a Coral-aware namespace with two test harnesses
on sv01 (the k3s node that has the Coral USB Accelerator plugged in).

## What it does

- **No device plugin.** A Coral workload is just a pod on a node that:
  - has the label `coral=present` (added to sv01 by `argocd/argocd-app-coral.yaml`'s
    post-install JSON patch — but for now, do it manually as in step 1 below)
  - mount-binds `/dev/bus/usb` (rw) and `/sys/bus/usb` (ro) from the host
  - mount-binds `/lib/x86_64-linux-gnu/libedgetpu.so.1` and `/usr/lib/x86_64-linux-gnu`
    (which provides `libusb-1.0.so.0`) from the host
  - calls `ai-edge-litert`'s `load_delegate("libedgetpu.so.1")` to drive the
    TPU directly via libusb

This is the **libusb-based** runtime — we do NOT use the in-kernel
`gasket`/`apex` drivers. They were dropped from the kernel when Google
archived the Coral project; the userspace libedgetpu library talks to the
device over libusb and works on any modern Linux.

## Prerequisites on sv01

Already done in this setup:

1. `sudo apt install -y libusb-1.0-0` — needed for libedgetpu's libusb path
2. Install `libedgetpu1-std` 16.0-tf2.19.1 deb from
   `https://github.com/feranick/libedgetpu/releases/download/16.0TF2.19.1-1/libedgetpu1-std_16.0tf2.19.1-1.ubuntu24.04_amd64.deb`
   (provides `/lib/x86_64-linux-gnu/libedgetpu.so.1`)
3. The udev rule `/lib/udev/rules.d/60-libedgetpu1-std.rules` from the deb
   sets `GROUP="plugdev"` on the Coral USB nodes

## One-time node label

Mark sv01 as having a Coral:

```bash
kubectl label node sv01 coral=present --overwrite
```

The DaemonSet in this directory and any future Coral workloads use
`nodeSelector: { coral: present }` to land on sv01.

## What's in here

| File | Purpose |
|---|---|
| `namespace.yaml` | The `coral` namespace |
| `configmap-test-script.yaml` | The `test.py` and `run-test.sh` inference harness |
| `pod-test-inference.yaml` | Long-lived scratch pod for interactive testing |
| `job-test-inference.yaml` | One-shot Job that prints "OK Coral working" and exits |
| `kustomization.yaml` | Kustomize manifest list |

## Usage

### One-shot verification (recommended)

```bash
kubectl logs -n coral -f job/coral-test-run
# expect: "OK Coral USB Accelerator working from inside the pod"
# expect: "avg latency: ~3-15 ms over 10 runs"
```

If the Job gets pruned by `ttlSecondsAfterFinished: 3600` (1 hour) and you
want to re-run, just delete the Job — ArgoCD will recreate it.

### Interactive shell

```bash
kubectl exec -n coral -it coral-test -- /bin/bash
# inside the shell:
pip install --break-system-packages ai-edge-litert numpy
python3 /workspace/coral/test.py
```

### Building a Coral-aware workload

In your own Deployment, copy the volume + volumeMount set from
`job-test-inference.yaml` — that's the whole trick. The four mounts are:

```yaml
volumeMounts:
  - name: coral-libs     # libedgetpu.so.1
    mountPath: /usr/local/lib/coral
    readOnly: true
  - name: usr-lib        # libusb-1.0.so.0
    mountPath: /usr/lib/x86_64-linux-gnu
    readOnly: true
  - name: usb-dev        # /dev/bus/usb
    mountPath: /dev/bus/usb
  - name: usb-sys        # /sys/bus/usb
    mountPath: /sys/bus/usb
    readOnly: true
volumes:
  - name: coral-libs
    hostPath: { path: /lib/x86_64-linux-gnu/libedgetpu.so.1, type: File }
  - name: usr-lib
    hostPath: { path: /usr/lib/x86_64-linux-gnu, type: Directory }
  - name: usb-dev
    hostPath: { path: /dev/bus/usb, type: Directory }
  - name: usb-sys
    hostPath: { path: /sys/bus/usb, type: Directory }
```

Add `nodeSelector: { coral: present }` to schedule on sv01.

## Limitations

- **No device plugin, no `resources.limits`:** the scheduler can't reason
  about Coral availability. Two pods can both think they have the device
  and both try to drive it, which works (each one gets a turn) but isn't
  exclusive. If you need exclusive access, use a Deployment with
  `replicas: 1` and a leader-elect lock, or revisit a real device plugin.
- **No hot-plug awareness:** the manifests use `nodeSelector`, not a
  dynamic resource. If you move the Coral stick to a different USB port
  on sv01 it just works (same bus, same device). If you move it to a
  *different node*, re-label that node and remove the label from sv01.
- **K3s + libedgetpu + USB:** works on k3s v1.34.6 (sv01's version).
  Tested with the 16.0-tf2.19.1 release. Newer releases should also work.

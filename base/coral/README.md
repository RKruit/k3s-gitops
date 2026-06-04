# Coral USB Accelerator — hostPath + libedgetpu shared runtime

This directory deploys a Coral-aware namespace with two test harnesses
on sv01 (the k3s node that has the Coral USB Accelerator plugged in).

## What it does

- **No device plugin.** A Coral workload is just a pod on a node that:
  - has the label `coral=present` (added to sv01 manually, see step 1)
  - runs with `securityContext.privileged: true` so it can open USB
    character devices (k3s denies this by default with EPERM)
  - sets `LD_LIBRARY_PATH=/usr/local/lib` to find `libedgetpu.so.1` and
    `libusb-1.0.so.0`
  - mount-binds `/dev/bus/usb` (rw) and `/sys/bus/usb` (ro) from the host
  - mount-binds `/lib/x86_64-linux-gnu/libedgetpu.so.1` and
    `/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0` from the host as files
    (not directories) so they land at exactly the right basename

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

Pods and Jobs in this directory use `nodeSelector: { coral: present }`
to land on sv01.

## What's in here

| File | Purpose |
|---|---|
| `namespace.yaml` | The `coral` namespace |
| `configmap-test-script.yaml` | The `test.py` inference harness |
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

In your own Deployment/StatefulSet, copy the volume + volumeMount set from
`job-test-inference.yaml` — that's the whole trick. The key points:

```yaml
env:
  - name: LD_LIBRARY_PATH
    value: /usr/local/lib:$LD_LIBRARY_PATH
securityContext:
  privileged: true                       # k3s denies USB without this
volumeMounts:
  - name: libedgetpu                     # file-as-mount at the right basename
    mountPath: /usr/local/lib/libedgetpu.so.1
    readOnly: true
  - name: libusb
    mountPath: /usr/local/lib/libusb-1.0.so.0
    readOnly: true
  - name: usb-dev                        # /dev/bus/usb (rw)
    mountPath: /dev/bus/usb
  - name: usb-sys                        # /sys/bus/usb (ro)
    mountPath: /sys/bus/usb
    readOnly: true
volumes:
  - name: libedgetpu
    hostPath: { path: /lib/x86_64-linux-gnu/libedgetpu.so.1, type: File }
  - name: libusb
    hostPath: { path: /usr/lib/x86_64-linux-gnu/libusb-1.0.so.0, type: File }
  - name: usb-dev
    hostPath: { path: /dev/bus/usb, type: Directory }
  - name: usb-sys
    hostPath: { path: /sys/bus/usb, type: Directory }
```

Add `nodeSelector: { coral: present }` to schedule on sv01.

## Why each part is needed

| Component | Reason |
|---|---|
| `privileged: true` | k3s denies `EPERM` when non-privileged containers try to open USB character devices. The device cgroup / capabilities aren't enough. |
| `LD_LIBRARY_PATH=/usr/local/lib` | The `libedgetpu.so.1` we bind-mount lands at `/usr/local/lib/libedgetpu.so.1`; the dynamic linker needs to be told to look there. |
| `type: File` hostPaths | Mounting a file gives you that file at that exact path. Mounting the parent directory doesn't expose the file with its real basename. |
| `nodeSelector: coral=present` | So the scheduler doesn't try to put it on a node without the Coral. |
| `/dev/bus/usb` rw, `/sys/bus/usb` ro | libedgetpu opens the USB endpoint for I/O; libusb reads sysfs for device metadata. |

## Limitations

- **No device plugin, no `resources.limits`:** the scheduler can't reason
  about Coral availability. Two pods can both think they have the device
  and both try to drive it, which works (each one gets a turn) but isn't
  exclusive. If you need exclusive access, use a Deployment with
  `replicas: 1` and a leader-elect lock.
- **No hot-plug awareness:** the manifests use `nodeSelector`, not a
  dynamic resource. If you move the Coral stick to a different USB port
  on sv01 it just works (same bus, same device). If you move it to a
  *different node*, re-label that node and remove the label from sv01.
- **`privileged: true`:** the security model here is "homelab, single
  tenant". On a multi-tenant cluster, isolate Coral workloads to a
  dedicated namespace with a `Restricted` Pod Security Admission, or use
  a more granular approach (USB device cgroup BPF program).

## Verifying the host setup is correct

If inference stops working, check these on sv01:

```bash
# 1. Coral is visible to the kernel
lsusb | grep "1a6e:089a\|18d1:9302"

# 2. libedgetpu is installed
ldconfig -p | grep edgetpu

# 3. udev rule applied to the device node
ls -la /dev/bus/usb/002/
# expect: crw-rw-r-- root plugdev ... 004

# 4. Inference works on the host itself
python3 -c "
import ai_edge_litert.interpreter as itp
import numpy as np
d = itp.load_delegate('libedgetpu.so.1')
i = itp.Interpreter(model_path='/home/rkruit/coral/models/mobilenet_v1_1.0_224_quant_edgetpu.tflite', experimental_delegates=[d])
i.allocate_tensors()
i.set_tensor(i.get_input_details()[0]['index'], np.random.randint(0,255,size=i.get_input_details()[0]['shape'],dtype=np.uint8))
i.invoke()
print('host OK')
"
```

If 1-4 all pass, the cluster setup is fine; the problem is in the pod spec.

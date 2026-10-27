#!/usr/bin/env python3
"""GitHub push-webhook receiver — trigger voor host-side sv01-deploys.

Architectuur (zie docs/ en skill compose-gitops):
  GitHub push -> https://hooks.oostrandpark.com (Cloudflare Tunnel, cloudflared
  container, host-net) -> 127.0.0.1:9100 -> receiver -> systemctl restart
  sv01-config-sync.service (git pull + compose up, zelfde pad als de timer).

- Alleen POST met geldige X-Hub-Signature-256 (HMAC-SHA256, GH_WEBHOOK_SECRET uit env).
- Alleen event=push en branch main; ping event krijgt 200 (GitHub-webhook-setup-check).
- Bindt bewust 127.0.0.1: het endpoint is alleen via de tunnel bereikbaar,
  host-net Kuma kan de healthcheck nog wél op loopback monitoren.
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ["GH_WEBHOOK_SECRET"].encode()
SYNC_UNIT = "sv01-config-sync.service"
PORT = int(os.environ.get("GH_WEBHOOK_PORT", "9100"))
BRANCH = "refs/heads/main"


def trigger_sync():
    subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", "restart", SYNC_UNIT],
        check=True, timeout=30,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - basestate-paramnaam
        print("%s - %s" % (self.address_string(), format % args), flush=True)

    def _reply(self, code, msg):
        self.send_response(code)
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg.encode())

    def do_GET(self):
        self._reply(200, "ok")

    def do_POST(self):
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            sig = self.headers.get("X-Hub-Signature-256", "")
            mac = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac, sig):
                self._reply(403, "bad signature")
                return
            event = self.headers.get("X-GitHub-Event", "")
            if event == "ping":
                print("ping ok", flush=True)
                self._reply(200, "ping ok")
                return
            if event != "push":
                self._reply(200, "ignored event")
                return
            ref = json.loads(body).get("ref", "")
            if ref != BRANCH:
                print("ignored branch: %s" % ref, flush=True)
                self._reply(200, "ignored branch")
                return
            commit = json.loads(body).get("after", "")[:7]
            trigger_sync()
            print("deploy triggered for %s (commit %s)" % (ref, commit), flush=True)
            self._reply(200, "deploy triggered")
        except Exception as e:  # noqa: BLE001 - receiver mag nooit crashen op 1 request
            print("error: %r" % e, file=sys.stderr, flush=True)
            try:
                self._reply(500, "error")
            except Exception:
                pass


if __name__ == "__main__":
    print("gh-webhook receiver op 127.0.0.1:%d (unit: %s)" % (PORT, SYNC_UNIT), flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
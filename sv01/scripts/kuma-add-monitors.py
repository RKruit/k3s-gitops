#!/usr/bin/env python3
"""Uptime Kuma v2 headless monitor-set (idempotent).

Run: ~/.hermes/venvs/monitoring/bin/python scripts/kuma-add-monitors.py
Creds: /srv/sv01-secrets/kuma-admin.env (KUMA_ADMIN_USER / KUMA_ADMIN_PASSWORD).
v2.5.x socket-API: setup = tuple-args, login = dict, add = (monitor,).
Verplicht in elke monitor-payload: conditions: [] + accepted_statuscodes.
sv02 bewust NIET in de set; alleen uitbreiden op Roberts verzoek.
"""
import json
import subprocess, sys, time
import socketio

COMMON = {
    "interval": 60, "maxretries": 3, "retryInterval": 60,
    "accepted_statuscodes": ["200-299"], "ignoreTls": True,
    "conditions": [],
}

MONITORS = [
    dict(COMMON, type="http", name="Komodo UI", url="https://komodo.oostrandpark.com/"),
    dict(COMMON, type="http", name="Todo-app", url="https://todo.oostrandpark.com/healthz"),
    dict(COMMON, type="http", name="Beszel hub", url="https://beszel.oostrandpark.com/"),
    dict(COMMON, type="http", name="Docs site", url="https://docs.oostrandpark.com/"),
    dict(COMMON, type="http", name="Homepage", url="https://homepage.oostrandpark.com/"),
    dict(COMMON, type="http", name="IT-Tools", url="https://tools.oostrandpark.com/"),
    dict(COMMON, type="http", name="Authelia", url="https://auth.oostrandpark.com/api/health"),
    dict(COMMON, type="http", name="GitHub webhook (hooks)", url="https://hooks.oostrandpark.com/"),
    dict(COMMON, type="http", name="Uptime Kuma", url="http://127.0.0.1:3001/",
         accepted_statuscodes=["200-299", "302-302"]),
    dict(COMMON, type="http", name="Hindsight API", url="http://127.0.0.1:9177/health",
         keyword="healthy"),
    dict(COMMON, type="http", name="Ollama", url="http://127.0.0.1:11434/"),
    dict(COMMON, type="port", name="PostgreSQL (pg0)", hostname="127.0.0.1", port=5432),
    dict(COMMON, type="port", name="WAHA (WhatsApp bridge)", hostname="127.0.0.1", port=3000),
    dict(COMMON, type="port", name="Mosquitto MQTT", hostname="192.168.1.10", port=1883),
    dict(COMMON, type="http", name="Homey energy export", url="http://192.168.1.10:8890/energy.json"),
]


def read_env_file(path):
    out = subprocess.run(["sudo", "cat", path], capture_output=True, text=True).stdout
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def fetch_monitors(sio, wait=2.5):
    box = {}
    @sio.on("monitorList")
    def _on(data):
        box.update(data or {})
    sio.emit("getMonitorList")
    time.sleep(wait)
    return box


creds = read_env_file("/srv/sv01-secrets/kuma-admin.env")
USER = creds.get("KUMA_ADMIN_USER", "hermes-monitoring")
PWD = creds.get("KUMA_ADMIN_PASSWORD", "")
if not PWD:
    sys.exit("geen wachtwoord in kuma-admin.env")

sio = socketio.Client()


@sio.event
def connect():
    print("connected", flush=True)


sio.connect("http://127.0.0.1:3001", wait_timeout=15)
time.sleep(1.0)

try:
    r = sio.call("setup", (USER, PWD), timeout=10)
    print("setup:", r, flush=True)
except socketio.exceptions.TimeoutError:
    print("setup: geen ack (al geinitialiseerd?) — doorgaan naar login", flush=True)

r = sio.call("login", {"username": USER, "password": PWD, "token": None}, timeout=15)
if not (isinstance(r, dict) and r.get("ok")):
    sys.exit(f"login mislukt: {r}")
print("login ok", flush=True)

existing = {m.get("name") for m in fetch_monitors(sio).values()}
for m in MONITORS:
    if m["name"] in existing:
        print(f"skip {m['name']} (bestaat al)", flush=True)
        continue
    try:
        r = sio.call("add", (m,), timeout=15)
        print("add", m["name"], "->", r, flush=True)
    except Exception as ex:
        print("add", m["name"], "ERR", ex, flush=True)

final = fetch_monitors(sio)

# --- notificaties idempotent linken (les: API-monitors krijgen de
#     isDefault-notificatie NIET automatisch mee) ---
for mname in [m["name"] for m in MONITORS]:
    mon = next((v for v in final.values() if v.get("name") == mname), None)
    if mon is None:
        print(f"notif {mname}: monitor ontbreekt", flush=True)
        continue
    raw = mon.get("notificationIDList")
    lst = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) and raw.strip() else {})
    if lst.get("1"):
        continue
    lst["1"] = True
    mon["notificationIDList"] = lst  # dict — editMonitor verwacht géén json-string
    try:
        r = sio.call("editMonitor", (mon,), timeout=15)
        print("notif link", mname, "->", r, flush=True)
    except Exception as ex:
        print("notif link", mname, "ERR", ex, flush=True)

print("--- monitors:", sorted(final.keys()) if final else "(leeg)", flush=True)
sio.disconnect()

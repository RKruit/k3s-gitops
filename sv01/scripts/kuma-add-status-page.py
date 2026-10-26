#!/usr/bin/env /home/rkruit/.hermes/venvs/monitoring/bin/python3
"""Idempotent: maak/status-update van de Kuma-statuspage 'sv01'.

Run (als root, leest kuma-admin.env): python3 kuma-add-status-page.py [slug]
  - addStatusPage als slug ontbreekt
  - saveStatusPage: group 'Site' met alle HTTP-site-monitors (sendUrl=true)
Kuma v2.5.x socket-API: login = dict {username,password,token:null};
  getMonitorList = emit-only, lijst komt via 'monitorList' push-event.
"""
import json
import os
import sys
import time

import socketio

slug = "sv01"
title = "sv01"
group_name = "Sites"

sio = socketio.Client()
state = {}


def read_env_file(path):
    import subprocess
    out = subprocess.run(["sudo", "cat", path], capture_output=True, text=True).stdout
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


creds = read_env_file("/srv/sv01-secrets/kuma-admin.env")

@sio.on("monitorList")
def on_monitor_list(data):
    state["monitors"] = data

@sio.event
def connect():
    print("connected")

sio.connect("http://127.0.0.1:3001", wait_timeout=15)
time.sleep(1.0)

# --- login (v2.5.x-pad: probeer setup (timeout = al geinitialiseerd), dan login) ---
try:
    sio.call("setup", (creds["KUMA_ADMIN_USER"], creds["KUMA_ADMIN_PASSWORD"]), timeout=10)
except socketio.exceptions.TimeoutError:
    print("setup: geen ack (al geinitializeerd) — doorgaan naar login")

ack = sio.call("login", {"username": creds["KUMA_ADMIN_USER"],
                         "password": creds["KUMA_ADMIN_PASSWORD"],
                         "token": None}, timeout=30)
if not ack.get("ok"):
    sys.exit(f"login failed: {ack}")
print("login ok")

# --- status page bestaat? ---
ack = sio.call("getStatusPage", slug, timeout=15)
if ack.get("ok"):
    print("status page exists already")
else:
    ack = sio.call("addStatusPage", (title, slug), timeout=15)
    if not ack.get("ok"):
        sys.exit(f"addStatusPage failed: {ack}")
    print("status page added:", slug)

# --- monitors ---
sio.emit("getMonitorList")
for _ in range(40):
    if "monitors" in state:
        break
    time.sleep(0.25)
monitors = state.get("monitors")
if monitors is None:
    sys.exit("no monitorList push received")

# alleen HTTP-site-monitors op de publieke status-page (komodo/todo/kuma/beszel/docs/homepage/it-tools)
site_names = ["komodo", "todo", "kuma", "beszel", "docs", "homepage", "it-tools"]
chosen = []
for mid, m in monitors.items():
    name = m.get("name", "")
    if m.get("type") == "http" and any(n in name.lower() for n in site_names):
        chosen.append({"id": int(mid), "name": name})
print("site monitors:", json.dumps(chosen))

if not chosen:
    sys.exit("geen site-monitors gevonden")

# --- opslaan: group + config ---
config = {
    "slug": slug,
    "title": title,
    "description": "sv01 homelab",
    "logo": "",
    "autoRefreshInterval": 300,
    "theme": "auto",
    "showTags": False,
    "footerText": "",
    "customCSS": "",
    "showPoweredBy": False,
    "domainNameList": [],
    "showOnlyLastHeartbeat": True,
    "showCertificateExpiry": True,
    "showClickAbleLink": True,
    "showEndpoint": False,
    "rssTitle": "Uptime Kuma",
    "searchEngineIndex": False,
    "analyticsType": None,
    "analyticsId": None,
    "analyticsScriptUrl": None,
}
public_group_list = [{
    "name": group_name,
    "monitorList": [{"id": c["id"], "sendUrl": True} for c in chosen],
}]

ack = sio.call("saveStatusPage", (slug, config, "", public_group_list), timeout=20)
print("saveStatusPage:", json.dumps(ack))
sio.disconnect()
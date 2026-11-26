#!/usr/bin/env /home/rkruit/.hermes/venvs/monitoring/bin/python3
"""Stuur tekst via de WAHA-bridge (sv01-waha, loopback :3000) naar WhatsApp.

Gebruik:  echo "tekst" | waha-send.py [chatId]
- Zonder chatId-arg: WAHA_CHAT_ID (Roberts self-chat) uit /srv/sv01-secrets/waha.env.
- Tekst op stdin; lege tekst = niks versturen (exit 0).
Drukt het message-id (of een foutmelding) op stdout; exit 0 bij succes, 1 bij falen.
"""
import json
import sys
import urllib.request


def read_env(path):
    d = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    except FileNotFoundError:
        sys.exit(f"waha-send: {path} ontbreekt")
    return d


def main():
    env = read_env("/srv/sv01-secrets/waha.env")
    key = env.get("WAHA_API_KEY", "")
    chat = sys.argv[1] if len(sys.argv) > 1 else env.get("WAHA_CHAT_ID", "")
    text = sys.stdin.read()
    if not text.strip():
        print("waha-send: lege tekst — niets verstuurd")
        return 0
    if not key or not chat:
        sys.exit("waha-send: WAHA_API_KEY of chat-id ontbreekt")
    req = urllib.request.Request(
        "http://127.0.0.1:3000/api/sendText",
        data=json.dumps({"session": "default", "chatId": chat, "text": text}).encode(),
        headers={"Content-Type": "application/json", "X-Api-Key": key},
    )
    try:
        r = json.load(urllib.request.urlopen(req, timeout=30))
        print(r.get("key", {}).get("id", "?"))
        return 0
    except Exception as ex:
        print(f"waha-send: versturing mislukt: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
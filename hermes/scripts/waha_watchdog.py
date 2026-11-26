#!/usr/bin/env python3
"""WAHA-watchdog: herstelt de WhatsApp-sessie als de container 'healthy' is
maar de API hangt of de sessie niet WORKING staat.

Aanleiding 22-11-2026: de sessie viel om 03:40 weg; de container-healthcheck
bleef groen, /api/sessions gaf 500 (Knex pool vol) en de dagrapport-wrappers
konden met hun sessie-restart niets meer (de API hing). Alleen een
container-restart hielp. Dit script doet dat automatisch.

Stil als alles goed is (watchdog-patroon: lege stdout = geen levering).
"""
import json
import subprocess
import time
import urllib.error
import urllib.request

ENV = "/srv/sv01-secrets/waha.env"
API = "http://127.0.0.1:3000"
CONTAINER = "sv01-waha"


def api_key():
    try:
        for line in open(ENV):
            if line.startswith("WAHA_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


KEY = api_key()


def get_sessions(timeout=12):
    """(http_code, status_of_default_session|None)"""
    req = urllib.request.Request(API + "/api/sessions",
                                 headers={"X-Api-Key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
            sessions = data if isinstance(data, list) else data.get("sessions", [])
            for s in sessions:
                if s.get("name") == "default":
                    return 200, s.get("status")
            return 200, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None


def restart_session():
    req = urllib.request.Request(
        API + "/api/sessions/default/restart", method="POST",
        headers={"X-Api-Key": KEY, "Content-Type": "application/json"},
        data=b"{}")
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception:
        return False


def restart_container():
    r = subprocess.run(["docker", "restart", CONTAINER],
                       capture_output=True, text=True, timeout=90)
    return r.returncode == 0


def main():
    code, status = get_sessions()
    if code == 200 and status == "WORKING":
        return  # gezond: stil

    # tweede meting: een reconnect van WhatsApp is geen storing
    time.sleep(20)
    code2, status2 = get_sessions()
    if code2 == 200 and status2 == "WORKING":
        return

    actions = [f"WAHA niet gezond (HTTP {code}/{code2}, status {status}/{status2}) — herstel gestart"]

    # 1) lichte route: sessie herstarten als de API nog antwoordt
    if code2 == 200:
        actions.append("sessie-restart: " + ("ok" if restart_session() else "mislukt"))
        time.sleep(25)
        c, s = get_sessions()
        if c == 200 and s == "WORKING":
            print(" · ".join(actions + ["sessie weer WORKING"]))
            return
    else:
        actions.append("API hangt — sessie-restart overgeslagen")

    # 2) zware route: container herstarten (creds blijven bewaard, geen QR nodig)
    actions.append("container-restart: " + ("ok" if restart_container() else "mislukt"))
    time.sleep(45)
    c, s = get_sessions()
    actions.append(f"na restart: HTTP {c}, status {s}")
    if not (c == 200 and s == "WORKING"):
        actions.append("⚠️ nog steeds niet WORKING — handmatige check nodig")
    print(" · ".join(actions))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # watchdog mag nooit stil stukgaan
        print(f"WAHA-watchdog fout: {e}")

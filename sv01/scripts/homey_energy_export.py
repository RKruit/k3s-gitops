#!/usr/bin/env python3
"""Homey energy exporter (phase 2, 2026-10-30).

Pulls GET /api/manager/energy/live from the hub every INTERVAL seconds and
serves a flattened JSON at http://192.168.1.10:8890/energy.json for the
homepage customapi widget and Uptime Kuma.

Session strategy (rate-limit lesson: EVERY CLI call mints a cloud
delegation, so a 1-minute exporter must never mint per tick):
- steady state: reuse the Homey CLI's cached hub session from
  ~/.athom-cli/settings.json (pure local HTTP, zero cloud calls)
- dead/unreachable session: run ONE `homey` CLI command (the CLI re-auths
  itself and rewrites the cached session), at most once per 10 minutes,
  then reuse the refreshed session
- persistent failure: serve {"error": ...} so the widget fails visibly

Runs as a host-side systemd service (sv01-homey-energy): the refresh path
may touch /srv/sv01-secrets only via rkruit-level access, never from a
container (an in-container refresh_token rotation cannot be persisted and
would burn the OAuth client).
"""
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HID = '66e57ca24da8fc528fb04f47'
HUB = 'http://192.168.68.128'
SCF = '/home/rkruit/.athom-cli/settings.json'
OUT = '/srv/homey-metrics/energy.json'
BIND = ('192.168.1.10', 8890)
INTERVAL = 60
HEAL_COOLDOWN = 600
CTX = urllib.request.build_opener()  # plain http
HTTP_TIMEOUT = 20


def read_session():
    try:
        return json.load(open(SCF))['homeyApi']['homey-' + HID]['token']
    except Exception:
        return None


def hub_get(path, tok):
    h = {'Accept': 'application/json', 'X-Homey-ID': HID, 'Authorization': 'Bearer ' + tok}
    rq = urllib.request.Request(HUB + path, headers=h)
    try:
        r = urllib.request.urlopen(rq, timeout=HTTP_TIMEOUT)
        return r.status, r.read().decode('utf8', 'ignore')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf8', 'ignore')[:300]
    except Exception as e:
        return 0, 'ERR ' + str(e)[:150]


def flatten(o):
    """energy/live -> flat scalars the homepage customapi widget can map."""
    out: dict = {'source': 'homey', 'updated': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    try:
        out['consumed_w'] = (o.get('totalConsumed') or {}).get('W')
        out['generated_w'] = (o.get('totalGenerated') or {}).get('W')
        out['cumulative_w'] = (o.get('totalCumulative') or {}).get('W')
        items = [
            i for i in (o.get('items') or [])
            if isinstance(i, dict) and isinstance(i.get('values'), dict)
            and isinstance(i['values'].get('W'), (int, float))
        ]
        items.sort(key=lambda i: -abs(i['values']['W']))
        for n in (1, 2):
            if n <= len(items):
                i = items[n - 1]
                out['top_%d_name' % n] = str(i.get('name') or '?')
                out['top_%d_w' % n] = float(i['values']['W'])
    except Exception as e:
        out['parse_error'] = str(e)[:100]
    return out


def write_json(obj):
    tmp = OUT + '.tmp'
    try:
        with open(tmp, 'w') as f:
            f.write(json.dumps(obj, ensure_ascii=False))
        os.replace(tmp, OUT)
    except Exception:
        pass


LAST = {'error': 'starting up'}


def tick():
    global LAST
    sess = read_session()
    last_heal = 0
    while True:
        ok = False
        if sess:
            st, body = hub_get('/api/manager/energy/live', sess)
            if st == 200:
                try:
                    flat = flatten(json.loads(body))
                    LAST = flat
                    ok = True
                except Exception:
                    pass
            elif st == 401:
                sess = None
        if not ok:
            err = 'hub unreachable or session expired'
            if time.time() - last_heal > HEAL_COOLDOWN:
                # one CLI command lets the CLI re-auth + refresh the cache
                try:
                    subprocess.run(
                        ['homey', 'api', 'raw', '--path', '/api/manager/system/ping'],
                        capture_output=True, timeout=60)
                except Exception:
                    pass
                last_heal = time.time()
                sess = read_session()
            cur = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            out = dict(LAST) if LAST.get('source') == 'homey' and 'error' not in LAST else {}
            out.update({'source': 'homey', 'updated': cur, 'stale': bool(out), 'error': err})
            write_json(out)
        else:
            write_json(LAST)
        time.sleep(INTERVAL)


def serve():
    handler = partial(SimpleHTTPRequestHandler, directory=os.path.dirname(OUT))
    ThreadingHTTPServer(BIND, handler).serve_forever()


if __name__ == '__main__':
    threading.Thread(target=serve, daemon=True).start()
    tick()
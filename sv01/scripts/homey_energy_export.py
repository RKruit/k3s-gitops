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
HUB = 'http://192.168.68.188'  # ethernet (live 02-11-2026); wifi standby .128
# heal uses an ABSOLUTE path: the service PATH lacks ~/.npm-global/bin where
# the CLI lives — a bare 'homey' raised FileNotFoundError and (uncaught) KILLED
# the tick loop overnight 06-11 (crash-loop for ~16 h: stale 520 W on the PV)
HOMEY_CLI = '/home/rkruit/.npm-global/bin/homey'
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


LAST: dict[str, object] = {'error': 'starting up'}
LAST_NOTE: object = '__none__'


def log(msg):
    print(time.strftime('%H:%M:%S'), msg, flush=True)


def tick():
    global LAST, LAST_NOTE
    sess = read_session()
    last_heal = 0
    while True:
        ok = False
        note = ''
        if sess:
            st, body = hub_get('/api/manager/energy/live', sess)
            if st == 200:
                try:
                    LAST = flatten(json.loads(body))
                    ok = True
                except Exception as e:
                    note = 'HTTP 200 but unparseable: %s' % e
            elif st == 401:
                sess = None
                note = 'HTTP 401: hub session expired/invalid'
            elif st == 0:
                note = 'hub unreachable: %s' % body[:120]
            else:
                note = 'HTTP %s: %s' % (st, body[:120])

        if not ok:
            # one journal line per NEW cause, not per interval
            if note and note != LAST_NOTE:
                log('down: %s' % note)
                LAST_NOTE = note

            if time.time() - last_heal > HEAL_COOLDOWN:
                last_heal = time.time()
                try:
                    healed = subprocess.run(  # one CLI command -> re-auth + cache refresh
                        [HOMEY_CLI, 'api', 'raw', '--path',
                         '/api/manager/system/ping'], capture_output=True, timeout=60)
                    tail = (healed.stdout + healed.stderr).decode('utf8', 'ignore')
                    tail = tail.strip()[:150]
                    sess = read_session()
                    log('heal: exit=%s out=%r -> token %s' % (
                        healed.returncode, tail, 'refreshed' if sess else 'MISSING'))
                except Exception as e:
                    log('heal: CLI failed: %s' % e)
            err = 'hub unreachable or session expired'
            cur = time.strftime('%Y-%m-%dT%H:%M:%S%z')
            out = dict(LAST) if LAST.get('source') == 'homey' and 'error' not in LAST else {}
            out.update({'source': 'homey', 'updated': cur, 'stale': True, 'error': err})
            write_json(out)
        else:
            if LAST_NOTE != '__none__':
                log('recovered after: %s' % LAST_NOTE)
                LAST_NOTE = '__none__'
            # uniform flags on EVERY write: stale/error keys must always be
            # present so keyword monitors + the poller's stale-gate cannot be
            # fooled by key-absence (healthy = '"stale":false' + '"error":null')
            out = dict(LAST)
            out.setdefault('stale', False)
            out['error'] = None
            write_json(out)
        time.sleep(INTERVAL)


def serve():
    handler = partial(SimpleHTTPRequestHandler, directory=os.path.dirname(OUT))
    ThreadingHTTPServer(BIND, handler).serve_forever()


if __name__ == '__main__':
    threading.Thread(target=serve, daemon=True).start()
    tick()
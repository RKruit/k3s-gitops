"""Publishes Homey Logic variables to MQTT (retained) and accepts set-commands.

Topics:
  homey/logic/<slug>            value as text ("dicht", "open", "true", "12.5"), retained
  homey/cmd/logic/<slug>/set    set the variable (payload = waarde, JSON of platte tekst)
"""
import json, os, re, time, logging
import urllib.request, urllib.error
import paho.mqtt.client as mqtt

BASE = os.environ.get("HOMEY_BASE", "http://192.168.68.128")
TOKEN = os.environ["HOMEY_TOKEN"]
POLL = int(os.environ.get("POLL_S", "10"))
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.1.10")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "hasp")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

log = logging.getLogger("homey-logic")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def req(path, method="GET", data=None):
    r = urllib.request.Request(
        BASE + path,
        method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
        data=json.dumps(data).encode() if data is not None else None,
    )
    with urllib.request.urlopen(r, timeout=10) as resp:
        body = resp.read()
        return json.loads(body) if body else None


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


by_slug = {}
last = {}


def poll(client):
    variables = req("/api/manager/logic/variable/") or {}
    for v in variables.values():
        name = v.get("name") or ""
        if name.startswith("__"):
            continue
        s = slug(name)
        if not s:
            continue
        by_slug[s] = v.get("id")
        val = v.get("value")
        if isinstance(val, bool):
            txt = "true" if val else "false"
        elif val is None:
            txt = ""
        else:
            txt = str(val)
        if last.get(s) != txt:
            last[s] = txt
            client.publish("homey/logic/" + s, txt, retain=True)
            log.info("publish homey/logic/%s = %s", s, txt)


def on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    if len(parts) != 5 or parts[0] != "homey" or parts[2] != "logic" or parts[4] != "set":
        return
    s = parts[3]
    vid = by_slug.get(s)
    payload = msg.payload.decode("utf-8", "replace")
    if not vid:
        log.warning("cmd voor onbekende variabele %s", s)
        return
    try:
        data = json.loads(payload)
    except Exception:
        data = payload
    try:
        req("/api/manager/logic/variable/" + vid, method="PUT", data={"value": data})
        log.info("set logic %s = %r", s, data)
        last.pop(s, None)
    except urllib.error.HTTPError as e:
        log.error("set logic %s mislukt: HTTP %s", s, e.code)


def main():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sv01-homey-logic")
    c.username_pw_set(MQTT_USER, MQTT_PASS)
    c.on_message = on_message
    while True:
        try:
            c.connect(MQTT_HOST, MQTT_PORT, 30)
            break
        except Exception as e:
            log.warning("mqtt connect faalde (%s), retry in 10s", e)
            time.sleep(10)
    c.subscribe("homey/cmd/logic/+/set")
    c.loop_start()
    poll(c)
    while True:
        c.loop(1)
        try:
            poll(c)
        except Exception as e:
            log.warning("poll faalde: %s", e)
        time.sleep(POLL)


if __name__ == "__main__":
    main()

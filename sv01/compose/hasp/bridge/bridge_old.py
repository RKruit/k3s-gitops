import json, os, time, logging
import urllib.request
import paho.mqtt.client as mqtt

BASE = os.environ.get("HOMEY_BASE", "http://192.168.68.128")
TOKEN = os.environ["HOMEY_TOKEN"]
POLL = int(os.environ.get("POLL_S", "10"))
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.1.10")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

log = logging.getLogger("homey-bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def req(path, method="GET", data=None):
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
        data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(r, timeout=10) as resp:
        body = resp.read()
        return json.loads(body) if body else None

c = mqtt.Client(client_id="homey-bridge", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
c.username_pw_set(os.environ.get("MQTT_USER", "hasp"), os.environ["MQTT_PASS"])
c.reconnect_delay_set(2, 30)
known = set()

try:
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
except Exception as e:
    log.warning("eerste connect mislukt: %s", e)

def on_connect(client, userdata, flags, reason_code, properties=None):
    client.subscribe("homey/cmd/#", qos=1)
    log.info("MQTT connected (%s), subscribed homey/cmd/#", reason_code)
c.on_connect = on_connect

def on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    try:
        payload = msg.payload.decode()
        if parts[1] == "cmd" and parts[2] == "flow":
            for fid in payload.split(","):
                fid = fid.strip()
                if fid:
                    req("/api/manager/flow/flow/" + fid, "PUT", {"trigger": True})
                    log.info("flow %s triggered", fid[:8])
            return
        dev, cap = parts[2], parts[3]
        val = payload
        try:
            f = float(val); val = int(f) if f.is_integer() else f
        except ValueError:
            if val.lower() in ("true", "false", "on", "off"):
                val = val.lower() in ("true", "on")
            elif val.lower() in ("null", "none", ""):
                val = None
        req(f"/api/manager/devices/device/{dev}/capability/{cap}", "PUT", {"value": val})
        log.info("set %s.%s = %r", dev[:8], cap, val)
    except Exception as e:
        log.error("cmd failed %s: %s", msg.topic, e)
c.on_message = on_message

def snapshot():
    zones = req("/api/manager/zones/zone/")
    zmap = {k: (v.get("name", "?") if isinstance(v, dict) else str(v)) for k, v in zones.items()}
    devs = req("/api/manager/devices/device/")
    out = []
    for d in devs.values():
        caps = {}
        for cap in d.get("capabilities", []):
            o = d.get("capabilitiesObj", {}).get(cap) or {}
            if o.get("value") is not None:
                caps[cap] = o["value"]
        out.append({"id": d.get("id"), "name": d.get("name"),
                    "zone": zmap.get(d.get("zone"), "?"),
                    "available": d.get("available", True), "capability": caps})
    return out

def mqpublish(topic, payload, retain=True):
    try:
        c.publish(topic, payload, retain=retain)
    except Exception as e:
        log.warning("publish mislukt %s: %s", topic, e)

while True:
    # netwerklus + reconnect expliciet in main thread
    try:
        if c.loop(timeout=1) != 0:
            raise ConnectionError(f"loop rc={c.loop(timeout=1)}")
    except Exception as e:
        log.warning("MQTT netwerkprobleem (%s) - reconnect", e)
        try:
            c.reconnect()
        except Exception:
            time.sleep(10)
        continue
    try:
        devs = snapshot()
        new = set()
        for d in devs:
            if not d.get("id"):
                continue
            base = f"homey/state/{d['id']}"
            mqpublish(base, json.dumps(d, separators=(",", ":")))
            new.add(base)
            for cap, val in d["capability"].items():
                t = f"{base}/{cap}"
                v = ("true" if val else "false") if isinstance(val, bool) else str(val)
                mqpublish(t, v)
                new.add(t)
        for t in known - new:
            mqpublish(t, "")
        known = new
        mqpublish("homey/overview",
                  json.dumps({"devices": [{"id": d["id"], "name": d["name"], "zone": d["zone"]}
                                          for d in devs if d.get("id")]}, separators=(",", ":")))
        log.info("Homey poll OK (%d devices)", len(devs))
    except Exception as e:
        log.error("poll failed: %s", e)
    time.sleep(POLL)

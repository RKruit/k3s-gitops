
import json, os, time, logging, threading
import urllib.request
import paho.mqtt.client as mqtt

BASE = os.environ.get("HOMEY_BASE", "http://192.168.68.128")
TOKEN = os.environ["HOMEY_TOKEN"]
POLL = int(os.environ.get("POLL_S", "10"))
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.1.10")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
NODE = "plate1"

log = logging.getLogger("homey-bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def req(path, method="GET", data=None):
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
        data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(r, timeout=10) as resp:
        body = resp.read()
        return json.loads(body) if body else None

DEV_TADO = "a0e669fe"  # Woonkamer - Slimme Thermostaat (id-prefix; volledige id wordt aangevuld runtime)
cache = {}       # devprefix -> cap -> laatste waarde (uit snapshot)
fullids = {}     # devprefix -> volledige device id
synced = {}      # (obj_ref) -> laatste geleverde tekst/waarde

# ---------- layout ----------
def L(page, oid, obj, **kw):
    d = {"page": page, "id": oid, "obj": obj}
    d.update(kw)
    return d

# Portret-layout 240x320 (rotatie 0)
LAYOUT = [
    {"page": 1, "id": 0, "swipe": 1},
    L(1, 1, "label", x=0, y=8, w=240, h=28, text="WOONKAMER"),
    L(1, 10, "label", x=0, y=44, w=240, h=72, align="center"),
    L(1, 11, "label", x=0, y=124, w=240, h=24, align="center"),
    L(1, 22, "label", x=0, y=158, w=240, h=40, align="center"),
    L(1, 21, "btn", x=24, y=212, w=84, h=76, text="-",
      tag={"t": "cap", "dev": "a0e669fe", "cap": "target_temperature", "mode": "delta", "d": -0.5, "min": 5, "max": 30}),
    L(1, 23, "btn", x=132, y=212, w=84, h=76, text="+",
      tag={"t": "cap", "dev": "a0e669fe", "cap": "target_temperature", "mode": "delta", "d": 0.5, "min": 5, "max": 30}),
]
LIGHTS = [
    ("Buislamp", "903aadf9"),
    ("Licht Apple", "93e80449"),
    ("Luchtreiniger", "7003efa2"),
    ("Spot Bijkeuken", "0c1dcf0e"),
]
for i, (naam, devp) in enumerate(LIGHTS):
    col, row = i % 2, i // 2
    x = 8 + col * 118
    y = 44 + row * 62
    LAYOUT.append(
        L(2, 30 + i, "btn", x=x, y=y, w=112, h=56, text=naam, toggle=1,
          tag={"t": "cap", "dev": devp, "cap": "onoff", "mode": "toggle", "sync": "val"}))
LAYOUT += [
    {"page": 2, "id": 0, "swipe": 1},
    L(2, 1, "label", x=0, y=8, w=240, h=28, text="LICHT"),
]

# ---------- helpers ----------
def dev_id(pref):
    return fullids.get(pref)

def cap_value(pref, cap):
    return (cache.get(pref) or {}).get(cap)

def push_line(topic_base, obj):
    payload = json.dumps(obj, separators=(",", ":"))
    c.publish(topic_base + "command/jsonl", payload)

def push_page_clear():
    for pg in (1, 2):
        c.publish(f"hasp/{NODE}/command/p{pg}b0.clear")

def push_layout():
    push_page_clear()
    for obj in LAYOUT:
        push_line(f"hasp/{NODE}/", obj)
    log.info("layout pushed (%d objects)", len(LAYOUT))

def fmt_num(v):
    try:
        f = float(v)
        return f"{f:.1f}".rstrip("0").rstrip(".") if f == int(f) else f"{f:.1f}"
    except Exception:
        return str(v)

def sync_panel(devs):
    # state -> panel: alleen bij wijziging (of eerste keer)
    tado = cap_value(DEV_TADO, "measure_temperature")
    if tado is not None:
        t = f"{float(tado):.1f} \u00b0C"
        if synced.get("t") != t:
            synced["t"] = t
            push_line(f"hasp/{NODE}/", {"page": 1, "id": 10, "text": t + ("  #FFFF00" if False else "")})
    hum = cap_value(DEV_TADO, "measure_humidity")
    if hum is not None:
        t = f"Luchtvochtigheid {fmt_num(hum)}%"
        if synced.get("h") != t:
            synced["h"] = t
            push_line(f"hasp/{NODE}/", {"page": 1, "id": 11, "text": t})
    tar = cap_value(DEV_TADO, "target_temperature")
    if tar is not None:
        t = f"\u2192 {fmt_num(tar)} \u00b0C"
        if synced.get("g") != t:
            synced["g"] = t
            push_line(f"hasp/{NODE}/", {"page": 1, "id": 22, "text": t})
    for i, (_, devp) in enumerate(LIGHTS):
        v = cap_value(devp, "onoff")
        if v is None:
            continue
        val = 1 if str(v).lower() in ("true", "1") else 0
        key = f"l{i}"
        if synced.get(key) != val:
            synced[key] = val
            push_line(f"hasp/{NODE}/", {"page": 2, "id": 30 + i, "val": val})

def panel_repush():
    try:
        c.publish(f"hasp/{NODE}/command/config/gui", json.dumps({"rotate": 0}))
        time.sleep(1.5)
        push_layout()
        synced.clear()
        sync_panel({})
        log.info("panel-repush klaar (rotatie+layout+sync)")
    except Exception as e:
        log.error("panel-repush failed: %s", e)

EVENT_OK = {"up", "release"}

def on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    try:
        payload = msg.payload.decode()
        if parts[0] == "hasp" and len(parts) >= 3 and parts[2] == "LWT":
            if msg.payload.decode().strip() == "online":
                log.info("panel LWT online -> repush gepland")
                threading.Timer(3.0, panel_repush).start()
            return
        # paneel-events met tag
        if parts[0] == "hasp" and len(parts) >= 4 and parts[2] == "state":
            try:
                ev = json.loads(payload)
            except Exception:
                return
            tag = ev.get("tag")
            if not isinstance(tag, dict):
                return
            if ev.get("event") not in EVENT_OK and tag.get("t") != "page":
                return
            if tag.get("t") == "flow":
                req("/api/manager/flow/flow/" + str(tag["id"]) + "/trigger", "POST", {})
                log.info("flow %s triggered", tag["id"][:8])
                return
            if tag.get("t") == "page":
                client.publish(f"hasp/{NODE}/command/page", str(tag["id"]))
                return
            if tag.get("t") != "cap":
                return
            dev = dev_id(tag["dev"])
            if not dev:
                log.warning("dev onbekend: %s", tag["dev"]); return
            cap, mode = tag["cap"], tag.get("mode")
            if mode == "toggle" and "val" in ev:
                val = bool(ev["val"])
            elif mode == "delta":
                d = tag.get("d", 0.5)
                mn, mx = tag.get("min", 5), tag.get("max", 30)
                cur = cap_value(tag["dev"], cap)
                if cur is None:
                    return
                val = max(mn, min(mx, float(cur) + d))
            elif mode == "val" and "val" in ev:
                val = ev["val"]
            else:
                return
            req(f"/api/manager/devices/device/{dev}/capability/{cap}", "PUT", {"value": val})
            log.info("set %s.%s = %r", dev[:8], cap, val)
            # optimistische update in cache/panel
            cache.setdefault(tag["dev"], {})[cap] = val
            synced.pop("g", None)
            for i, (_, dp) in enumerate(LIGHTS):
                if dp == tag["dev"] and cap == "onoff":
                    synced.pop(f"l{i}", None)
            return
        # homey cmd topics
        if parts[1] == "cmd" and parts[2] == "flow":
            for fid in payload.split(","):
                fid = fid.strip()
                if fid:
                    req("/api/manager/flow/flow/" + fid + "/trigger", "POST", {})
                    log.info("flow %s triggered", fid[:8])
            return
        dev = fullids.get(parts[2], parts[2]); cap = parts[3]
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
        log.error("msg failed %s: %s", msg.topic, e)

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

c = mqtt.Client(client_id="homey-bridge", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
c.username_pw_set(os.environ.get("MQTT_USER", "hasp"), os.environ["MQTT_PASS"])
c.reconnect_delay_set(2, 30)
known = set()

def on_connect(client, userdata, flags, reason_code, properties=None):
    client.subscribe("homey/cmd/#", qos=1)
    client.subscribe(f"hasp/{NODE}/state/#")
    client.subscribe("hasp/plate1/LWT")
    log.info("MQTT connected (%s), geabonneerd", reason_code)
c.on_connect = on_connect
c.on_message = on_message

while True:
    # eerste keer verbinden + layout
    try:
        c.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        c.loop(timeout=1)
        break
    except Exception as e:
        log.warning("MQTT connect retry: %s", e); time.sleep(5)
c.loop_start()
time.sleep(2)
try:
    push_layout()
except Exception as e:
    log.error("layout push failed: %s", e)
first = True
while True:
    try:
        rc = c.loop(timeout=1)
        if rc != 0:
            raise ConnectionError(f"loop rc={rc}")
        devs = snapshot()
        new = set()
        for d in devs:
            if not d.get("id"):
                continue
            fullids[d["id"][:8]] = d["id"]
            for cap, val in d["capability"].items():
                cache.setdefault(d["id"][:8], {})[cap] = val
            base = f"homey/state/{d['id']}"
            c.publish(base, json.dumps(d, separators=(",", ":")), retain=True)
            new.add(base)
            for cap, val in d["capability"].items():
                t = f"{base}/{cap}"
                v = ("true" if val else "false") if isinstance(val, bool) else str(val)
                c.publish(t, v, retain=True)
                new.add(t)
        for t in known - new:
            c.publish(t, "", retain=True)
        known = new
        c.publish("homey/overview",
                  json.dumps({"devices": [{"id": d["id"], "name": d["name"], "zone": d["zone"]}
                                          for d in devs if d.get("id")]}, separators=(",", ":")), retain=True)
        log.info("Homey poll OK (%d devices)", len(devs))
        if first:
            first = False
            time.sleep(2)
        sync_panel(devs)
    except Exception as e:
        log.error("poll/sync failed: %s", e)
        try:
            c.reconnect()
        except Exception:
            time.sleep(10)
    time.sleep(POLL)

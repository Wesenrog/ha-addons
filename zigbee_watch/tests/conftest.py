"""Fixtures: a real MQTT broker in the test process (amqtt), a fake Zigbee2MQTT,
a fake clock, and the app under test against both."""
import asyncio, json, os, socket, sqlite3, sys, threading, time
from datetime import date

import paho.mqtt.client as mqtt
import pytest
from amqtt.broker import Broker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zigbee_watch.app import App                     # noqa: E402
from zigbee_watch.config import Config               # noqa: E402
from zigbee_watch.timeutil import LocalTime          # noqa: E402

OSLO = LocalTime("Europe/Oslo")
# A Monday well clear of any clock change; tests count from its local noon.
DAY0 = date(2026, 11, 2)
T0 = OSLO.at(DAY0, "12:00")

COORD = "0x00124b0000000000"
R1, R2 = "0x0017880100000001", "0x0017880100000002"
E1, E2, E3 = "0x00158d0000000011", "0x00158d0000000012", "0x00158d0000000013"


def device(ieee, name, type_, power, nwk, model="M"):
    return {"ieee_address": ieee, "friendly_name": name, "type": type_, "power_source": power,
            "network_address": nwk, "definition": {"model": model, "vendor": "V"}}


DEVICES = [
    device(COORD, "Coordinator", "Coordinator", None, 0),
    device(R1, "Ruter stue", "Router", "Mains (single phase)", 0x1001, "plug"),
    device(R2, "Ruter kjeller", "Router", "Mains (single phase)", 0x1002, "plug"),
    device(E1, "Termometer bad", "EndDevice", "Battery", 0x2001, "temp"),
    device(E2, "Termometer kontor", "EndDevice", "Battery", 0x2002, "temp"),
    device(E3, "Vindu soverom", "EndDevice", "Battery", 0x2003, "contact"),
]


def wait_until(predicate, timeout=5.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if predicate():
                return
        except (sqlite3.OperationalError, TypeError, IndexError):
            pass                     # not there yet
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


class FakeClock:
    def __init__(self, t):
        self.t = int(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += int(seconds)

    def set(self, t):
        self.t = int(t)


class BrokerThread:
    """amqtt on its own event loop in a thread; restartable on the same port,
    and like a broker restarted with a clean session it loses retained
    messages."""

    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.broker = None

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    async def _start(self):
        broker = Broker({
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}"}},
            "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
        })
        await broker.start()
        return broker

    def start(self):
        self.broker = self._run(self._start())

    def stop(self):
        if self.broker:
            self._run(self.broker.shutdown())
            self.broker = None

    def restart(self):
        self.stop()
        self.start()

    def close(self):
        self.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


class MqttProbe:
    """A plain client: records everything it sees, can publish."""

    def __init__(self, port, subscribe=("#",), client_id=""):
        self.messages = []
        self.retained = {}
        self._subs = subscribe
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.on_connect = self._connected
        self.client.on_message = self._message
        self.client.reconnect_delay_set(0.1, 0.5)
        self.client.connect("127.0.0.1", port)
        self.client.loop_start()
        wait_until(self.client.is_connected, what="probe connect")

    def _connected(self, client, userdata, flags, rc, properties=None):
        for topic in self._subs:
            client.subscribe(topic, qos=1)

    def _message(self, client, userdata, msg):
        # amqtt 0.12 also hands a new subscriber retained messages from topics
        # outside its filter; a real broker does not.
        if not any(mqtt.topic_matches_sub(sub, msg.topic) for sub in self._subs):
            return
        payload = msg.payload.decode(errors="replace")
        self.messages.append((msg.topic, payload, msg.retain))
        self.retained[msg.topic] = payload
        self.on_message(msg.topic, payload)

    def on_message(self, topic, payload):
        pass

    def publish(self, topic, payload, retain=False):
        if not isinstance(payload, (str, bytes)):
            payload = json.dumps(payload)
        self.client.publish(topic, payload, qos=1, retain=retain).wait_for_publish(5)

    def latest(self, topic):
        for t, p, _ in reversed(self.messages):
            if t == topic:
                return p
        return None

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


class FakeZ2M(MqttProbe):
    """Scripted Zigbee2MQTT: bridge topics, device messages, log lines, and a
    networkmap responder that answers, stays silent, or errors."""

    def __init__(self, port, base="zigbee2mqtt"):
        self.base = base
        self.mode = "answer"
        self.topology = None
        self.requests = []
        super().__init__(port, subscribe=(f"{base}/bridge/request/#",), client_id="fake-z2m")

    def on_message(self, topic, payload):
        if topic != f"{self.base}/bridge/request/networkmap":
            return
        request = json.loads(payload)
        self.requests.append(request)
        if self.mode == "silent":
            return
        if self.mode == "error":
            response = {"data": {}, "status": "error", "error": "Network is busy",
                        "transaction": request.get("transaction")}
        else:
            response = {"data": {"type": "raw", "routes": True, "value": self.topology},
                        "status": "ok", "transaction": request.get("transaction")}
        # From paho's thread: publish without waiting on ourselves.
        self.client.publish(f"{self.base}/bridge/response/networkmap", json.dumps(response), qos=1)

    def devices(self, devices=DEVICES):
        self.publish(f"{self.base}/bridge/devices", devices, retain=True)

    def bridge_state(self, state):
        self.publish(f"{self.base}/bridge/state", {"state": state}, retain=True)

    def message(self, name, payload, retain=False):
        self.publish(f"{self.base}/{name}", payload, retain=retain)

    def availability(self, name, state):
        self.publish(f"{self.base}/{name}/availability", {"state": state}, retain=True)

    def log(self, message, level="info"):
        self.publish(f"{self.base}/bridge/logging", {"message": message, "level": level})


def topology(parents, router_lqi=200, last_seen_ms=None):
    """A raw networkmap value. `parents` maps end device IEEE -> router IEEE;
    routers R1 and R2 hang off the coordinator."""
    names = {d["ieee_address"]: d for d in DEVICES}
    nodes = []
    for d in DEVICES:
        nodes.append({"ieeeAddr": d["ieee_address"], "friendlyName": d["friendly_name"],
                      "type": d["type"], "networkAddress": d["network_address"], "failed": [],
                      "lastSeen": last_seen_ms})
    links = []

    def link(src, tgt, rel, lqi, routes=()):
        links.append({"source": {"ieeeAddr": src, "networkAddress": names[src]["network_address"]},
                      "target": {"ieeeAddr": tgt, "networkAddress": names[tgt]["network_address"]},
                      "lqi": lqi, "depth": 1, "relationship": rel, "routes": list(routes),
                      "linkquality": lqi, "sourceIeeeAddr": src, "targetIeeeAddr": tgt})
    for r in (R1, R2):
        link(r, COORD, 2, router_lqi)
        link(COORD, r, 2, router_lqi, routes=[{"destinationAddress": 0, "status": "ACTIVE",
                                                "nextHopAddress": 0}])
    for end, router in parents.items():
        link(end, router, 1, 150)
    return {"nodes": nodes, "links": links}


@pytest.fixture
def broker():
    b = BrokerThread()
    b.start()
    yield b
    b.close()


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def z2m(broker):
    fake = FakeZ2M(broker.port)
    yield fake
    fake.close()


@pytest.fixture
def make_app(tmp_path, broker, clock):
    apps = []

    def make(**overrides):
        cfg = Config(db_path=str(tmp_path / "zigbee_watch.sqlite"), mqtt_host="127.0.0.1",
                     mqtt_port=broker.port, **overrides)
        app = App(cfg, clock=clock, idle=0.02, client_id="zigbee-watch")
        app.handled = []
        handle = app.watch.handle

        def recording(topic, raw, retained, now):
            handle(topic, raw, retained, now)
            app.handled.append(topic)
        app.watch.handle = recording
        app.start()
        wait_until(lambda: app.watch.connected, what="app connect")
        apps.append(app)
        return app

    yield make
    for app in apps:
        app.stop()


_SYNC = iter(range(10**9))


def sync(app, client):
    """Wait until the app has handled everything `client` published before:
    a marker on a topic the app subscribes to, after the others."""
    topic = f"zigbee2mqtt/bridge/sync/{next(_SYNC)}"
    client.publish(topic, "1")
    wait_until(lambda: topic in app.handled, what=f"app to handle {topic}")


def read_db(app):
    db = sqlite3.connect(app.cfg.db_path)
    db.row_factory = sqlite3.Row
    return db

"""Stale data shows as unavailable."""

import copy
import time

from fake_homely import FreezableProxy
from harness import DOOR, LOCATION, MOTION, wait_for
from test_snapshot import make_bridge

PROCESS = "homely/bridge/availability"
DATA = f"homely/{LOCATION}/availability"
HUB = f"homely/{LOCATION}/state"


def device_availability(device_id):
    return f"homely/{LOCATION}/{device_id}/availability"


def config_topic(component, node, key):
    return f"homeassistant/{component}/homely_{node}/{key}/config"


def availability_of(mq, topic):
    return [a["topic"] for a in mq.json(topic)["availability"]]


def wait_live(mq, timeout=10):
    wait_for(lambda: mq.messages.get(DATA) == "online", timeout)


def test_entities_unavailable_until_snapshot_and_websocket(fake, start):
    fake.reject_ws = True
    bridge, mq = start()
    # Snapshot applied, websocket refused: still not live
    wait_for(lambda: (s := mq.json(HUB)) and s["last_poll_status"] == "success")
    assert mq.messages[DATA] == "offline"
    assert mq.messages[PROCESS] == "online"
    assert mq.json(HUB)["live_updates"] == "OFF"
    time.sleep(1.5)
    assert mq.values(DATA) == ["offline"]

    polls_before = fake.calls.count("home")
    fake.reject_ws = False
    wait_live(mq)
    # The snapshot taken before the websocket connected did not count
    assert fake.calls.count("home") > polls_before
    assert mq.values(DATA) == ["offline", "online"]


def test_availability_lists(running):
    bridge, mq = running
    wait_live(mq)
    every = [PROCESS, DATA, device_availability(MOTION)]
    assert availability_of(mq, config_topic("binary_sensor", MOTION, "alarm_alarm")) == every
    assert availability_of(mq, config_topic("sensor", MOTION, "temperature_temperature")) == every
    assert availability_of(mq, config_topic("binary_sensor", MOTION, "online")) == [PROCESS, DATA]
    for key in ("armed", "triggered", "battery_problem", "tamper"):
        assert availability_of(mq, config_topic("binary_sensor", LOCATION, key)) == [PROCESS, DATA]
    assert availability_of(mq, config_topic("sensor", LOCATION, "alarm")) == [PROCESS, DATA]
    assert availability_of(mq, config_topic("binary_sensor", LOCATION, "live_updates")) == [PROCESS]
    for key in ("devices_offline", "last_poll", "last_poll_status"):
        assert availability_of(mq, config_topic("sensor", LOCATION, key)) == [PROCESS]
    assert mq.json(config_topic("binary_sensor", MOTION, "alarm_alarm"))["availability_mode"] == "all"
    assert "availability_topic" not in mq.json(config_topic("binary_sensor", MOTION, "alarm_alarm"))


def test_short_drop_changes_nothing(fake, start):
    bridge, mq = start(stale_after=4)
    wait_live(mq)
    since = time.monotonic()
    fake.kick_websockets()
    wait_for(lambda: mq.json(HUB)["live_updates"] == "OFF", timeout=5)
    wait_for(lambda: mq.json(HUB)["live_updates"] == "ON", timeout=5)
    time.sleep(4.5)  # past stale_after, measured from the drop
    assert mq.values(DATA, since) == []
    assert mq.messages[DATA] == "online"


def test_long_drop_goes_stale_until_a_fresh_poll(fake, start):
    bridge, mq = start(stale_after=2)
    wait_live(mq)
    assert mq.json(f"homely/{LOCATION}/{DOOR}/state")["alarm_alarm"] == "ON"

    fake.reject_ws = True
    dropped = time.monotonic()
    fake.kick_websockets()
    wait_for(lambda: mq.json(HUB)["live_updates"] == "OFF", timeout=5)
    # Still available within the grace period
    assert mq.messages[DATA] == "online"
    wait_for(lambda: mq.messages[DATA] == "offline", timeout=5)
    assert time.monotonic() - dropped >= 2
    # Diagnostics only follow the process, so they keep explaining
    assert mq.messages[PROCESS] == "online"

    # The door closes while the websocket is down; the event is lost
    for device in fake.home["devices"]:
        if device["id"] == DOOR:
            device["features"]["alarm"]["states"]["alarm"]["value"] = False
    fake.home_delay = 1.5
    polls_before = fake.calls.count("home")
    fake.reject_ws = False
    wait_for(lambda: fake.ws_sids, timeout=5)
    wait_for(lambda: mq.json(HUB)["live_updates"] == "ON", timeout=5)
    # Websocket back, but the data stays unavailable until the poll is in
    assert mq.messages[DATA] == "offline"
    wait_live(mq)
    assert fake.calls.count("home") > polls_before
    assert mq.json(f"homely/{LOCATION}/{DOOR}/state")["alarm_alarm"] == "OFF"


def test_reconnect_poll_honours_rate_limit(fake, start, monkeypatch):
    from homely_bridge import bridge as bridge_module

    monkeypatch.setattr(bridge_module, "POLL_BACKOFF", (4, 4, 4))
    bridge, mq = start(stale_after=1)
    wait_live(mq)
    fake.reject_ws = True
    fake.kick_websockets()
    wait_for(lambda: mq.messages[DATA] == "offline", timeout=5)
    # Rate limited while down: the next poll may not happen for 4 s
    fake.home_errors.append((429, {"Retry-After": "4"}))
    bridge.request_poll()
    wait_for(lambda: mq.json(HUB)["last_poll_status"] == "failed_429", timeout=5)
    limited_at = time.monotonic()
    fake.reject_ws = False
    wait_live(mq, timeout=10)
    assert time.monotonic() - limited_at >= 3.5


def test_device_reported_offline(fake, start):
    for device in fake.home["devices"]:
        if device["id"] == MOTION:
            device["online"] = False
    bridge, mq = start()
    wait_live(mq)
    assert mq.messages[device_availability(MOTION)] == "offline"
    assert mq.json(f"homely/{LOCATION}/{MOTION}/state")["online"] == "OFF"
    assert mq.messages[device_availability(DOOR)] == "online"

    # Back online in the next snapshot
    for device in fake.home["devices"]:
        if device["id"] == MOTION:
            device["online"] = True
    bridge.request_poll()
    wait_for(lambda: mq.messages[device_availability(MOTION)] == "online", timeout=5)

    # And offline again from a websocket event
    fake.emit({"type": "device-state-changed", "data": {"deviceId": MOTION, "online": False, "changes": []}})
    wait_for(lambda: mq.messages[device_availability(MOTION)] == "offline", timeout=5)
    assert mq.json(f"homely/{LOCATION}/{MOTION}/state")["online"] == "OFF"


def test_half_open_connection_is_detected(fake, start):
    proxy = FreezableProxy(fake.port)
    try:
        bridge, mq = start(ws_url=f"http://127.0.0.1:{proxy.port}", stale_after=2)
        wait_live(mq)
        fake.reject_ws = True  # keep it down after detection, to see it go stale
        frozen_at = time.monotonic()
        proxy.freeze()
        # Server pings every 1 s with a 1 s timeout: the client must give up within ~2 s
        wait_for(lambda: mq.json(HUB)["live_updates"] == "OFF", timeout=6)
        assert time.monotonic() - frozen_at < 4
        wait_for(lambda: mq.messages[DATA] == "offline", timeout=5)
        fake.reject_ws = False
        wait_live(mq)
    finally:
        proxy.close()


def test_republish_after_broker_restart(broker, running):
    bridge, mq = running
    wait_live(mq)
    before = {t: mq.messages[t] for t in (PROCESS, DATA, device_availability(MOTION), device_availability(DOOR))}
    config = config_topic("binary_sensor", MOTION, "alarm_alarm")
    discovery_before = mq.json(config)
    broker.restart()  # the test broker keeps nothing, retained messages included
    mq.clear()
    wait_for(lambda: all(mq.messages.get(t) == v for t, v in before.items()), timeout=15)
    wait_for(lambda: mq.json(config) == discovery_before, timeout=5)


def test_removal_clears_availability(home_payload):
    bridge, pub = make_bridge(home_payload)
    bridge.apply_snapshot(home_payload)
    topic = device_availability(DOOR)
    assert pub.last(topic) == "online"
    smaller = copy.deepcopy(home_payload)
    smaller["devices"] = [d for d in smaller["devices"] if d["id"] != DOOR]
    bridge.apply_snapshot(smaller)
    bridge.apply_snapshot(smaller)
    assert pub.last(topic) == ""


def test_transitions_logged_once(fake, start, caplog):
    caplog.set_level("INFO", logger="homely_bridge.bridge")
    bridge, mq = start(stale_after=1)
    wait_live(mq)
    fake.reject_ws = True
    fake.kick_websockets()
    wait_for(lambda: mq.messages[DATA] == "offline", timeout=5)
    time.sleep(1.5)
    fake.reject_ws = False
    wait_live(mq)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Data ")]
    assert lines == [
        "Data stale: starting",
        "Data live",
        "Data stale: live updates disconnected for more than 1s",
        "Data live",
    ]


# -- Poll on every reconnect ---------------------------------------------------


def set_door(fake, value):
    for device in fake.home["devices"]:
        if device["id"] == DOOR:
            device["features"]["alarm"]["states"]["alarm"]["value"] = value


def test_change_during_short_drop_is_fetched_on_reconnect(fake, start, monkeypatch):
    from homely_bridge import bridge as bridge_module

    gap = 3.0
    monkeypatch.setattr(bridge_module, "MIN_POLL_GAP", gap)
    bridge, mq = start(stale_after=10, poll_interval=3600)
    wait_live(mq)
    door_state = f"homely/{LOCATION}/{DOOR}/state"
    assert mq.json(door_state)["alarm_alarm"] == "ON"
    since = time.monotonic()

    fake.kick_websockets()
    set_door(fake, False)  # the door closes during the drop; no event is sent
    wait_for(lambda: mq.json(HUB)["live_updates"] == "OFF", timeout=5)
    wait_for(lambda: mq.json(HUB)["live_updates"] == "ON", timeout=5)
    reconnected = time.monotonic()
    wait_for(lambda: mq.json(door_state)["alarm_alarm"] == "OFF", timeout=gap + 2)
    assert time.monotonic() - reconnected <= gap + 1.5
    # A short drop: the data never went offline
    assert mq.values(DATA, since) == []


def test_flapping_connection_polls_at_most_once_per_gap(fake, start, monkeypatch):
    from homely_bridge import bridge as bridge_module

    gap = 3.0
    monkeypatch.setattr(bridge_module, "MIN_POLL_GAP", gap)
    bridge, mq = start(stale_after=30, poll_interval=3600)
    wait_live(mq)
    first = len(fake.home_times)
    end = time.monotonic() + 8
    drops = 0
    while time.monotonic() < end:
        if fake.ws_sids:
            fake.kick_websockets()
            drops += 1
        time.sleep(0.3)
    wait_for(lambda: mq.json(HUB)["live_updates"] == "ON", timeout=5)
    time.sleep(gap + 1)  # let a pending poll run
    polls = fake.home_times[first - 1:]  # include the last poll before flapping
    assert drops >= 4
    assert len(polls) >= 2, "reconnects must poll"
    assert all(b - a >= gap - 0.1 for a, b in zip(polls, polls[1:], strict=False)), polls


def test_replaced_connection_cannot_mark_new_one_down():
    from homely_bridge.websocket import HomelyWebSocket

    statuses = []
    ws = HomelyWebSocket("loc", on_event=lambda e: None, on_status=lambda up, why: statuses.append(up))
    old = object()
    ws._sio = old
    ws.connected = True
    # A new connection replaces the old one; the old one's late callback must be ignored
    ws._sio = object()
    handlers_of_old = []

    class FakeClient:
        def __init__(self, *a, **k):
            self.handlers = {}

        def event(self, fn):
            self.handlers[fn.__name__] = fn
            return fn

        def on(self, name):
            def register(fn):
                self.handlers[name] = fn
                return fn
            return register

        def connect(self, *a, **k):
            handlers_of_old.append(self.handlers)
            self.handlers["connect"]()

        def disconnect(self):
            pass

    import homely_bridge.websocket as wsmod

    real = wsmod.socketio.Client
    wsmod.socketio.Client = FakeClient
    try:
        ws.connect("t1")  # connection A
        first = handlers_of_old[-1]
        ws.connect("t2")  # connection B replaces A
        assert ws.connected
        first["disconnect"]("transport error")  # A's late disconnect
        assert ws.connected
        assert statuses == [True, True]
        first["event"]({"type": "x"})  # and A's events are dropped
    finally:
        wsmod.socketio.Client = real

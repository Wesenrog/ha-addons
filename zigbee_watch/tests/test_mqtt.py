"""End to end through the in-process broker with a fake Zigbee2MQTT."""
import json

from conftest import (DEVICES, E1, E2, E3, OSLO, DAY0, R1, R2, MqttProbe, device, read_db,
                      sync, topology, wait_until)


def start(make_app, z2m, clock, **cfg):
    """Zigbee2MQTT online with its inventory, the app connected, and the clock
    moved past the cached-state replay window."""
    z2m.bridge_state("online")
    z2m.devices()
    app = make_app(**cfg)
    sync(app, z2m)
    clock.advance(600)
    return app


# --- inventory ---------------------------------------------------------------
def test_inventory_changes_recorded(make_app, z2m, clock):
    app = start(make_app, z2m, clock)
    db = read_db(app)
    assert db.execute("SELECT COUNT(*) FROM device").fetchone()[0] == len(DEVICES)
    assert db.execute("SELECT COUNT(*) FROM device_event WHERE kind='appeared'").fetchone()[0] == len(DEVICES)

    # Same list again (a retained replay): nothing new.
    z2m.devices()
    sync(app, z2m)
    assert db.execute("SELECT COUNT(*) FROM device_event").fetchone()[0] == len(DEVICES)

    clock.advance(60)
    changed = [dict(d) for d in DEVICES if d["ieee_address"] != E3]           # E3 leaves
    changed[3]["friendly_name"] = "Termometer badet"                           # E1 renamed
    changed[4]["network_address"] = 0x2F02                                     # E2 rejoined
    changed.append(device("0x00158d0000000099", "Ny sensor", "EndDevice", "Battery", 0x2099))
    z2m.devices(changed)
    sync(app, z2m)
    events = {(r["kind"], r["old"], r["new"]) for r in db.execute(
        "SELECT * FROM device_event WHERE ts = ?", (clock.t,))}
    assert events == {
        ("disappeared", "Vindu soverom", None),
        ("renamed", "Termometer bad", "Termometer badet"),
        ("network_address", "0x2002", "0x2f02"),
        ("appeared", None, "Ny sensor"),
    }
    # The history stays on the IEEE address across the rename.
    assert db.execute("SELECT name FROM device WHERE ieee = ?", (E1,)).fetchone()[0] == "Termometer badet"
    assert db.execute("SELECT removed_at FROM device WHERE ieee = ?", (E3,)).fetchone()[0] == clock.t

    # The device comes back.
    clock.advance(60)
    z2m.devices(changed + [d for d in DEVICES if d["ieee_address"] == E3])
    sync(app, z2m)
    assert db.execute("SELECT removed_at FROM device WHERE ieee = ?", (E3,)).fetchone()[0] is None
    assert db.execute("SELECT kind FROM device_event WHERE ts = ?", (clock.t,)).fetchone()[0] == "appeared"


def test_odd_inventory_ignored(make_app, z2m, clock):
    app = start(make_app, z2m, clock)
    for bad in ("[]", "not json", '{"a": 1}', '[{"friendly_name": "no ieee"}]'):
        z2m.publish("zigbee2mqtt/bridge/devices", bad, retain=True)
    sync(app, z2m)
    db = read_db(app)
    assert db.execute("SELECT COUNT(*) FROM device WHERE removed_at IS NULL").fetchone()[0] == len(DEVICES)


# --- link quality -------------------------------------------------------------
def test_lqi_thinned_to_last_value_per_10_minutes(make_app, z2m, clock):
    app = start(make_app, z2m, clock)
    clock.set(clock.t - clock.t % 600)                 # start of a bucket
    bucket = clock.t
    for lqi in (100, 110, 120):
        z2m.message("Termometer bad", {"temperature": 21.5, "linkquality": lqi})
        sync(app, z2m)
        clock.advance(120)
    clock.set(bucket + 600)                            # next bucket
    z2m.message("Termometer bad", {"temperature": 21.4, "linkquality": 90})
    z2m.message("Termometer bad", {"temperature": 21.4})          # no linkquality
    z2m.message("Termometer bad", {"linkquality": 999})           # out of range
    z2m.message("Termometer bad", "garbage")
    sync(app, z2m)
    db = read_db(app)
    rows = db.execute("SELECT bucket, lqi FROM lqi ORDER BY bucket").fetchall()
    assert [tuple(r) for r in rows] == [(bucket, 120), (bucket + 600, 90)]
    dev_id = db.execute("SELECT id FROM device WHERE ieee = ?", (E1,)).fetchone()[0]
    assert db.execute("SELECT ts FROM heard WHERE device_id = ?", (dev_id,)).fetchone()[0] == bucket + 600


def test_cached_republish_and_retained_messages_not_stored(make_app, z2m, clock):
    # A device with retain: true, its message stored before the app subscribes:
    # delivered to the app as retained, so stale.
    z2m.message("Vindu soverom", {"contact": True, "linkquality": 66}, retain=True)
    app = start(make_app, z2m, clock)
    db = read_db(app)
    # Zigbee2MQTT restarts and republishes its cached states.
    z2m.bridge_state("offline")
    sync(app, z2m)
    clock.advance(300)
    z2m.bridge_state("online")
    z2m.message("Termometer kontor", {"temperature": 20.0, "linkquality": 77})
    sync(app, z2m)
    # Home Assistant restarts: Zigbee2MQTT republishes 30 s later.
    clock.advance(600)
    z2m.publish("homeassistant/status", "online")
    clock_ha = clock.t
    sync(app, z2m)
    clock.advance(30)
    z2m.message("Termometer kontor", {"temperature": 20.0, "linkquality": 78})
    sync(app, z2m)
    assert db.execute("SELECT COUNT(*) FROM lqi").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM heard").fetchone()[0] == 0
    # A cached message that carries last_seen still says when it was heard.
    seen = clock_ha - 3600
    clock.set(clock_ha + 60)
    z2m.message("Termometer kontor", {"linkquality": 78, "last_seen": seen * 1000})
    sync(app, z2m)
    assert db.execute("SELECT ts FROM heard").fetchone()[0] == seen
    # Afterwards messages count again.
    clock.set(clock_ha + 3600)
    z2m.message("Termometer kontor", {"temperature": 20.1, "linkquality": 80})
    sync(app, z2m)
    assert db.execute("SELECT lqi FROM lqi").fetchone()[0] == 80


# --- availability -------------------------------------------------------------
def test_availability_transitions_stored(make_app, z2m, clock):
    app = start(make_app, z2m, clock)
    db = read_db(app)
    for state in ("online", "online", "offline", "offline", "online"):
        z2m.availability("Ruter stue", state)
        sync(app, z2m)
        clock.advance(60)
    z2m.publish("zigbee2mqtt/Ruter stue/availability", "online", retain=True)   # legacy form
    z2m.publish("zigbee2mqtt/Ruter stue/availability", "{bad", retain=True)
    sync(app, z2m)
    states = [r[0] for r in db.execute("SELECT state FROM availability ORDER BY ts")]
    assert states == ["online", "offline", "online"]


# --- log lines -------------------------------------------------------------------
LOG_LINES = [
    # (message, level, expected kind or None, expected device IEEE or None)
    ('zh:ember:ezsp: Received network/route error ROUTE_ERROR_NON_TREE_LINK_FAILURE for "8193".',
     "info", "route_error", E1),
    ('zh:ember:ezsp: Received network/route error NETWORK_STATUS_BAD_FRAME_COUNTER for "8194".',
     "info", "network_status", E2),
    ('zh:ember:ezsp: Received network/route error ROUTE_ERROR_NO_ROUTE_AVAILABLE for "48879".',
     "info", "route_error", None),                                # address not in the inventory
    ("zh:ember: Delivery of BROADCAST failed for '65533'.", "error",
     "delivery_failed_broadcast", None),
    ("z2m: Publish 'set' 'state' to 'Ruter kjeller' failed: 'Error: ZCL command "
     "0x0017880100000002/1 genOnOff.on({}, {}) failed (Delivery failed for '4098'.)'",
     "error", "delivery_failed", R2),
    ("z2m: Publish 'get' 'temperature' to 'Termometer bad' failed: 'Error: ZCL command "
     "0x00158d0000000011/1 msTemperatureMeasurement.read failed ({\"target\":8193} timed out after 10000ms)'",
     "error", "timeout", E1),
    ("z2m: Failed to ping 'Ruter stue' (attempt 1/2, Delivery failed for '4097'.)", "warning",
     "ping_failed", R1),
    ("z2m: Failed to execute LQI for 'Ruter kjeller'", "error", "scan_lqi_failed", R2),
]


def test_log_lines_classified_and_unknown_sampled(make_app, z2m, clock):
    app = start(make_app, z2m, clock)
    db = read_db(app)
    for message, level, _, _ in LOG_LINES:
        z2m.log(message, level)
    # Unknown lines: two of one shape (different numbers), one other; info not sampled.
    z2m.log("zh:ember:uart:ash: Received frame with CRC error 17", "warning")
    z2m.log("zh:ember:uart:ash: Received frame with CRC error 23", "warning")
    z2m.log("z2m: Something new went wrong", "error")
    z2m.log("z2m: MQTT publish: topic 'zigbee2mqtt/x'", "info")
    z2m.log({"not": "a log line"}, "error")
    sync(app, z2m)
    rows = db.execute("SELECT e.kind, d.ieee FROM log_event e LEFT JOIN device d ON d.id = e.device_id"
                      " ORDER BY e.rowid").fetchall()
    assert [(r["kind"], r["ieee"]) for r in rows] == [(k, i) for _, _, k, i in LOG_LINES]
    unknown = {r["sample"]: r["count"] for r in db.execute("SELECT * FROM log_unmatched")}
    assert unknown == {
        "zh:ember:uart:ash: Received frame with CRC error 23": 2,
        "z2m: Something new went wrong": 1,
    } or unknown == {
        "zh:ember:uart:ash: Received frame with CRC error 17": 2,
        "z2m: Something new went wrong": 1,
    }


# --- network map -------------------------------------------------------------------
def at_scan_time(clock, day=DAY0, hhmm="02:15"):
    clock.set(OSLO.at(day, hhmm))


def test_scan_stored(make_app, z2m, clock, broker):
    probe = MqttProbe(broker.port, subscribe=("zigbee_watch/#",))
    z2m.topology = topology({E1: R1, E2: R1, E3: R2}, last_seen_ms=(clock.t - 60) * 1000)
    app = start(make_app, z2m, clock)
    db = read_db(app)
    at_scan_time(clock, DAY0.replace(day=DAY0.day + 1))
    wait_until(lambda: db.execute("SELECT status FROM scan").fetchone()[0] == "ok", what="scan ok")
    assert z2m.requests[0]["type"] == "raw" and z2m.requests[0]["routes"] is True
    assert len(z2m.requests) == 1                       # once a night
    scan_id = db.execute("SELECT id FROM scan").fetchone()[0]
    assert db.execute("SELECT COUNT(*) FROM scan_node WHERE scan_id = ?", (scan_id,)).fetchone()[0] == 6
    assert db.execute("SELECT COUNT(*) FROM scan_link WHERE scan_id = ?", (scan_id,)).fetchone()[0] == 7
    parents = {r["ieee"]: (r["parent"], r["how"]) for r in db.execute(
        "SELECT d.ieee, pd.ieee AS parent, p.how FROM parent p JOIN device d ON d.id = p.device_id"
        " LEFT JOIN device pd ON pd.id = p.parent_id")}
    assert parents[E1] == (R1, "child_of") and parents[E3] == (R2, "child_of")
    assert parents[R1][1] == "route"                    # next hop to the coordinator
    # The scan's lastSeen counts as heard for devices that never published.
    assert db.execute("SELECT COUNT(*) FROM heard").fetchone()[0] == 6
    clock.advance(60)
    wait_until(lambda: probe.retained.get("zigbee_watch/kartstatus/state") == "ok", what="kartstatus ok")
    assert probe.retained["zigbee_watch/siste_kart/state"].endswith("+00:00")
    probe.close()


def test_scan_timeout_reported_and_late_answer_ignored(make_app, z2m, clock):
    z2m.mode = "silent"
    z2m.topology = topology({E1: R1})
    app = start(make_app, z2m, clock)
    db = read_db(app)
    at_scan_time(clock, DAY0.replace(day=DAY0.day + 1))
    wait_until(lambda: len(z2m.requests) == 1, what="scan request")
    clock.advance(14 * 60)
    sync(app, z2m)
    assert db.execute("SELECT status FROM scan").fetchone()[0] == "running"
    clock.advance(2 * 60)
    wait_until(lambda: db.execute("SELECT status FROM scan").fetchone()[0] == "timeout", what="timeout")
    # The answer arrives after all: ignored, not stored, no second scan.
    txn = z2m.requests[0]["transaction"]
    z2m.publish("zigbee2mqtt/bridge/response/networkmap",
                {"data": {"type": "raw", "routes": True, "value": z2m.topology}, "status": "ok",
                 "transaction": txn})
    clock.advance(3600)
    sync(app, z2m)
    assert db.execute("SELECT COUNT(*) FROM scan_node").fetchone()[0] == 0
    assert db.execute("SELECT status FROM scan").fetchall()[-1][0] == "timeout"
    assert len(z2m.requests) == 1


def test_scan_error_reported(make_app, z2m, clock):
    z2m.mode = "error"
    app = start(make_app, z2m, clock)
    db = read_db(app)
    at_scan_time(clock, DAY0.replace(day=DAY0.day + 1))
    wait_until(lambda: (db.execute("SELECT status FROM scan").fetchone() or [""])[0]
               == "error: Network is busy", what="scan error")


def test_scan_skipped_near_restart_then_run(make_app, z2m, clock, broker):
    probe = MqttProbe(broker.port, subscribe=("zigbee_watch/#",))
    z2m.topology = topology({E1: R1})
    app = start(make_app, z2m, clock)
    db = read_db(app)
    night = DAY0.replace(day=DAY0.day + 1)
    # A power-cycle: Zigbee2MQTT gone at 01:55, back at 02:05.
    at_scan_time(clock, night, "01:55")
    z2m.bridge_state("offline")
    sync(app, z2m)
    at_scan_time(clock, night, "02:05")
    z2m.bridge_state("online")
    sync(app, z2m)
    at_scan_time(clock, night, "02:15")
    wait_until(lambda: db.execute("SELECT COUNT(*) FROM scan").fetchone()[0] == 1, what="skip row")
    assert db.execute("SELECT status FROM scan").fetchone()[0] == "skipped: Zigbee2MQTT restarted 02:05"
    assert z2m.requests == []
    wait_until(lambda: probe.retained.get("zigbee_watch/kartstatus/state") ==
               "skipped: Zigbee2MQTT restarted 02:05", what="kartstatus skipped")
    probe.close()
    # 30 minutes after the restart it runs.
    at_scan_time(clock, night, "02:34")
    sync(app, z2m)
    assert z2m.requests == []
    at_scan_time(clock, night, "02:36")
    wait_until(lambda: db.execute("SELECT status FROM scan ORDER BY id DESC").fetchone()[0] == "ok",
               what="scan after the guard")
    assert len(z2m.requests) == 1


def test_scan_skipped_while_z2m_offline(make_app, z2m, clock):
    app = start(make_app, z2m, clock)
    db = read_db(app)
    z2m.bridge_state("offline")
    sync(app, z2m)
    at_scan_time(clock, DAY0.replace(day=DAY0.day + 1))
    wait_until(lambda: (db.execute("SELECT status FROM scan").fetchone() or [""])[0]
               == "skipped: Zigbee2MQTT offline", what="skip offline")
    # Never comes back that night: no request, no second row.
    at_scan_time(clock, DAY0.replace(day=DAY0.day + 1), "06:00")
    sync(app, z2m)
    assert z2m.requests == []
    assert db.execute("SELECT COUNT(*) FROM scan").fetchone()[0] == 1


# --- entities and reconnect -----------------------------------------------------------
ENTITY_IDS = ["sensor.zigbee_watch_funn", "sensor.zigbee_watch_rutefeil_24t",
              "sensor.zigbee_watch_siste_kart", "sensor.zigbee_watch_kartstatus",
              "binary_sensor.zigbee_watch_tilkoblet"]


def discovered(probe):
    out = {}
    for topic, payload, _ in probe.messages:
        if topic.startswith("homeassistant/") and topic.endswith("/config") and payload:
            cfg = json.loads(payload)
            out[cfg["default_entity_id"]] = cfg
    return out


def test_entities_discovered(make_app, z2m, clock, broker):
    probe = MqttProbe(broker.port, subscribe=("homeassistant/#", "zigbee_watch/#"))
    start(make_app, z2m, clock)
    wait_until(lambda: len(discovered(probe)) == 5, what="discovery")
    configs = discovered(probe)
    assert sorted(configs) == sorted(ENTITY_IDS)
    for cfg in configs.values():
        assert {"topic": "zigbee_watch/availability"} in cfg["availability"]
        assert cfg["device"]["identifiers"] == ["zigbee_watch"]
    assert configs["binary_sensor.zigbee_watch_tilkoblet"]["entity_category"] == "diagnostic"
    assert configs["sensor.zigbee_watch_siste_kart"]["device_class"] == "timestamp"
    assert probe.retained["zigbee_watch/availability"] == "online"
    wait_until(lambda: probe.retained.get("zigbee_watch/tilkoblet/state") == "ON", what="tilkoblet")
    z2m.bridge_state("offline")
    wait_until(lambda: probe.retained.get("zigbee_watch/tilkoblet/state") == "OFF", what="tilkoblet off")
    probe.close()


def test_reconnect_resubscribes_and_republishes(make_app, z2m, clock, broker):
    app = start(make_app, z2m, clock)
    db = read_db(app)
    connects = len(db.execute("SELECT * FROM listening").fetchall())
    z2m.close()
    broker.restart()                       # a clean broker: retained messages gone
    probe = MqttProbe(broker.port, subscribe=("homeassistant/#", "zigbee_watch/#"))
    wait_until(lambda: len(db.execute("SELECT * FROM listening").fetchall()) == connects + 1,
               timeout=15, what="app reconnect")
    # Republished: availability, discovery and state, retained on the new broker.
    wait_until(lambda: probe.retained.get("zigbee_watch/availability") == "online", what="availability")
    wait_until(lambda: len(discovered(probe)) == 5, what="discovery after restart")
    wait_until(lambda: "zigbee_watch/funn/state" in probe.retained, what="state after restart")
    # Resubscribed: a device message after the restart is stored.
    fresh = type(z2m)(broker.port)
    fresh.message("Ruter stue", {"power": 5, "linkquality": 140})
    sync(app, fresh)
    assert db.execute("SELECT lqi FROM lqi").fetchone()[0] == 140
    fresh.close()
    probe.close()


def test_republish_on_home_assistant_online(make_app, z2m, clock, broker):
    start(make_app, z2m, clock)
    probe = MqttProbe(broker.port, subscribe=("homeassistant/sensor/zigbee_watch/#",))
    wait_until(lambda: len(probe.messages) == 4, what="retained discovery")
    z2m.publish("homeassistant/status", "online")
    wait_until(lambda: len(probe.messages) == 8, what="discovery republished")
    probe.close()


def test_last_will_marks_unavailable(make_app, z2m, clock, broker):
    probe = MqttProbe(broker.port, subscribe=("zigbee_watch/availability",))
    app = start(make_app, z2m, clock)
    wait_until(lambda: probe.retained.get("zigbee_watch/availability") == "online", what="online")
    app.link._client._sock.close()          # die without a clean disconnect
    wait_until(lambda: probe.retained.get("zigbee_watch/availability") == "offline", timeout=10,
               what="last will")
    probe.close()


def test_garbage_never_crashes(make_app, z2m, clock):
    app = start(make_app, z2m, clock)
    for topic, payload in [
        ("zigbee2mqtt/bridge/state", b"\xff\xfe"),
        ("zigbee2mqtt/bridge/logging", "[1,2]"),
        ("zigbee2mqtt/bridge/response/networkmap", '{"transaction": 5}'),
        ("zigbee2mqtt/bridge/devices", "null"),
        ("zigbee2mqtt/Ruter stue", "[]"),
        ("zigbee2mqtt/Ruter stue/availability", "42"),
        ("zigbee2mqtt/unknown device", '{"linkquality": 5}'),
        ("homeassistant/status", b"\x00"),
    ]:
        z2m.publish(topic, payload)
    sync(app, z2m)
    assert app._worker.is_alive()

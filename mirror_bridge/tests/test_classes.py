"""Buttons and date/time helpers (0.5.0), scenes, light colour and effects
(0.6.0), relays (0.7.0), number helpers, timers and scripts (0.8.0) on the
mirror: what each becomes, and that a command is checked before
any service is called."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mirror_bridge import classes                                 # noqa: E402
from mirror_bridge.bridge import Bridge                           # noqa: E402
from mirror_bridge.config import Config                           # noqa: E402

RETURN = {"entity_id": "input_datetime.home", "state": "2026-10-25 18:45:00",
          "attributes": {"has_date": True, "has_time": True, "friendly_name": "Home"}}
CANCEL = {"entity_id": "button.cancel", "state": "unknown",
          "attributes": {"friendly_name": "Cancel"}}
SCENE = {"entity_id": "scene.room_read", "state": "unknown",
         "attributes": {"friendly_name": "Room Read"}}
LAMP = {"entity_id": "light.lamp", "state": "on",
        "attributes": {"supported_color_modes": ["color_temp", "xy"], "color_mode": "xy",
                       "brightness": 128, "color_temp_kelvin": 2700,
                       "xy_color": [0.4573, 0.41], "effect": "off",
                       "effect_list": ["off", "candle", "fire"],
                       "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6535,
                       "friendly_name": "Lamp"}}


class FakeHA:
    def __init__(self, states):
        self.states, self.calls = states, []

    async def get_states(self):
        return list(self.states)

    async def call_service(self, domain, service, target, data):
        self.calls.append((domain, service, target, data))


class FakeBroker:
    def __init__(self):
        self.sent = {}

    def publish(self, topic, payload, retain=True):
        self.sent[topic] = payload

    def retract(self, topic):
        self.sent.pop(topic, None)
EDITOR = {"entity_id": "sensor.editor", "state": "ok",
          "attributes": {"zones": {"a": {"programme": {"mon": [[0, 19.0]]}}},
                         "last": None, "friendly_name": "Editor"}}
SPEED = {"entity_id": "input_number.speed", "state": "45.0",
         "attributes": {"min": 0, "max": 100, "step": 5, "mode": "slider",
                        "unit_of_measurement": "%", "friendly_name": "Speed"}}
TIMER = {"entity_id": "timer.run", "state": "active",
         "attributes": {"duration": "0:30:00", "remaining": "0:30:00",
                        "finishes_at": "2026-11-01T21:00:00+00:00", "friendly_name": "Run"}}
SCRIPT = {"entity_id": "script.start", "state": "off",
          "attributes": {"friendly_name": "Start"}}


def bridge(states, writable=True):
    ha, broker = FakeHA(states), FakeBroker()
    b = Bridge(Config(), ha, broker)
    b._members = {s["entity_id"]: writable for s in states}
    b._cfg.relays = {"sensor.editor": "prog/editor/command"}
    return b, ha, broker


def run(coro):
    return asyncio.run(coro)


def test_a_writable_button_is_a_button_that_presses():
    spec = classes.describe("button.cancel", CANCEL, True)
    assert spec.component == "button"
    assert set(spec.commands) == {"press"}
    assert "press" in classes.ALLOWED["button"]


def test_a_read_only_button_is_not_mirrored():
    assert classes.describe("button.cancel", CANCEL, False) is None


def test_a_button_has_a_command_topic_and_no_state_topic():
    b, _ha, _broker = bridge([CANCEL])
    payload = b._discovery("button.cancel", CANCEL, classes.describe("button.cancel", CANCEL, True))
    assert payload["command_topic"] == "mirror/cmd/button.cancel/press"
    assert "state_topic" not in payload


def test_pressing_calls_button_press():
    b, ha, _ = bridge([CANCEL])
    run(b.handle_command("mirror/cmd/button.cancel/press", "PRESS"))
    assert ha.calls == [("button", "press", {"entity_id": "button.cancel"}, {})]


def test_anything_but_press_is_refused():
    b, ha, _ = bridge([CANCEL])
    run(b.handle_command("mirror/cmd/button.cancel/press", "on"))
    assert ha.calls == []


def test_a_writable_datetime_is_text_held_to_its_format():
    spec = classes.describe("input_datetime.home", RETURN, True)
    assert spec.component == "text"
    assert spec.config["pattern"] == r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$"
    assert spec.commands["set"].data_field == "datetime"


def test_a_read_only_datetime_is_a_sensor():
    assert classes.describe("input_datetime.home", RETURN, False).component == "sensor"


def test_a_date_only_helper_takes_a_date():
    st = dict(RETURN, attributes={"has_date": True, "has_time": False})
    assert classes.describe("input_datetime.home", st, True).commands["set"].data_field == "date"


def test_setting_a_return_time_calls_set_datetime_with_seconds():
    b, ha, _ = bridge([RETURN])
    run(b.handle_command("mirror/cmd/input_datetime.home/set", "2026-11-02 17:30"))
    assert ha.calls == [("input_datetime", "set_datetime", {"entity_id": "input_datetime.home"},
                         {"datetime": "2026-11-02 17:30:00"})]


def test_a_date_that_does_not_exist_is_refused():
    b, ha, _ = bridge([RETURN])
    for bad in ("2026-02-30 10:00", "tomorrow", "2026-11-02T17:30", ""):
        run(b.handle_command("mirror/cmd/input_datetime.home/set", bad))
    assert ha.calls == []


def test_read_only_members_still_refuse_commands():
    b, ha, _ = bridge([RETURN, CANCEL], writable=False)
    run(b.handle_command("mirror/cmd/input_datetime.home/set", "2026-11-02 17:30"))
    run(b.handle_command("mirror/cmd/button.cancel/press", "PRESS"))
    assert ha.calls == []


def test_a_writable_scene_is_a_scene_without_state():
    spec = classes.describe("scene.room_read", SCENE, True)
    assert spec.component == "scene"
    b, _ha, _ = bridge([SCENE])
    payload = b._discovery("scene.room_read", SCENE, spec)
    assert payload["command_topic"] == "mirror/cmd/scene.room_read/activate"
    assert payload["payload_on"] == "PRESS"
    assert "state_topic" not in payload


def test_a_read_only_scene_is_not_mirrored():
    assert classes.describe("scene.room_read", SCENE, False) is None


def test_activating_calls_scene_turn_on_and_nothing_else_does():
    b, ha, _ = bridge([SCENE])
    run(b.handle_command("mirror/cmd/scene.room_read/activate", "ON"))
    run(b.handle_command("mirror/cmd/scene.room_read/activate", "PRESS"))
    assert ha.calls == [("scene", "turn_on", {"entity_id": "scene.room_read"}, {})]


def test_a_colour_light_gets_xy_and_its_effects():
    spec = classes.describe("light.lamp", LAMP, True)
    b, _ha, _ = bridge([LAMP])
    payload = b._discovery("light.lamp", LAMP, spec)
    assert payload["xy_command_topic"] == "mirror/cmd/light.lamp/xy"
    assert payload["xy_state_topic"] == "mirror/state/light/lamp/xy_color"
    assert payload["effect_command_topic"] == "mirror/cmd/light.lamp/effect"
    assert payload["effect_list"] == ["off", "candle", "fire"]


def test_a_white_light_gets_neither():
    st = dict(LAMP, attributes={"supported_color_modes": ["color_temp"],
                                "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6535})
    spec = classes.describe("light.lamp", st, True)
    assert "xy" not in spec.commands and "effect" not in spec.commands


def test_the_colour_goes_out_as_x_comma_y():
    b, _ha, broker = bridge([LAMP])
    b.publish_entity("light.lamp", LAMP)
    assert broker.sent["mirror/state/light/lamp/xy_color"] == "0.4573,0.41"
    assert broker.sent["mirror/state/light/lamp/effect"] == "off"


def test_the_mode_the_source_is_in_is_published_last():
    def later(st):
        order = classes.describe("light.lamp", st, True).attributes
        return max(("xy_color", "color_temp_kelvin"), key=order.index)
    assert later(LAMP) == "xy_color"
    ct = dict(LAMP, attributes=dict(LAMP["attributes"], color_mode="color_temp"))
    assert later(ct) == "color_temp_kelvin"


def test_setting_a_colour_calls_turn_on_with_xy_color():
    b, ha, _ = bridge([LAMP])
    run(b.handle_command("mirror/cmd/light.lamp/xy", "0.3,0.6"))
    for bad in ("0.3", "0.3,1.2", "red", "0.1,0.2,0.3"):
        run(b.handle_command("mirror/cmd/light.lamp/xy", bad))
    assert ha.calls == [("light", "turn_on", {"entity_id": "light.lamp"},
                         {"xy_color": [0.3, 0.6]})]


def test_an_effect_must_be_one_the_light_lists():
    b, ha, _ = bridge([LAMP])
    run(b.handle_command("mirror/cmd/light.lamp/effect", "candle"))
    run(b.handle_command("mirror/cmd/light.lamp/effect", "disco"))
    assert ha.calls == [("light", "turn_on", {"entity_id": "light.lamp"},
                         {"effect": "candle"})]


def test_relays_are_parsed_and_wildcards_dropped():
    from mirror_bridge.config import parse_relays
    assert parse_relays(" sensor.editor = prog/editor/command ,sensor.x=a/#,light.y=b,junk") \
        == {"sensor.editor": "prog/editor/command"}
    assert parse_relays("") == {}


def test_a_relay_sensor_carries_its_attributes_and_a_command():
    b, _ha, broker = bridge([EDITOR])
    spec = classes.describe("sensor.editor", EDITOR, True, relay="prog/editor/command")
    payload = b._discovery("sensor.editor", EDITOR, spec)
    assert payload["json_attributes_topic"] == "mirror/state/sensor/editor/attributes"
    assert payload["command_topic"] == "mirror/cmd/sensor.editor/command"
    b.publish_entity("sensor.editor", EDITOR)
    assert json.loads(broker.sent["mirror/state/sensor/editor/attributes"])["zones"]["a"]


def test_a_sensor_without_the_option_or_the_label_is_a_plain_sensor():
    assert classes.describe("sensor.editor", EDITOR, True).commands == {}
    assert classes.describe("sensor.editor", EDITOR, False, relay="t").commands == {}


def test_a_command_is_relayed_unchanged_and_unretained():
    b, ha, broker = bridge([EDITOR])
    sent = []
    broker.publish = lambda topic, payload, retain=True: sent.append((topic, payload, retain))
    cmd = '{"id": "x1", "op": "refresh"}'
    run(b.handle_command("mirror/cmd/sensor.editor/command", cmd))
    assert sent == [("prog/editor/command", cmd, False)]
    assert ha.calls == []


def test_only_a_json_object_within_the_limit_is_relayed():
    b, _ha, broker = bridge([EDITOR])
    sent = []
    broker.publish = lambda topic, payload, retain=True: sent.append(topic)
    for bad in ("refresh", "[1, 2]", '"op"', "", '{"op": "%s"}' % ("x" * 20000)):
        run(b.handle_command("mirror/cmd/sensor.editor/command", bad))
    assert sent == []


def test_a_read_only_relay_sensor_refuses_commands():
    b, _ha, broker = bridge([EDITOR], writable=False)
    sent = []
    broker.publish = lambda topic, payload, retain=True: sent.append(topic)
    run(b.handle_command("mirror/cmd/sensor.editor/command", '{"op": "refresh"}'))
    assert sent == []


def test_a_writable_number_helper_is_a_number_held_to_its_range():
    spec = classes.describe("input_number.speed", SPEED, True)
    assert spec.component == "number"
    assert (spec.config["min"], spec.config["max"], spec.config["step"]) == (0, 100, 5)
    assert spec.config["unit_of_measurement"] == "%"
    b, ha, _ = bridge([SPEED])
    run(b.handle_command("mirror/cmd/input_number.speed/set", "60"))
    run(b.handle_command("mirror/cmd/input_number.speed/set", "250"))
    run(b.handle_command("mirror/cmd/input_number.speed/set", "fast"))
    assert ha.calls == [("input_number", "set_value", {"entity_id": "input_number.speed"},
                         {"value": 60.0}),
                        ("input_number", "set_value", {"entity_id": "input_number.speed"},
                         {"value": 100.0})]


def test_a_read_only_number_helper_is_a_sensor():
    assert classes.describe("input_number.speed", SPEED, False).component == "sensor"


def test_a_timer_is_a_read_only_sensor_with_its_countdown():
    for writable in (True, False):
        spec = classes.describe("timer.run", TIMER, writable)
        assert spec.component == "sensor" and spec.commands == {}
    b, ha, broker = bridge([TIMER])
    b.publish_entity("timer.run", TIMER)
    assert json.loads(broker.sent["mirror/state/timer/run/attributes"])["finishes_at"]
    run(b.handle_command("mirror/cmd/timer.run/set", "idle"))
    assert ha.calls == []


def test_a_writable_script_is_a_button_that_runs_it():
    spec = classes.describe("script.start", SCRIPT, True)
    assert spec.component == "button"
    assert classes.describe("script.start", SCRIPT, False) is None
    b, ha, _ = bridge([SCRIPT])
    run(b.handle_command("mirror/cmd/script.start/press", "PRESS"))
    run(b.handle_command("mirror/cmd/script.start/press", "on"))
    assert ha.calls == [("script", "turn_on", {"entity_id": "script.start"}, {})]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok  ", name)
        except Exception as ex:                                   # noqa: BLE001
            failed += 1
            print("FAIL", name, repr(ex))
    print("%d ok, %d failed" % (len(tests) - failed, failed))
    sys.exit(1 if failed else 0)

"""Buttons and date/time helpers on the mirror (0.5.0): what each becomes, and
that a command is checked before any service is called."""
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


def bridge(states, writable=True):
    ha, broker = FakeHA(states), FakeBroker()
    b = Bridge(Config(), ha, broker)
    b._members = {s["entity_id"]: writable for s in states}
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

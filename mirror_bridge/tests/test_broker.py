"""The broker wrapper (0.11.0): the will that says the add-on is gone, retained
commands refused, and reading back what the broker holds."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mirror_bridge.broker import Broker                           # noqa: E402
from mirror_bridge.config import Config                           # noqa: E402

ALIVE = "mirror/state/_bridge/alive"


class Message:
    def __init__(self, topic, payload, retain=False):
        self.topic, self.payload, self.retain = topic, payload.encode(), retain


class FakeClient:
    """Answers a subscription the way the broker does: with what it holds."""

    def __init__(self, deliver, held):
        self.deliver, self.held = deliver, held
        self.subscribed, self.unsubscribed, self.published = [], [], []

    def subscribe(self, pattern, qos=0):
        self.subscribed.append(pattern)
        for message in self.held:
            if message.topic.startswith(pattern.rstrip("#")):
                self.deliver(self, None, message)

    def unsubscribe(self, pattern):
        self.unsubscribed.append(pattern)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def broker():
    seen = []
    return Broker(Config(), lambda topic, payload: seen.append((topic, payload))), seen


def test_the_will_says_the_addon_is_gone():
    b, _seen = broker()
    # paho 2.1.0, pinned in the Dockerfile, keeps the will in these.
    assert b._client._will_topic == ALIVE.encode()
    assert b._client._will_payload == b"0"
    assert b._client._will_retain is True


def test_liveness_is_said_again_on_every_connect():
    b, _seen = broker()
    b._client = client = FakeClient(b._message, [])
    b.set_alive(True)
    client.published.clear()
    b._connected(client, None, None, 0)
    assert (ALIVE, "1", True) in client.published
    assert "mirror/cmd/#" in client.subscribed


def test_a_retained_command_is_ignored():
    b, seen = broker()
    b._message(None, None, Message("mirror/cmd/switch.pump/set", "on", retain=True))
    assert seen == []
    b._message(None, None, Message("mirror/cmd/switch.pump/set", "on"))
    assert seen == [("mirror/cmd/switch.pump/set", "on")]


def test_retained_reads_back_what_the_broker_holds_and_only_that():
    b, seen = broker()
    held = [Message("mirror/discovery/sensor/sensor_a/config", "{}", retain=True),
            Message("mirror/state/sensor/a/state", "1", retain=True),
            Message("mirror/state/sensor/b/state", "", retain=True),      # cleared
            Message("mirror/state/sensor/c/state", "2", retain=False)]    # passing by
    b._client = client = FakeClient(b._message, held)
    found = b.retained(("mirror/discovery/#", "mirror/state/#"), quiet=0.2)
    assert found == {"mirror/discovery/sensor/sensor_a/config": "{}",
                     "mirror/state/sensor/a/state": "1"}
    assert client.unsubscribed == ["mirror/discovery/#", "mirror/state/#"]
    # None of it is a command, and nothing is collected once it has returned.
    b._message(None, None, Message("mirror/state/sensor/d/state", "3", retain=True))
    assert seen == [] and "mirror/state/sensor/d/state" not in found

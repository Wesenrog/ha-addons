import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fake_homely import FakeHomely  # noqa: E402
from harness import Collector, TestBroker  # noqa: E402

from homely_bridge import bridge as bridge_module  # noqa: E402
from homely_bridge.api import HomelyAPI  # noqa: E402
from homely_bridge.bridge import Bridge  # noqa: E402
from homely_bridge.config import Config  # noqa: E402
from homely_bridge.mqtt import MqttPublisher  # noqa: E402


@pytest.fixture
def home_payload():
    return json.loads((Path(__file__).parent / "home.json").read_text())


@pytest.fixture
def broker():
    b = TestBroker()
    yield b
    b.stop()


@pytest.fixture
def fake(home_payload):
    server = FakeHomely(home_payload)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def start(fake, broker, monkeypatch):
    """Start the bridge against the fake server and broker; returns (bridge, collector)."""
    monkeypatch.setattr(bridge_module, "POLL_BACKOFF", (1, 1, 1))
    monkeypatch.setattr(bridge_module, "WS_BACKOFF", (1, 1, 1))
    monkeypatch.setattr(bridge_module, "MIN_POLL_GAP", 0.5)
    running = []

    def _start(ws_url=None, **overrides):
        collector = Collector(broker.port)
        options = dict(
            username=fake.username, password=fake.password, poll_interval=300, stale_after=3,
            mqtt_host="127.0.0.1", mqtt_port=broker.port, api_url=fake.api_url, ws_url=ws_url or fake.ws_url,
        )
        options.update(overrides)
        cfg = Config(**options)
        holder = {}
        publisher = MqttPublisher(
            cfg.mqtt_host, cfg.mqtt_port, None, None, "homely/bridge/availability", "homeassistant",
            on_reconnect=lambda: holder["bridge"].republish_all(),
        )
        assert publisher.start(10)
        bridge = Bridge(cfg, HomelyAPI(cfg.username, cfg.password, base_url=cfg.api_url), publisher)
        holder["bridge"] = bridge
        stop = threading.Event()

        def run():
            if bridge.setup(stop):
                bridge.run(stop)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        running.append((stop, thread, bridge, publisher, collector))
        return bridge, collector

    yield _start
    for stop, thread, bridge, publisher, collector in running:
        stop.set()
        thread.join(10)
        bridge.close()
        publisher.stop()
        collector.stop()


@pytest.fixture
def running(start):
    return start()

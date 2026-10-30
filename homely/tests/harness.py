"""Shared test harness: MQTT broker, collector, fake Homely and a running bridge."""

import asyncio
import json
import threading
import time

import paho.mqtt.client as mqtt
from amqtt.broker import Broker

from fake_homely import free_port

LOCATION = "182bb447-8fa3-4fab-aa1a-5f01d15d6b59"
MOTION = "60566ab4-114b-44f9-b355-b08eae8e2bb8"
DOOR = "aa6d0206-bfcc-4c8b-83f1-c23d7270fe00"


def wait_for(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


class TestBroker:
    """An amqtt broker in its own thread. Retained messages live in memory only."""

    def __init__(self):
        self.port = free_port()
        self._loop = None
        self._broker = None
        self.start()

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._broker = Broker(
            {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{self.port}"}}}, loop=self._loop
        )
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._broker.start())
            ready.set()
            self._loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        assert ready.wait(10)

    def stop(self):
        asyncio.run_coroutine_threadsafe(self._broker.shutdown(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)

    def restart(self):
        self.stop()
        time.sleep(0.5)
        self.start()


class Collector:
    """Subscribes to everything; keeps the latest payload and the full history."""

    def __init__(self, port):
        self.messages = {}
        self.history = []
        self._lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self._on_message
        self.client.on_connect = lambda c, *a: c.subscribe("#")
        self.client.reconnect_delay_set(min_delay=1, max_delay=2)
        self.client.connect("127.0.0.1", port)
        self.client.loop_start()

    def _on_message(self, client, userdata, message):
        with self._lock:
            payload = message.payload.decode()
            self.messages[message.topic] = payload
            self.history.append((time.monotonic(), message.topic, payload))

    def json(self, topic):
        value = self.messages.get(topic)
        return json.loads(value) if value else None

    def values(self, topic, since=0.0):
        with self._lock:
            return [p for t0, t, p in self.history if t == topic and t0 >= since]

    def clear(self):
        with self._lock:
            self.messages.clear()
            self.history.clear()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

"""Thin paho-mqtt wrapper: publish retained, subscribe to the command tree."""
import logging, threading, time

import paho.mqtt.client as mqtt

_LOG = logging.getLogger(__name__)


class Broker:
    def __init__(self, cfg, on_command, on_reconnect=None):
        self._cfg = cfg
        self._on_command = on_command
        # Called after every successful connect, including reconnects. IL2
        # Bridge taught this lesson the hard way: a client that only publishes
        # its state once at startup is silently wrong for as long as the broker
        # remembers a stale retained value.
        self._on_reconnect = on_reconnect
        self._connections = 0
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if cfg.mqtt_user:
            self._client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        # The broker says "0" for us if we go without saying it ourselves, so
        # the mirror greys out when the add-on dies rather than showing stale
        # values as live.
        self._client.will_set(cfg.alive_topic, "0", qos=1, retain=True)
        self._alive = "0"
        self._client.on_connect = self._connected
        self._client.on_message = self._message
        self._ready = threading.Event()
        self._collected = None      # topic -> payload, while retained() reads
        self._heard = 0.0

    def start(self):
        self._client.connect(self._cfg.mqtt_host, self._cfg.mqtt_port, keepalive=60)
        self._client.loop_start()
        if not self._ready.wait(timeout=30):
            raise RuntimeError(
                "no usable broker connection after 30s - check credentials")

    def stop(self):
        """Say "0" and leave. A clean disconnect sends no will, so a stop has
        to say it itself."""
        self._alive = "0"
        info = self._client.publish(self._cfg.alive_topic, "0", qos=1, retain=True)
        try:
            info.wait_for_publish(timeout=2)
        except (RuntimeError, ValueError):
            pass
        self._client.disconnect()
        self._client.loop_stop()

    def _connected(self, client, userdata, flags, reason_code, properties=None):
        # paho calls this for failures too, so the reason code decides. Logging
        # unconditionally here hides an auth-rejection reconnect loop behind a
        # cheerful "connected" line, which is exactly what it did once.
        if reason_code != 0:
            _LOG.error("broker refused the connection as %r: %s",
                       self._cfg.mqtt_user, reason_code)
            return
        _LOG.info("connected to %s:%s as %r",
                  self._cfg.mqtt_host, self._cfg.mqtt_port, self._cfg.mqtt_user)
        client.subscribe(f"{self._cfg.base_topic}/cmd/#", qos=1)
        # The will fired when the last connection dropped, so say it again.
        client.publish(self._cfg.alive_topic, self._alive, qos=1, retain=True)
        self._ready.set()
        self._connections += 1
        if self._connections > 1 and self._on_reconnect:
            _LOG.info("reconnected - re-announcing every mirrored entity")
            self._on_reconnect()

    def _message(self, client, userdata, message):
        if not message.topic.startswith(f"{self._cfg.base_topic}/cmd/"):
            # Only retained() subscribes to anything else, and it wants what
            # the broker holds, not what passes by while it listens.
            collected = self._collected
            if collected is not None and message.retain:
                self._heard = time.monotonic()
                if message.payload:
                    collected[message.topic] = message.payload.decode(errors="replace")
            return
        if message.retain:
            # A retained command would run again at every start.
            _LOG.warning("ignoring retained command on %s", message.topic)
            return
        try:
            self._on_command(message.topic, message.payload.decode())
        except Exception:
            _LOG.exception("command handling failed for %s", message.topic)

    def publish(self, topic, payload, retain=True):
        self._client.publish(topic, payload, qos=1, retain=retain)

    def retract(self, topic):
        """An empty retained payload removes a discovered entity cleanly -
        the thing plain YAML cannot do, and why removals leave no orphans."""
        self._client.publish(topic, "", qos=1, retain=True)

    def set_alive(self, alive):
        self._alive = "1" if alive else "0"
        self._client.publish(self._cfg.alive_topic, self._alive, qos=1, retain=True)

    def retained(self, patterns, quiet=2.0, limit=15.0):
        """What the broker holds retained under `patterns`: topic -> payload.

        The broker sends its retained messages right after a subscription, so
        this subscribes, listens until `quiet` seconds pass without one, and
        unsubscribes. It blocks, so call it from a thread. Hearing nothing
        returns nothing, which costs a retraction and never causes one.
        """
        collected = self._collected = {}
        started = self._heard = time.monotonic()
        for pattern in patterns:
            self._client.subscribe(pattern, qos=1)
        while True:
            time.sleep(0.1)
            now = time.monotonic()
            if now - self._heard >= quiet or now - started >= limit:
                break
        for pattern in patterns:
            self._client.unsubscribe(pattern)
        self._collected = None
        return collected

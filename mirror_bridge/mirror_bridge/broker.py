"""Thin paho-mqtt wrapper: publish retained, subscribe to the command tree."""
import logging, threading

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
        self._client.on_connect = self._connected
        self._client.on_message = self._message
        self._ready = threading.Event()

    def start(self):
        self._client.connect(self._cfg.mqtt_host, self._cfg.mqtt_port, keepalive=60)
        self._client.loop_start()
        if not self._ready.wait(timeout=30):
            raise RuntimeError(
                "no usable broker connection after 30s - check credentials")

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
        self._ready.set()
        self._connections += 1
        if self._connections > 1 and self._on_reconnect:
            _LOG.info("reconnected - re-announcing every mirrored entity")
            self._on_reconnect()

    def _message(self, client, userdata, message):
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

"""Thin paho-mqtt wrapper: last will, reconnect, and callbacks for the app.

paho runs its own thread; every callback here only hands over to the worker.
"""
import logging

import paho.mqtt.client as mqtt

_LOG = logging.getLogger(__name__)


class MqttLink:
    def __init__(self, cfg, subscriptions, on_message, on_connect, on_disconnect, client_id=""):
        self._cfg = cfg
        self._subscriptions = subscriptions
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if cfg.mqtt_user:
            self._client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        # The broker marks every entity unavailable if this add-on vanishes.
        self._client.will_set(cfg.availability_topic, "offline", qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._connected
        self._client.on_disconnect = self._disconnected
        self._client.on_message = self._message

    def start(self):
        # Asynchronous: a broker that is down at start is waited for, not fatal.
        self._client.connect_async(self._cfg.mqtt_host, self._cfg.mqtt_port, keepalive=60)
        self._client.loop_start()

    def stop(self):
        self._client.disconnect()
        self._client.loop_stop()

    def _connected(self, client, userdata, flags, reason_code, properties=None):
        # paho calls this for refusals too, so the reason code decides.
        if reason_code.is_failure:
            _LOG.error("broker refused the connection as %r: %s", self._cfg.mqtt_user, reason_code)
            return
        _LOG.info("connected to %s:%s", self._cfg.mqtt_host, self._cfg.mqtt_port)
        for topic in self._subscriptions:
            client.subscribe(topic, qos=1)
        self._on_connect()

    def _disconnected(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            _LOG.warning("disconnected from the broker (%s) - reconnecting", reason_code)
        else:
            _LOG.info("disconnected from the broker")
        self._on_disconnect()

    def _message(self, client, userdata, message):
        self._on_message(message.topic, message.payload, bool(message.retain))

    def publish(self, topic, payload, retain=True):
        self._client.publish(topic, payload, qos=1, retain=retain)

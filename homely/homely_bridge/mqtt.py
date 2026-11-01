"""MQTT connection and Home Assistant discovery messages."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .model import EntitySpec, entity_id_suffix, entity_name

_LOGGER = logging.getLogger(__name__)

ORIGIN = {
    "name": "Homely add-on",
    "support_url": "https://github.com/wesenrog/ha-addons",
}


class MqttPublisher:
    def __init__(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        availability_topic: str,
        discovery_prefix: str,
        on_reconnect: Callable[[], None] | None = None,
    ):
        self.availability_topic = availability_topic
        self.discovery_prefix = discovery_prefix
        self._on_reconnect = on_reconnect
        self._host = host
        self._port = port
        self._connected = threading.Event()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"homely-addon-{uuid.uuid4().hex[:8]}",
        )
        if username:
            self._client.username_pw_set(username, password or None)
        self._client.will_set(availability_topic, "offline", qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

    def start(self, timeout: float = 30.0) -> bool:
        _LOGGER.info("Connecting to MQTT broker %s:%d", self._host, self._port)
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()
        return self._connected.wait(timeout)

    def stop(self) -> None:
        if self._connected.is_set():
            self._client.publish(self.availability_topic, "offline", qos=1, retain=True).wait_for_publish(5)
        self._client.disconnect()
        self._client.loop_stop()

    def _handle_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            _LOGGER.error("MQTT connection refused: %s", reason_code)
            return
        _LOGGER.info("Connected to MQTT broker")
        client.subscribe(f"{self.discovery_prefix}/status")
        client.publish(self.availability_topic, "online", qos=1, retain=True)
        first = not self._connected.is_set()
        self._connected.set()
        if not first and self._on_reconnect:
            # The broker may have lost retained messages; send everything again
            self._on_reconnect()

    def _handle_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            _LOGGER.warning("MQTT connection lost (%s), reconnecting", reason_code)

    def _handle_message(self, client, userdata, message) -> None:
        if message.topic == f"{self.discovery_prefix}/status" and message.payload == b"online":
            _LOGGER.info("Home Assistant came online, republishing")
            if self._on_reconnect:
                self._on_reconnect()

    def publish(self, topic: str, payload: Any, retain: bool = True) -> None:
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self._client.publish(topic, payload, qos=1, retain=retain)


def discovery_topic(prefix: str, component: str, node_id: str, key: str) -> str:
    return f"{prefix}/{component}/homely_{node_id}/{key}/config"


def discovery_payload(
    spec: EntitySpec,
    unique_prefix: str,
    state_topic: str,
    availability_topics: list[str],
    device: dict[str, Any],
    language: str,
    entity_id_prefix: str,
    json_attributes_topic: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": entity_name(spec.name_id, language),
        # unique_id is unchanged since 0.1.0; never change it, HA keys its registry on it
        "unique_id": f"{unique_prefix}_{spec.key}",
        # Language-independent entity id, used the first time HA sees the entity
        "default_entity_id": f"{spec.component}.{entity_id_prefix}_{entity_id_suffix(spec)}",
        "state_topic": state_topic,
        "value_template": f"{{{{ value_json.get('{spec.key}') }}}}",
        # Available only while every listed topic says "online"
        "availability": [{"topic": topic} for topic in availability_topics],
        "availability_mode": "all",
        "device": device,
        "origin": ORIGIN,
    }
    if spec.component == "binary_sensor":
        payload["payload_on"] = "ON"
        payload["payload_off"] = "OFF"
    if spec.device_class:
        payload["device_class"] = spec.device_class
    if spec.unit:
        payload["unit_of_measurement"] = spec.unit
    if spec.state_class:
        payload["state_class"] = spec.state_class
    if spec.entity_category:
        payload["entity_category"] = spec.entity_category
    if not spec.enabled_by_default:
        payload["enabled_by_default"] = False
    if spec.icon:
        payload["icon"] = spec.icon
    if json_attributes_topic:
        payload["json_attributes_topic"] = json_attributes_topic
    payload.update(spec.extra)
    return payload

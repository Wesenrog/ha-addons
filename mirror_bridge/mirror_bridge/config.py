"""Runtime configuration, all of it from the environment set up by run.sh."""
import os


class Config:
    def __init__(self):
        self.label = os.environ.get("MIRROR_LABEL", "mirror")
        self.label_rw = os.environ.get("MIRROR_LABEL_RW", "mirror-rw")
        self.base_topic = os.environ.get("MIRROR_BASE_TOPIC", "mirror").rstrip("/")
        self.discovery_prefix = os.environ.get(
            "MIRROR_DISCOVERY_PREFIX", "mirror/discovery").rstrip("/")
        self.device_name = os.environ.get("MIRROR_DEVICE_NAME", "Pi mirror")
        self.log_level = os.environ.get("MIRROR_LOG_LEVEL", "info").upper()

        self.mqtt_host = os.environ.get("MQTT_HOST", "core-mosquitto")
        self.mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
        self.mqtt_user = os.environ.get("MQTT_USER") or None
        self.mqtt_password = os.environ.get("MQTT_PASSWORD") or None

        # Provided to add-ons declaring homeassistant_api: true.
        self.supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")

    # --- topic layout, in one place so both halves agree --------------------
    def state_topic(self, entity_id):
        return f"{self.base_topic}/state/{entity_id.replace('.', '/', 1)}/state"

    def attribute_topic(self, entity_id, attribute):
        return f"{self.base_topic}/state/{entity_id.replace('.', '/', 1)}/{attribute}"

    def command_topic(self, entity_id, capability):
        return f"{self.base_topic}/cmd/{entity_id}/{capability}"

    def discovery_topic(self, component, entity_id):
        return f"{self.discovery_prefix}/{component}/{entity_id.replace('.', '_')}/config"

    @property
    def availability_topic(self):
        # Published by the mosquitto bridge's own will, so the mirror greys out
        # when the tunnel drops rather than showing stale values as live.
        return f"{self.base_topic}/link"

"""Runtime configuration, all of it from the environment set up by run.sh.

Tests build a Config directly with keyword overrides instead.
"""
import os
from dataclasses import dataclass, field, fields


def _env(name, default):
    return os.environ.get(f"ZW_{name.upper()}", default)


@dataclass
class Config:
    z2m_base_topic: str = "zigbee2mqtt"
    base_topic: str = "zigbee_watch"
    discovery_prefix: str = "homeassistant"
    db_path: str = "/share/zigbee_watch/zigbee_watch.sqlite"
    timezone: str = "Europe/Oslo"
    retention_days: int = 400
    log_level: str = "INFO"

    # Nightly network map.
    scan_time: str = "02:15"
    scan_timeout_min: int = 15
    scan_restart_guard_min: int = 30

    # Link quality is thinned to the last value per device per bucket.
    lqi_bucket_min: int = 10

    # Change detection.
    lqi_drop: int = 25
    lqi_min_samples: int = 5
    error_min_count: int = 10
    error_factor: float = 4.0
    error_baseline_days: int = 14
    # The same timeouts as Zigbee2MQTT's availability (active / passive).
    timeout_mains_min: int = 60
    timeout_battery_min: int = 1500
    event_finding_days: int = 7

    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_user: str | None = None
    mqtt_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls):
        values = {}
        for f in fields(cls):
            if f.name.startswith("mqtt_"):
                raw = os.environ.get(f.name.upper())
            else:
                raw = _env(f.name, None)
            if raw is None or raw == "":
                continue
            if f.type is int:
                values[f.name] = int(raw)
            elif f.type is float:
                values[f.name] = float(raw)
            else:
                values[f.name] = raw
        cfg = cls(**values)
        cfg.log_level = cfg.log_level.upper()
        return cfg

    # --- topic layout, in one place ------------------------------------------
    @property
    def availability_topic(self):
        return f"{self.base_topic}/availability"

    def state_topic(self, key):
        return f"{self.base_topic}/{key}"

    def discovery_topic(self, component, object_id):
        return f"{self.discovery_prefix}/{component}/zigbee_watch/{object_id}/config"

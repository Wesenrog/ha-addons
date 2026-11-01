"""Add-on options, read from /data/options.json with environment overrides."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api import DEFAULT_BASE_URL
from .model import LANGUAGES
from .websocket import DEFAULT_WS_URL

OPTIONS_FILE = "/data/options.json"


@dataclass
class Config:
    username: str
    password: str
    home: str = ""
    poll_interval: int = 3600
    discovery_prefix: str = "homeassistant"
    base_topic: str = "homely"
    log_level: str = "info"
    language: str = "nb"
    stale_after: int = 120
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    api_url: str = DEFAULT_BASE_URL
    ws_url: str = DEFAULT_WS_URL


def _env(name: str, default: Any) -> Any:
    value = os.environ.get(name)
    return default if value in (None, "") else value


def _language(value: Any) -> str:
    value = str(value).lower()
    return value if value in LANGUAGES else "nb"


def load() -> Config:
    path = Path(os.environ.get("HOMELY_OPTIONS_FILE", OPTIONS_FILE))
    options: dict[str, Any] = json.loads(path.read_text()) if path.is_file() else {}

    def opt(key: str, env: str, default: Any) -> Any:
        value = options.get(key)
        return _env(env, default if value in (None, "") else value)

    return Config(
        username=str(opt("username", "HOMELY_USERNAME", "")).strip(),
        password=str(opt("password", "HOMELY_PASSWORD", "")),
        home=str(opt("home", "HOMELY_HOME", "")),
        poll_interval=max(300, int(opt("poll_interval", "HOMELY_POLL_INTERVAL", 3600))),
        discovery_prefix=str(opt("discovery_prefix", "MQTT_DISCOVERY_PREFIX", "homeassistant")).strip("/"),
        base_topic=str(opt("base_topic", "MQTT_BASE_TOPIC", "homely")).strip("/"),
        log_level=str(opt("log_level", "LOG_LEVEL", "info")).lower(),
        language=_language(opt("language", "HOMELY_LANGUAGE", "nb")),
        stale_after=min(3600, max(30, int(opt("stale_after", "HOMELY_STALE_AFTER", 120)))),
        # The MQTT settings come from run.sh (Supervisor service or manual options)
        mqtt_host=_env("MQTT_HOST", "core-mosquitto"),
        mqtt_port=int(_env("MQTT_PORT", 1883)),
        mqtt_username=_env("MQTT_USERNAME", ""),
        mqtt_password=_env("MQTT_PASSWORD", ""),
        api_url=_env("HOMELY_API_URL", DEFAULT_BASE_URL),
        ws_url=_env("HOMELY_WS_URL", DEFAULT_WS_URL),
    )

"""The few Home Assistant entities, by MQTT discovery. No per-device entities.

Ids are fixed with `default_entity_id`, so rewording a name never moves them.
"""
import json
from datetime import datetime, timezone

from . import __version__

DEVICE = {
    "identifiers": ["zigbee_watch"],
    "name": "Zigbee Watch",
    "manufacturer": "wesenrog/ha-addons",
    "model": "Zigbee Watch",
    "sw_version": __version__,
}

LOWER_BOUND_NOTE = ("Nedre grense: Zigbee2MQTT sender ikke en logglinje som er lik den forrige, "
                    "så tallene blir lavere enn i loggfilene.")
# Below this share of the last 24 h listened to, the count is not trusted.
MIN_COVERAGE = 0.9
LATEST_FINDINGS = 10
TOP_DEVICES = 5

# object_id -> (component, name, extra discovery keys)
ENTITIES = {
    "funn": ("sensor", "Funn", {"icon": "mdi:magnify-scan", "state_class": "measurement"}),
    "rutefeil_24t": ("sensor", "Rutefeil siste 24 t",
                     {"icon": "mdi:transit-detour", "state_class": "measurement"}),
    "siste_kart": ("sensor", "Siste nettverkskart", {"device_class": "timestamp"}),
    "kartstatus": ("sensor", "Kartstatus", {"icon": "mdi:graph-outline"}),
    "tilkoblet": ("binary_sensor", "Zigbee2MQTT tilkoblet",
                  {"device_class": "connectivity", "entity_category": "diagnostic"}),
}


def discovery(cfg):
    """(topic, payload) per entity."""
    out = []
    for object_id, (component, name, extra) in ENTITIES.items():
        topic_base = cfg.state_topic(object_id)
        payload = {
            "name": name,
            "unique_id": f"zigbee_watch_{object_id}",
            "default_entity_id": f"{component}.zigbee_watch_{object_id}",
            "has_entity_name": True,
            "device": DEVICE,
            "state_topic": f"{topic_base}/state",
            "availability": [{"topic": cfg.availability_topic}],
            **extra,
        }
        if object_id in ("funn", "rutefeil_24t"):
            payload["json_attributes_topic"] = f"{topic_base}/attributes"
        if object_id == "rutefeil_24t":
            # Also unavailable on its own when the count cannot be trusted.
            payload["availability"].append({"topic": f"{topic_base}/available"})
            payload["availability_mode"] = "all"
        out.append((cfg.discovery_topic(component, object_id), json.dumps(payload)))
    return out


def states(watch, now):
    """topic -> payload for every state and attribute topic."""
    cfg = watch.cfg
    out = {}

    findings = watch.detector.open_findings()
    out[cfg.state_topic("funn") + "/state"] = str(len(findings))
    out[cfg.state_topic("funn") + "/attributes"] = json.dumps({
        "funn": [f"{watch.local.format(f['ts'])} {f['text']}" for f in findings[:LATEST_FINDINGS]],
    }, ensure_ascii=False)

    since = now - 86400
    rows = watch.store.all(
        "SELECT device_id, COUNT(*) AS n FROM log_event WHERE kind = 'route_error' AND ts >= ?"
        " GROUP BY device_id ORDER BY n DESC", (since,))
    total = sum(r["n"] for r in rows)
    coverage = watch.detector.observed_listening(since, now)
    top = {}
    for r in rows:
        if r["device_id"] is None:
            continue
        top[watch.detector.name_of(r["device_id"])] = r["n"]
        if len(top) == TOP_DEVICES:
            break
    unknown = sum(r["n"] for r in rows if r["device_id"] is None)
    attrs = {"enheter": top,
             "dekning": {"lyttet_andel": round(coverage, 3), "merknad": LOWER_BOUND_NOTE}}
    if unknown:
        attrs["ukjent_adresse"] = unknown
    base = cfg.state_topic("rutefeil_24t")
    out[base + "/state"] = str(total)
    out[base + "/attributes"] = json.dumps(attrs, ensure_ascii=False)
    out[base + "/available"] = "online" if coverage >= MIN_COVERAGE else "offline"

    last_ok = watch.store.one("SELECT finished FROM scan WHERE status = 'ok' ORDER BY id DESC LIMIT 1")
    out[cfg.state_topic("siste_kart") + "/state"] = (
        datetime.fromtimestamp(last_ok["finished"], timezone.utc).isoformat() if last_ok else "None")
    latest = watch.scanner.latest()
    status = "None"
    if latest:
        status = "running" if latest["status"] == "running" else latest["status"]
    out[cfg.state_topic("kartstatus") + "/state"] = status[:255]

    z2m = watch.ingest.z2m_state
    out[cfg.state_topic("tilkoblet") + "/state"] = {"online": "ON", "offline": "OFF"}.get(z2m, "None")
    return out

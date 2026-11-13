"""Orchestration: membership from labels, state out, commands in.

The invariant worth keeping in mind while reading: *membership is the
authorisation*. An entity is mirrored because it carries the label, and it is
writable because it carries the writable label. Nothing the mirror sends can
widen that, because every command is checked against the current membership set
before a service is ever called.
"""
import datetime, json, logging, re

from . import classes

_LOG = logging.getLogger(__name__)

# A button and a scene have no state on the MQTT platform.
STATELESS = ("button", "scene")


class Bridge:
    def __init__(self, cfg, ha, broker):
        self._cfg = cfg
        self._ha = ha
        self._broker = broker
        self._members = {}      # entity_id -> writable(bool)
        # entity_id -> {"device": <source device name or None>,
        #               "name": <short entity name or None>}
        # Home Assistant composes a friendly name from the device and the
        # entity's own short name; mirroring that structure rather than the
        # already-composed string is what keeps names from doubling up.
        self._meta = {}
        self._published = {}    # entity_id -> component, for clean retraction
        self._topics = {}       # entity_id -> its state topics, likewise
        self._available = {}    # entity_id -> the availability last published
        self._unsupported = set()

    # --- membership ---------------------------------------------------------
    async def refresh_members(self):
        """Recompute the mirrored set from the label and entity registries.

        Called at startup and whenever the entity registry changes, so applying
        a label in the UI is the entire act of adding an entity to the mirror.
        """
        labels = await self._ha.label_registry()
        by_name = {l["name"]: l["label_id"] for l in labels}
        want = by_name.get(self._cfg.label)
        want_rw = by_name.get(self._cfg.label_rw)
        if want is None:
            _LOG.warning("label %r does not exist yet - nothing to mirror",
                         self._cfg.label)

        entities = await self._ha.entity_registry()
        devices = {d["id"]: (d.get("name_by_user") or d.get("name"))
                   for d in await self._ha.device_registry()}
        members = {}
        for entry in entities:
            entity_labels = entry.get("labels") or []
            if want and want in entity_labels:
                members[entry["entity_id"]] = bool(want_rw and want_rw in entity_labels)
                self._meta[entry["entity_id"]] = {
                    "device": devices.get(entry.get("device_id")),
                    # A user rename on the source wins; original_name is the
                    # short form ("Temperature"); None means the entity takes
                    # its device's name, which is how Zigbee2MQTT presents a
                    # thermostat.
                    "name": entry.get("name") or entry.get("original_name"),
                }

        added = set(members) - set(self._members)
        removed = set(self._members) - set(members)
        changed = {e for e in set(members) & set(self._members)
                   if members[e] != self._members[e]}
        self._members = members

        for entity_id in removed:
            self._retract(entity_id)
        if added or changed:
            await self._announce(added | changed)
        if added or removed or changed:
            _LOG.info("membership: %d mirrored (+%d, -%d, ~%d)",
                      len(members), len(added), len(removed), len(changed))

    # --- publishing ---------------------------------------------------------
    async def _announce(self, entity_ids):
        """Publish discovery and current state for the given entities."""
        states = {s["entity_id"]: s for s in await self._ha.get_states()}
        for entity_id in sorted(entity_ids):
            state = states.get(entity_id)
            if state is None:
                _LOG.warning("%s is labelled but has no state", entity_id)
                continue
            self.publish_entity(entity_id, state, announce=True)

    def publish_entity(self, entity_id, state, announce=False):
        writable = self._members.get(entity_id, False)
        spec = classes.describe(entity_id, state, writable,
                                relay=self._cfg.relays.get(entity_id))
        if spec is None:
            # A class that went with the writable label - a button made
            # read-only - leaves the mirror with it.
            self._retract(entity_id)
            if entity_id not in self._unsupported:
                self._unsupported.add(entity_id)
                _LOG.warning("no mirror class for %s - ignoring", entity_id)
            return
        self._unsupported.discard(entity_id)

        cfg = self._cfg
        # A member that had no state when it was labelled is announced by the
        # first state it gets.
        if announce or entity_id not in self._published:
            announce = True
            before = self._published.get(entity_id)
            if before and before != spec.component:
                # The writable label changes the platform (sensor to number),
                # and the old one's discovery would otherwise stay behind.
                self._broker.retract(cfg.discovery_topic(before, entity_id))
            topics = self._state_topics(entity_id, spec)
            for topic in self._topics.get(entity_id, set()) - topics:
                self._broker.retract(topic)
            self._broker.publish(cfg.discovery_topic(spec.component, entity_id),
                                 json.dumps(self._discovery(entity_id, state, spec)))
            self._published[entity_id] = spec.component
            self._topics[entity_id] = topics

        # Availability first, so the mirror never holds a state it refuses. A
        # source that is unavailable is unavailable here; one that is unknown
        # is unknown ("None" to every MQTT platform that has a state), except
        # where the class says the platform cannot hold that.
        value = state.get("state")
        available = value not in ("unavailable", None) and not (
            spec.refuses_unknown and value == "unknown")
        if announce or self._available.get(entity_id) != available:
            self._available[entity_id] = available
            self._broker.publish(cfg.attribute_topic(entity_id, "available"),
                                 "1" if available else "0")
        if available and spec.component not in STATELESS:
            self._broker.publish(cfg.state_topic(entity_id),
                                 "None" if value == "unknown" else str(value))
        attributes = state.get("attributes", {}) or {}
        if spec.json_attributes:
            self._broker.publish(cfg.attribute_topic(entity_id, "attributes"),
                                 json.dumps(attributes, default=str))
        for name in spec.attributes:
            value = attributes.get(name)
            if value is not None:
                # Plain text, not JSON: see the note in classes.py. A pair - a
                # light's xy colour - goes as "x,y", the MQTT light's own form.
                if isinstance(value, (list, tuple)):
                    value = ",".join(str(v) for v in value)
                self._broker.publish(cfg.attribute_topic(entity_id, name), str(value))

    def _discovery(self, entity_id, state, spec):
        cfg = self._cfg
        attributes = state.get("attributes", {}) or {}
        meta = self._meta.get(entity_id, {})
        payload = {
            # Keep the mirror's entity_id tied to the source rather than to the
            # display name, so ids stay predictable when names change. The
            # mirror's own domain, the source's object id. `object_id` did
            # this before Home Assistant deprecated it; the mirror now ignores
            # it and names the entity after its display name.
            "default_entity_id": f"{spec.component}.{entity_id.split('.', 1)[1]}",
            "unique_id": f"mirror_{entity_id.replace('.', '_')}",
            # Live only while all three hold: the tunnel, this add-on, and the
            # source entity itself.
            "availability": [
                {"topic": t, "payload_available": "1", "payload_not_available": "0"}
                for t in (cfg.availability_topic, cfg.alive_topic,
                          cfg.attribute_topic(entity_id, "available"))],
            "availability_mode": "all",
            **spec.config,
        }

        if meta.get("device"):
            # Reproduce the source's own structure: a device carrying the long
            # name, and an entity carrying only the short part. Home Assistant
            # then composes exactly what the source shows. A null name means the
            # entity takes the device's name outright.
            payload["device"] = {
                "identifiers": [f"mirror_{cfg.base_topic}_{meta['device']}"],
                "name": meta["device"],
                "manufacturer": "mirror-bridge",
            }
            payload["has_entity_name"] = True
            payload["name"] = meta.get("name")
        else:
            # Helpers and template sensors have no device on the source either,
            # so they stay loose here and keep their own name. Attaching them to
            # a synthetic device would only prefix them.
            payload["name"] = meta.get("name") or attributes.get(
                "friendly_name", entity_id)

        if spec.component == "climate":
            # The one platform that does not call the entity state its "state":
            # for a thermostat that topic carries the hvac mode.
            payload["mode_state_topic"] = cfg.state_topic(entity_id)
        elif spec.component not in STATELESS:
            payload["state_topic"] = cfg.state_topic(entity_id)

        # Everything else a platform reads from its own topic - a thermostat's
        # current temperature, a light's brightness - is declared in the class.
        for key, attribute in spec.state_map.items():
            payload[key] = cfg.attribute_topic(entity_id, attribute)

        for capability, command in spec.commands.items():
            payload[command.topic_key] = cfg.command_topic(entity_id, capability)
        return payload

    def _state_topics(self, entity_id, spec):
        """Every retained topic the entity has under state/, so a retraction
        leaves none of them behind."""
        cfg = self._cfg
        names = {"available", *spec.attributes}
        if spec.json_attributes:
            names.add("attributes")
        if spec.component not in STATELESS:
            names.add("state")
        return {cfg.attribute_topic(entity_id, name) for name in names}

    def _retract(self, entity_id):
        component = self._published.pop(entity_id, None)
        topics = self._topics.pop(entity_id, set())
        self._available.pop(entity_id, None)
        if component:
            self._broker.retract(self._cfg.discovery_topic(component, entity_id))
            for topic in sorted(topics):
                self._broker.retract(topic)
            _LOG.info("retracted %s", entity_id)

    def reconcile(self, retained):
        """Retract what the broker holds for entities no longer mirrored.

        `retained` is topic -> payload for everything retained under the
        discovery prefix and under state/, read before this run published
        anything. Membership changes while the add-on runs are retracted as
        they happen; this is for the ones it was not running to see - a label
        taken off, or the writable label toggled, while it was down.
        """
        cfg = self._cfg
        keep = {cfg.alive_topic}
        waiting = set()
        for entity_id in self._members:
            component = self._published.get(entity_id)
            if component:
                keep.add(cfg.discovery_topic(component, entity_id))
                keep |= self._topics[entity_id]
            elif entity_id not in self._unsupported:
                # Labelled but without a state so far - an integration still
                # loading after a restart. What it had is left alone.
                waiting.add(entity_id)
        waiting_state = tuple(cfg.attribute_topic(e, "") for e in waiting)
        waiting_discovery = {e.replace(".", "_") for e in waiting}

        stale = []
        for topic, payload in retained.items():
            if topic in keep:
                continue
            if topic.startswith(f"{cfg.base_topic}/state/"):
                if not topic.startswith(waiting_state):
                    stale.append(topic)
            elif topic.startswith(f"{cfg.discovery_prefix}/"):
                # The prefix may be shared (`homeassistant`), so only a config
                # this add-on wrote is ever touched.
                if (self._is_ours(payload)
                        and topic.split("/")[-2] not in waiting_discovery):
                    stale.append(topic)
        for topic in sorted(stale):
            self._broker.retract(topic)
        if stale:
            _LOG.info("retracted %d stale topics left from an earlier run", len(stale))

    def _is_ours(self, payload):
        try:
            config = json.loads(payload)
        except ValueError:
            return False
        if not isinstance(config, dict):
            return False
        links = [config.get("availability_topic")]
        links += [a.get("topic") for a in config.get("availability") or []
                  if isinstance(a, dict)]
        return (str(config.get("unique_id", "")).startswith("mirror_")
                and self._cfg.availability_topic in links)

    async def reannounce(self):
        """Republish discovery and state for everything currently mirrored."""
        if self._members:
            await self._announce(set(self._members))

    # --- events -------------------------------------------------------------
    async def on_state_changed(self, event):
        entity_id = event["data"]["entity_id"]
        if entity_id not in self._members:
            return
        new_state = event["data"].get("new_state")
        if new_state:
            self.publish_entity(entity_id, new_state)

    async def on_registry_updated(self, event):
        await self.refresh_members()

    # --- the command path ---------------------------------------------------
    async def handle_command(self, topic, payload):
        """mirror/cmd/<entity_id>/<capability>, validated before acting."""
        parts = topic.split("/")
        if len(parts) != 4:
            _LOG.warning("ignoring malformed command topic %s", topic)
            return
        _, _, entity_id, capability = parts

        writable = self._members.get(entity_id)
        if writable is not True:
            # Either not mirrored at all, or mirrored read-only. Membership is
            # the authorisation, so this is the check that matters.
            _LOG.warning("rejecting command for %s: not writable", entity_id)
            return

        states = {s["entity_id"]: s for s in await self._ha.get_states()}
        state = states.get(entity_id)
        if state is None:
            return
        relay = self._cfg.relays.get(entity_id)
        spec = classes.describe(entity_id, state, True, relay=relay)
        if spec is None or capability not in classes.ALLOWED.get(spec.component, ()):
            _LOG.warning("rejecting %s on %s: capability not allowed",
                         capability, entity_id)
            return

        command = spec.commands.get(capability)
        if command is None:
            return
        value = self._validate(command, state, payload)
        if value is None:
            _LOG.warning("rejecting payload %r for %s/%s", payload, entity_id, capability)
            return

        if command.kind == "relay":
            # Passed on unchanged, and never retained: a retained command would
            # run again every time the program behind it subscribed.
            self._broker.publish(relay, payload, retain=False)
            _LOG.info("%s relayed %r to %s", entity_id, value.get("op"), relay)
            return
        domain = command.service.split(".", 1)[0]
        if command.kind == "onoff":
            service = "turn_on" if value else "turn_off"
            await self._ha.call_service(domain, service,
                                        {"entity_id": entity_id}, {})
        elif command.kind == "press":
            await self._ha.call_service(domain, command.service.split(".", 1)[1],
                                        {"entity_id": entity_id}, {})
        else:
            service = command.service.split(".", 1)[1]
            await self._ha.call_service(domain, service, {"entity_id": entity_id},
                                        {command.data_field: value})
        _LOG.info("%s %s -> %r", entity_id, capability, value)

    @staticmethod
    def _validate(command, state, payload):
        """Bounds come from the entity itself, never from configuration."""
        attributes = state.get("attributes", {}) or {}
        if command.kind == "onoff":
            text = payload.strip().lower()
            if text in ("on", "true", "1"):
                return True
            if text in ("off", "false", "0"):
                return False
            return None
        if command.kind == "enum":
            allowed = attributes.get(command.enum_attr) or []
            return payload if payload in allowed else None
        if command.kind == "number":
            try:
                value = float(payload)
            except ValueError:
                return None
            # An attribute wins over the fixed bound, so a device reporting its
            # own range is still clamped to what it actually accepts.
            low = attributes.get(command.min_attr, command.min_value)
            high = attributes.get(command.max_attr, command.max_value)
            if low is not None:
                value = max(value, float(low))
            if high is not None:
                value = min(value, float(high))
            return int(round(value)) if command.as_int else value
        if command.kind == "press":
            return True if payload.strip() == "PRESS" else None
        if command.kind == "datetime":
            return _datetime_value(command.data_field, payload.strip())
        if command.kind == "text":
            return _text_value(payload, attributes)
        if command.kind == "xy":
            return _xy_value(payload)
        if command.kind == "relay":
            return _relay_value(payload)
        return None


# set_datetime's field -> the formats accepted, and the one sent on.
_DATETIME_IN = {
    "datetime": (("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"), "%Y-%m-%d %H:%M:%S"),
    "date": (("%Y-%m-%d",), "%Y-%m-%d"),
    "time": (("%H:%M:%S", "%H:%M"), "%H:%M:%S"),
}


def _datetime_value(field_name, text):
    """A real date and time in the helper's form, or None. The pattern on the
    mirror is a hint to its UI; this is the check."""
    accepted, out = _DATETIME_IN[field_name]
    for fmt in accepted:
        try:
            return datetime.datetime.strptime(text, fmt).strftime(out)
        except ValueError:
            continue
    return None


def _text_value(text, attributes):
    """The text as sent, if it is within the source's length and matches its
    pattern - the checks `text.set_value` makes - or None."""
    low, high = int(attributes.get("min", 0)), int(attributes.get("max", 255))
    if not low <= len(text) <= high:
        return None
    pattern = attributes.get("pattern")
    if pattern:
        try:
            if not re.match(pattern, text):
                return None
        except re.error:
            return None
    return text


def _xy_value(text):
    """"x,y" -> [x, y], both in the CIE chromaticity range 0-1, or None."""
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        xy = [float(p) for p in parts]
    except ValueError:
        return None
    return xy if all(0.0 <= v <= 1.0 for v in xy) else None


RELAY_MAX = 16384     # bytes; a week's programme for a zone is well under 2 KB


def _relay_value(text):
    """A JSON object of at most RELAY_MAX bytes, or None. Only the shape is
    checked here; the program the relay feeds checks the content."""
    if len(text.encode()) > RELAY_MAX:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None

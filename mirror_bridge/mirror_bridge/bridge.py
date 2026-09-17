"""Orchestration: membership from labels, state out, commands in.

The invariant worth keeping in mind while reading: *membership is the
authorisation*. An entity is mirrored because it carries the label, and it is
writable because it carries the writable label. Nothing the mirror sends can
widen that, because every command is checked against the current membership set
before a service is ever called.
"""
import json, logging

from . import classes

_LOG = logging.getLogger(__name__)


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
        spec = classes.describe(entity_id, state, writable)
        if spec is None:
            if entity_id not in self._unsupported:
                self._unsupported.add(entity_id)
                _LOG.warning("no mirror class for %s - ignoring", entity_id)
            return

        cfg = self._cfg
        if announce:
            self._broker.publish(cfg.discovery_topic(spec.component, entity_id),
                                 json.dumps(self._discovery(entity_id, state, spec)))
            self._published[entity_id] = spec.component

        self._broker.publish(cfg.state_topic(entity_id), str(state.get("state")))
        attributes = state.get("attributes", {}) or {}
        for name in spec.attributes:
            value = attributes.get(name)
            if value is not None:
                # Plain text, not JSON: see the note in classes.py.
                self._broker.publish(cfg.attribute_topic(entity_id, name), str(value))

    def _discovery(self, entity_id, state, spec):
        cfg = self._cfg
        attributes = state.get("attributes", {}) or {}
        meta = self._meta.get(entity_id, {})
        payload = {
            # Keep the mirror's entity_id tied to the source rather than to the
            # display name, so ids stay predictable when names change.
            "object_id": entity_id.split(".", 1)[1],
            "unique_id": f"mirror_{entity_id.replace('.', '_')}",
            "availability_topic": cfg.availability_topic,
            "payload_available": "1",
            "payload_not_available": "0",
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
        else:
            payload["state_topic"] = cfg.state_topic(entity_id)

        # Everything else a platform reads from its own topic - a thermostat's
        # current temperature, a light's brightness - is declared in the class.
        for key, attribute in spec.state_map.items():
            payload[key] = cfg.attribute_topic(entity_id, attribute)

        for capability, command in spec.commands.items():
            payload[command.topic_key] = cfg.command_topic(entity_id, capability)
        return payload

    def _retract(self, entity_id):
        component = self._published.pop(entity_id, None)
        if component:
            self._broker.retract(self._cfg.discovery_topic(component, entity_id))
            self._broker.retract(self._cfg.state_topic(entity_id))
            _LOG.info("retracted %s", entity_id)

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
        spec = classes.describe(entity_id, state, True)
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

        # TODO(verify against the live system): confirm homeassistant.turn_on /
        # turn_off is the right generic for the onoff kind across the domains we
        # label, and that call_service accepts an empty service_data.
        domain = command.service.split(".", 1)[0]
        if command.kind == "onoff":
            service = "turn_on" if value else "turn_off"
            await self._ha.call_service(domain, service,
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
        return None

# Mirror Bridge

Publishes label-selected Home Assistant entities to MQTT with discovery, so the
remote mirror needs no YAML and the Pi needs no restarts.

**Status: in production.** It is the mirror - the statestream block, the
dispatcher automations and the mirror's MQTT YAML have all been removed. As of
version 0.3.1 it publishes 13 entities by label: three thermostats with their
battery and temperature sensors, two override switches, a day/night flag and a
fan-speed sensor.

## Why it exists

The first version of the mirror expressed membership as *configuration*: an
entity appeared in `mqtt_statestream`'s include list, in two dispatcher
automations, in a seed automation and in the mirror's `mqtt:` block. Four files
that had to agree, which is why `tools/gen-mirror.py` exists. Worse,
`mqtt_statestream` reads its include list only at startup, so every added entity
cost a core restart.

Here membership is *data*: apply the `mirror` label to an entity and it is
mirrored. Add `mirror-rw` and it is writable. Remove the label and it disappears
from the mirror, discovery and all. No file is edited, nothing is deployed and
nothing restarts.

Per-entity details are not configured either - unit, device class, icon,
temperature limits and available modes are read from the source entity's own
attributes at publish time.

## What has to change, and when

| Action | What it takes |
|---|---|
| Mirror an entity | apply the `mirror` label |
| Make it writable | also apply `mirror-rw` |
| Stop mirroring it | remove the label |
| Support a new *class* (covers, locks, fans) | extend `classes.py`, bump the version, update the add-on |

`classes.py` is the only file that needs touching for a new class. Everything
else is generic.

## Design notes worth keeping

- **Membership is the authorisation.** Every command is checked against the
  current membership set before a service is called, so the mirror can only
  actuate what carries `mirror-rw`. A command naming any other entity is
  rejected, whatever the mirror sends.
- **Validation reads the entity, not a config file.** A setpoint is clamped to
  the device's own reported `min_temp`/`max_temp`, and a mode must appear in its
  own `hvac_modes`. Nothing to keep in sync when a device reports different
  limits.
- **Attributes are published as plain text.** `mqtt_statestream` publishes
  string attributes JSON-quoted (`"idle"`) while numbers arrive bare, which
  forces decode templates on the consuming side. Publishing plain values removes
  that mismatch entirely.
- **Removal is clean.** An empty retained payload on the discovery topic makes
  Home Assistant drop the entity along with its registry entry - the thing YAML
  cannot do, and the reason the instance this was built for once had 12 968
  orphaned entities to clear out.
- **No secrets are configured.** `services: ["mqtt:need"]` has the Supervisor
  inject broker credentials, and `homeassistant_api: true` provides
  `SUPERVISOR_TOKEN` for the websocket API.

## Verified on the live system (2026-09-17)

- The Supervisor token has the rights for `config/entity_registry/list` and
  `config/label_registry/list`.
- `entity_registry_updated` fires for a label applied through the UI, so
  membership really is live: labelling an entity published its discovery and
  state within about eight seconds, with no restart and no file edited.
- Removing the label retracted both, leaving nothing behind.
- Presentation is read from the source entity, not configured: a battery sensor
  arrived with `device_class: battery` and `%`, a temperature sensor with
  `device_class: temperature` and `°C`, and the thermostat with its own
  `hvac_modes`, `min_temp`, `max_temp` and `target_temp_step`.
- Publishing attributes as plain text works as intended - `hvac_action idle`
  rather than `"idle"` - so no decode template is needed anywhere.
- Cost of watching every `state_changed` event on a busy instance: **0.26% CPU
  and 24.5 MB**. The concern that this would be expensive was unfounded.

## The mqtt:need collision (resolved 2026-09-17)

`services: ["mqtt:need"]` did not work at first. The Supervisor injects username
`addons` with a generated 64-character password, while the Mosquitto add-on's
`logins` option defined a *manual* `addons`/`addons` account. The manual password
wins in the broker's password file, so the Supervisor's own credential was
refused and the add-on sat in a reconnect loop.

Fixed by giving each consumer its own account and freeing `addons` for the
Supervisor:

| Account | Used by |
|---|---|
| `zigbee2mqtt` | Zigbee2MQTT, set in its **add-on options** |
| `il2bridge` | IL2 Bridge add-on options |
| `amsreader` | the AMS reader hardware - left untouched |
| `addons` | the Supervisor service account, now unshadowed |
| `homeassistant` | Home Assistant core, via the add-on's own auth |

`mqtt_user`/`mqtt_password` remain as options for the case where the injected
credentials cannot be used; left empty, `mqtt:need` is used, which is now the
configuration in force.

## Also verified (2026-09-17)

- **The command path, including its refusals.** On/off and climate setpoint and
  mode all work; a setpoint of `2` clamped to the device's own minimum of 4;
  `DROP TABLE` and an invalid mode `banana` were rejected; and commands aimed at
  a labelled-but-read-only entity and at an unlabelled entity were both refused
  with `not writable`. Membership really is the authorisation.
- **Reconnection, both sides.** A Home Assistant restart produced
  `502 -> retry 4s, 8s, 16s -> reconnected` with membership restored, and a
  broker restart produced `reconnected - re-announcing every mirrored entity`.

## Naming

Entity names reproduce the source's own structure rather than its already
composed friendly name. Home Assistant builds a display name from a device plus
the entity's short name, and the Pi already works that way: a Zigbee2MQTT
thermostat sensor carries `original_name: "Temperature"` under a device named
"Radiator gang 2. etasje", while a thermostat itself carries
`original_name: None`, meaning it takes its device's name outright.

So each mirrored entity is published under a device derived from its **source
device**, with `has_entity_name: true` and the short name, and Home Assistant
composes exactly what the Pi shows. Entities with no device on the source -
helpers, template sensors - are published without one and keep their own name.

The earlier approach sent the composed friendly name *and* stapled a single
synthetic "Pi mirror" device on top, so Home Assistant composed a second time
and produced `sensor.pi_mirror_radiator_gang_2_etasje_temperature`, "Pi mirror
Radiator gang 2. etasje Temperature". The length was double composition, not
verbosity.

`object_id` ties the mirror's entity_id to the **source entity_id**, not to the
display name, so ids stay predictable when names change.

**Renaming an existing entity does not move its entity_id.** Home Assistant
keeps deleted registry records and restores the previous entity_id when the same
`unique_id` reappears, so deleting and recreating an entity is not enough - it
comes back with its old id. Renaming through
`config/entity_registry/update` with `new_entity_id` is the way to move one.

## Option, not built: an overrides file

Names currently follow the Pi exactly. If a name should differ on the mirror -
shorter, or simply different - the intended mechanism is a declarative overrides
file rather than renaming in the mirror's own UI:

```yaml
# /addon_configs/<slug>/overrides.yaml, kept in version control
devices:
  "Radiator gang 2. etasje": "Gang"     # shortens every entity on that device
entities:
  sensor.viftehastighet:
    name: "Vifte"
    icon: mdi:fan
```

Why a file rather than the UI. A rename made in the mirror's interface is stored
as a registry override that permanently wins over discovery - which means the
two mechanisms cannot be mixed, because the file silently stops having any
effect for that entity. It is also invisible to this repository, unreviewable in
a diff, and lives only on the instance that is meant to be the disposable half
of the pair. A file deployed from version control is version-controlled,
diffable and reproducible if the mirror is ever rebuilt.

Why device-level overrides matter most: one line shortens every entity on that
device, which is where the length actually comes from.

Implementation sketch: the add-on polls the file's mtime, and on change
re-announces affected entities - no restart, no Home Assistant reload. Changing
a *name* updates in place; changing an entity_id additionally requires the
registry rename described above.

## Known cosmetic wart

With a `device` block in the discovery payload, Home Assistant composes both the
friendly name and the entity_id from the device name, giving
`sensor.pi_mirror_viftehastighet`. Neither `object_id` nor
`has_entity_name: false` overrides it. The options are to accept it, to rename
on the mirror (registry overrides survive discovery updates), or to drop the
device block and lose the grouping.

## Deploying it locally

Local add-ons live in `/addons/<slug>/` on the Pi:

    scp -O -P 2222 -r addons/mirror-bridge hassio@192.168.1.201:/tmp/
    ssh -p 2222 hassio@192.168.1.201 'sudo mv /tmp/mirror-bridge /addons/mirror_bridge'

Then Settings -> Add-ons -> Add-on Store -> three dots -> Check for updates, and
it appears under "Local add-ons".

## Bridge and mirror configuration

The discovery subtree has to reach the mirror, and the mirror has to be told to
look for it:

- add `topic mirror/discovery/# out 1` to the `mirror_state` bridge connection
  in `mirror/pi-bridge.conf`
- set the mirror's MQTT `discovery_prefix` to `mirror/discovery` in its
  integration options

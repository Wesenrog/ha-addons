# Changelog

## 0.3.1

- Dockerfile defaults `BUILD_FROM`, since Supervisor 2026.04.0+ no longer passes
  it automatically and the add-on should build with or without `build.yaml`.
  First version published to this repository.

## 0.3.0

- Names reproduce the source's own structure instead of doubling up. Home
  Assistant composes a display name from a device plus the entity's short name,
  and the source already works that way; sending the already-composed
  `friendly_name` *and* attaching a synthetic device made it compose twice,
  giving `sensor.pi_mirror_radiator_gang_2_etasje_temperature`. Each entity is
  now published under a device derived from its source device, with
  `has_entity_name` and the short `original_name`. Entities with no device on
  the source stay loose and keep their own name.
- Note: an existing entity does not move its entity_id when renamed. Home
  Assistant restores the previous id when the same `unique_id` reappears, so
  moving one requires `config/entity_registry/update` with `new_entity_id`.

## 0.2.1 - 0.2.3

- `object_id` in the discovery payload, tying the mirror's entity_id to the
  source entity rather than to the display name.
- Tried and reverted `has_entity_name: false`: with a device block present,
  Home Assistant composes the name regardless and the flag has no effect.

## 0.2.0

- Reconnection on both sides. The websocket is re-established with backoff when
  Home Assistant restarts, re-reading membership and re-subscribing; previously
  the run loop simply ended.
- Re-announce every mirrored entity after an MQTT reconnect. A client that only
  publishes its state at startup is silently wrong for as long as the broker
  remembers a stale retained value - the failure mode observed in another add-on
  on the same broker.

## 0.1.0

Initial version. Add-on packaging, the domain-to-mirror class table, and the
runtime: label-driven membership, discovery publishing, retraction on removal,
and a command path validated against the entity's own attributes.

Fixed during the first run: `on_connect` logged success without checking paho's
reason code, which hid an authentication-rejection reconnect loop behind five
cheerful "connected" lines. Added explicit `mqtt_user`/`mqtt_password` options
because `services: ["mqtt:need"]` is unusable while the broker defines a manual
login colliding with the Supervisor's service account name.

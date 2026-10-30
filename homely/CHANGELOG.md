# Changelog

## 0.4.2

- Moved to the `ha-addons` repository; no change in behaviour, entity ids or `unique_id`s.
- Tests use a made-up home with the same device models and payloads as a real one.

## 0.4.1

Mapping checked against the devices of a real installation:

- Entry Sensor and Alarm Entry Sensor 2 are now contacts (`opening`, suffix `kontakt`) instead
  of a generic alarm. A sensor's alarm state is classified by a table of known models instead
  of words in the model name; an unknown model gets the generic alarm and one log line.
- Yale Doorman locks: Lock (`las`), Door (`dor`, `on` when open), Battery low
  (`las_lavt_batteri`), Fault (`las_feil`) and Error code (`las_feilkode`, disabled by default),
  from Homely's `report` data. The guessed `lock.*` mapping, which no device reports, is gone.
  The home's Battery problem now includes lock batteries.
- The Intelligent Heat Alarm's fire state uses the `heat` class; the suffix stays `brann`.

## 0.4.0

- Readable entity ids: `default_entity_id` now ends in a short Norwegian suffix that says
  what the entity is, for example `binary_sensor.homely_garasjedor_bak_kontakt` instead of
  `binary_sensor.homely_garasjedor_bak_alarm_alarm`, and `binary_sensor.homely_aktivert`
  instead of `binary_sensor.homely_armed`. The suffixes are in a frozen table, separate from
  the entity names, and don't follow `language`.
- `unique_id`s are unchanged.
- Install this version (or later) as your first version: Home Assistant fixes an entity id
  the first time it discovers the entity, so entities discovered by 0.2.0–0.3.1 keep their
  old ids.

## 0.3.1

- Every websocket reconnect now fetches all devices, however short the drop and however
  recent the last fetch. Before, a change during a short drop could stay wrong until the next
  `poll_interval`. Rate limits still apply: at most one fetch per minute, and never before a
  backoff or `Retry-After`.
- The switch to stale data no longer waits for a slow fetch or reconnect: it runs on its own
  timer, and a dead connection is closed in the background.
- A late callback from a replaced websocket connection can no longer mark the new connection
  as down.

## 0.3.0

- Stale data now shows as unavailable. Entities follow up to three availability topics
  (`availability_mode: all`): the add-on process, the data (live only while the websocket is
  connected and the data is fresh) and, for device entities, whether Homely reports the
  device online.
- New option `stale_after` (seconds, default 120): how long the websocket may be down before
  entities go unavailable. Shorter drops change nothing.
- After a longer drop, entities come back only once the websocket is up again and a fresh
  poll has been applied. That poll runs right away, but never earlier than a rate-limit
  backoff or `Retry-After` allows.
- A device Homely reports offline shows its entities as unavailable, except its Online
  entity, which shows `off`.
- Data transitions are logged once each at info level (`Data live` / `Data stale: <reason>`).

## 0.2.0

- New option `language` (`nb` or `en`, default `nb`): entity names and the hub's model text
  in Norwegian bokmål or English. Option texts are translated to Norwegian too.
- Every entity now gets a fixed, language-independent entity id through `default_entity_id`:
  `<domain>.homely_<device>_<key>` for device entities and `<domain>.homely_<key>` for the
  home. Home Assistant uses it the first time it discovers an entity.
- `unique_id`s are unchanged from 0.1.0.
- Upgrading from 0.1.x: entities already in Home Assistant keep their entity ids, and their
  names change to Norwegian unless you renamed them yourself or set `language: en`.

## 0.1.1

- Pinned build: exact Python base image per architecture in `build.yaml`
  (`3.13-alpine3.24-2026.10.3`), exact versions of every Python dependency,
  installed as pure-Python wheels only.
- Supported architectures are now `aarch64` and `amd64`; `armv7` is dropped.
- No change in entities, topics or payloads.

## 0.1.0

- First version: Homely alarm state and sensors to Home Assistant via MQTT discovery.

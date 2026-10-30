# Homely add-on

This add-on reads your Homely alarm system through Homely's SDK API and publishes it to
Home Assistant with MQTT discovery. Devices and entities appear automatically under the MQTT
integration.

## Requirements

- A Homely account where you are owner or admin of the home.
- An MQTT broker. The Mosquitto broker add-on is found automatically.
- The MQTT integration set up in Home Assistant.
- A 64-bit system: `aarch64` (for example a Raspberry Pi 4 or 5 on Home Assistant OS) or
  `amd64`. Other architectures are not supported.

The add-on is built on your Home Assistant host when you install or update it. The build is
pinned: the same base image and the same Python packages every time, and nothing is compiled.

## Configuration

| Option | Description |
| --- | --- |
| `username` | The email address you use in the Homely app. |
| `password` | Your Homely app password. |
| `home` | Name of the home to use if your account has more than one. Empty means the first. |
| `stale_after` | Seconds the live connection to Homely may be down before entities show as unavailable (30–3600, default 120). Shorter drops change nothing. |
| `poll_interval` | Seconds between full device refreshes (300–86400, default 3600). Live changes arrive over the websocket, so this can be long. Homely rate limits frequent requests. |
| `discovery_prefix` | Home Assistant's MQTT discovery prefix (default `homeassistant`). |
| `base_topic` | Prefix for state topics (default `homely`). |
| `log_level` | `debug`, `info`, `warning` or `error`. |
| `language` | Language of the entity names: `nb` (Norwegian bokmål, default) or `en` (English). It does not change entity ids. |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | Only needed if you don't use the Mosquitto broker add-on. |

## What you get

Names below are in English; with `language: nb` they are in Norwegian.

A **Homely** device for the home with:

- **Alarm**: `disarmed`, `armed_home`, `armed_night`, `armed_away`, `arming` (exit delay),
  `pending` (entry delay), `triggered` or `unknown`. The attributes show the raw Homely state
  and, after a live change, who changed it and when.
- **Armed** and **Triggered** binary sensors, handy for automations.
- **Battery problem** and **Tamper**, on if any device reports it.
- Diagnostics: **Offline devices**, **Live updates** (websocket connected), **Last poll** and
  **Last poll status**.

One device per Homely sensor, with entities for the states it reports:

| Homely data | Entity |
| --- | --- |
| Motion sensor alarm | Motion |
| Entry / window sensor alarm | Contact (opening) |
| Smoke detector / heat detector fire | Fire (smoke / heat) |
| Flood alarm | Flood (moisture) |
| Temperature, humidity | Temperature, Humidity |
| Battery low / defect / voltage | Battery low, Battery defect, Battery voltage |
| Link strength / parent address | Link quality, Parent address (disabled by default) |
| HAN plug | Energy imported/exported (kWh), Power (W), Metering check |
| ELKO thermostat | Temperature, Heating setpoint (read-only) |
| Yale Doorman lock | Lock, Door, Battery low, Fault, Error code (disabled by default) |
| Device online | Online |

States the add-on doesn't recognise are skipped. Run with `log_level: debug` to see them.

What a sensor's alarm state means depends on the model, because the data looks the same for a
motion sensor and a door contact. These models are known:

| Model | Alarm state is |
| --- | --- |
| Motion Sensor Mini, Alarm Motion Sensor 2 | Motion |
| Entry Sensor, Alarm Entry Sensor 2, Window Sensor | Contact (`opening`: the sensor can't tell a door from a window) |

Any other model's alarm state is shown as a generic alarm (`safety`), and the log says once
which model it was. Please report it so it can be added. The Intelligent Heat Alarm's fire
state uses the `heat` class; other fire states use `smoke`.

On a Yale Doorman lock, **Lock** is `on` when unlocked and **Door** is `on` when the door is
open, as Home Assistant's lock and door classes define. The add-on reads the lock state from
Homely's `report.locked` only, so each lock has one lock entity.

## Entity ids

Entity ids are fixed and don't depend on `language`, so automations and dashboards keep
working if you switch language. They end in a short Norwegian suffix that says what the
entity is:

- Home entities: `<domain>.homely_<suffix>`, for example `sensor.homely_alarm` and
  `binary_sensor.homely_aktivert`.
- Device entities: `<domain>.homely_<device>_<suffix>`, where `<device>` is the Homely device
  name as Home Assistant slugifies it (`ø`→`o`, `å`→`a`, `æ`→`ae`, spaces and punctuation→`_`).
  For a contact sensor named "Garasjedør bak": `binary_sensor.homely_garasjedor_bak_kontakt`.

| Entity | Suffix |
| --- | --- |
| Home: Alarm, Armed, Triggered | `alarm`, `aktivert`, `utlost` |
| Home: Battery problem, Tamper | `batteriproblem`, `sabotasje` |
| Home: Offline devices, Live updates | `enheter_frakoblet`, `direkteoppdatering` |
| Home: Last poll, Last poll status | `siste_henting`, `status_siste_henting` |
| Motion / contact / other alarm | `bevegelse` / `kontakt` / `alarm` |
| Tamper, fire, flood | `sabotasje`, `brann`, `vannlekkasje` |
| Temperature, humidity | `temperatur`, `luftfuktighet` |
| Thermostat temperature, setpoint | `termostat_temperatur`, `settpunkt` |
| Battery low, defect, voltage | `lavt_batteri`, `batterifeil`, `batterispenning` |
| Link quality, parent address | `signalstyrke`, `overordnet_node` |
| HAN meter: energy imported, exported, power, check | `energi_importert`, `energi_eksportert`, `effekt`, `malerkontroll` |
| Lock: lock, door, low battery, fault, error code | `las`, `dor`, `las_lavt_batteri`, `las_feil`, `las_feilkode` |
| Device online | `tilkoblet` |

The suffixes are frozen: later versions never change them, only add new ones. A state the
table doesn't know yet (from a future version) uses its raw Homely key as the suffix.

Home Assistant sets the entity id the first time it discovers an entity and keeps it after
that, even if the device is renamed in Homely. If two devices have the same name, the second
gets a `_2` suffix. You can still rename any entity or entity id in Home Assistant.

## Availability

An entity is available only while its data can be trusted. Each entity follows a list of
availability topics and is available only when all of them say `online`:

| Topic | `online` when |
| --- | --- |
| `homely/bridge/availability` (process) | The add-on is running. Set `offline` on a clean stop and by the MQTT will if it dies. |
| `homely/<location>/availability` (data) | The websocket is connected and the data is fresh (see below). |
| `homely/<location>/<device>/availability` (device) | Homely doesn't report the device offline (`online: false`). |

| Entities | Follow |
| --- | --- |
| Device entities, except Online | process + data + device |
| A device's Online entity | process + data, so it can show that the device is offline |
| Home: Alarm, Armed, Triggered, Battery problem, Tamper | process + data |
| Home diagnostics: Offline devices, Live updates, Last poll, Last poll status | process only, so they can explain why the rest is unavailable |

The data topic:

- is `offline` at start until both a full device fetch has been applied and the websocket
  is connected;
- stays `online` through websocket drops shorter than `stale_after`;
- goes `offline` when the websocket has been down longer than `stale_after`, because events
  during the gap are lost. It comes back only when the websocket is connected again **and** a
  full fetch made over that connection has been applied.
  A successful fetch alone does not make data live while the websocket is down.

Every reconnect, after a short drop or a long one, starts a full fetch at once, because a
change during the drop is never sent again. A door that opens during a 30-second drop is
therefore corrected right after the reconnect, not at the next `poll_interval`. The fetch still
respects the rate limits: never sooner than a minute after the previous fetch attempt, nor
before a backoff or `Retry-After` from Homely has passed. A flapping connection therefore
fetches at most once a minute.

A connection that silently dies (no packets, no close) is detected by Socket.IO's ping and
dropped within Homely's ping interval plus ping timeout.

The add-on logs each change of the data topic once, as `Data live` or `Data stale: <reason>`.

## How it works

- At start, and then every `poll_interval`, the add-on fetches all devices from Homely.
- A websocket delivers alarm and sensor changes as they happen. After a drop it reconnects
  with increasing delays and then fetches all devices again (see Availability).
- If Homely rate limits a request (HTTP 429 or 439), the add-on keeps the last known state and
  retries with increasing delays, honouring `Retry-After`.
- Access tokens are refreshed before they expire, with a fresh login if the refresh is rejected.
- A device removed from Homely is removed from Home Assistant after it has been missing from
  two full refreshes in a row.

## Limitations

- The Homely API is read-only. The alarm can't be armed or disarmed from Home Assistant.
- The add-on only sees what Homely's API exposes, which may not be every device in the app.
- Changes that happen while the add-on is stopped are picked up by the full fetch at start.

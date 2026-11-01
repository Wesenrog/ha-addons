# Zigbee Watch

Keeps a history of the Zigbee mesh from Zigbee2MQTT and reports when it
changes or degrades. It observes only: the nightly network-map request is the
one thing it sends to Zigbee2MQTT.

## Requirements

- Zigbee2MQTT 2.x with an MQTT broker (the Mosquitto add-on); the add-on uses
  the Supervisor's MQTT service credentials.
- Zigbee2MQTT's **log level at `info`**. Route errors are logged at `info` by
  the `ember` adapter, and `bridge/logging` only carries lines that pass the
  log level. If the rest of the log should stay at `warning`, raise just that
  namespace in Zigbee2MQTT's `configuration.yaml`:

  ```yaml
  advanced:
    log_level: warning
    log_namespaced_levels:
      zh:ember:ezsp: info
  ```

- Availability enabled in Zigbee2MQTT, so the "not heard" rule can use it.

## Options

| Option | Default | |
|---|---|---|
| `z2m_base_topic` | `zigbee2mqtt` | Zigbee2MQTT's base topic |
| `scan_time` | `02:15` | local time of the nightly network map |
| `scan_timeout_min` | `15` | how long to wait for the map |
| `retention_days` | `400` | history kept |
| `lqi_drop` | `25` | fall in 7-day median link quality that is a finding |
| `error_min_count` | `10` | a day's errors must exceed this ... |
| `error_factor` | `4` | ... and this many times the device's normal day |
| `error_baseline_days` | `14` | days the normal day is the median of |
| `timeout_mains_min` | `60` | as Zigbee2MQTT's active availability timeout |
| `timeout_battery_min` | `1500` | as Zigbee2MQTT's passive availability timeout |
| `log_level` | `info` | |

## Entities

All under the device "Zigbee Watch", unavailable when the add-on is not
running.

| Entity | |
|---|---|
| `sensor.zigbee_watch_funn` | number of open findings; attribute `funn` with the latest ten |
| `sensor.zigbee_watch_rutefeil_24t` | route errors in the last 24 h; attributes `enheter` (top five devices) and `dekning` |
| `sensor.zigbee_watch_siste_kart` | when the last network map succeeded |
| `sensor.zigbee_watch_kartstatus` | `ok`, `timeout`, `error: ...`, `skipped: ...` or `running` |
| `binary_sensor.zigbee_watch_tilkoblet` | Zigbee2MQTT's own bridge state (diagnostic) |

**Error counts are lower bounds.** Zigbee2MQTT does not publish a log line on
MQTT that is identical to the one before it, so a burst of identical route
errors for one device arrives as one. The counts will not match Zigbee2MQTT's
log files; the baseline rule compares each device with its own history, so a
jump still shows. `dekning.lyttet_andel` is the share of the last 24 hours the
add-on was listening; below 0.9 the sensor is unavailable rather than showing a
count that is too low for a different reason.

## Findings

- **Parent changed** - after each network map, a device whose parent differs
  from the previous map. An end device's parent is the router that lists it as
  a child; a router's is its parent entry if any, else the next hop of its route
  to the coordinator. A device listed by two routers is not judged. Closes
  after 7 days.
- **Link quality falling** - daily and after each map: the 7-day median against
  the previous 7 days, per device (from its messages) and per link in the maps.
  Closes when it recovers.
- **Errors above normal** - daily, for the day before: per device and kind, more
  than `error_min_count` and more than `error_factor` times the median of the
  previous `error_baseline_days` days. Needs seven days of history first; errors
  caused by the map itself do not count.
- **Not heard** - every 5 minutes: a device Zigbee2MQTT does not report online
  and that has not been heard for longer than its availability timeout, counting
  only time during which both the add-on and Zigbee2MQTT were running, so
  restarts and power cuts do not count as silence.

## The database

`/share/zigbee_watch/zigbee_watch.sqlite`, SQLite in WAL mode, timestamps in
UTC epoch seconds. Each night, after the network map, a complete copy is
written to `/share/zigbee_watch/zigbee_watch-copy.sqlite`: **copy that file**.
The live file on its own may miss recent writes still in its `-wal` file.

About 35 MB after 400 days for a mesh of 31 devices.

## The report

On the Pi (in the add-on container) or anywhere with Python 3.11+ and this
repository, against the database or a copy:

    cd zigbee_watch
    python -m zigbee_watch.report --db zigbee_watch-copy.sqlite --days 14
    python -m zigbee_watch.report --db zigbee_watch-copy.sqlite --device "Termometer bad"
    python -m zigbee_watch.report --db zigbee_watch-copy.sqlite --until 2026-10-18 --days 7

Per device: parent history, link quality per day, link quality of its links in
the maps, availability drops, and errors per day per kind, in local time
(`--tz`, default `Europe/Oslo`). `--device` also takes an earlier name. On
Windows, `pip install tzdata` first.

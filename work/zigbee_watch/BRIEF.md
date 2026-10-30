# Zigbee Watch

A new add-on, `zigbee_watch`, that keeps a history of the house's Zigbee mesh -
who routes through whom, link quality, availability, route errors - and says
when it changes or degrades. Read [CLAUDE.md](../../CLAUDE.md) first for how to
work here.

## Why

Zigbee2MQTT shows the mesh only as it is right now. The questions the owner has
are about change over time, and today they are answered by digging in
Zigbee2MQTT's log files, which keep about a week:

- Three temperature sensors started 57-67 route errors a day between them on
  2026-10-17, against about 5 before, at the time two other sensors stopped.
  Nobody can say what changed in the mesh that day.
- One device is not yet located; its parent and neighbours would narrow down
  where it is.
- Heating depends on battery devices on this mesh (radiator valves, room
  sensors, window contacts). A link that degrades over weeks should be seen
  before a device drops out.

## The installation

You cannot reach it; the owner installs and checks each version.

- Raspberry Pi 4, Home Assistant OS 18.3 (`aarch64`), Core 2026.10.2,
  Supervisor 2026.10.5. Installed from this repository, built on the Pi.
- **Zigbee2MQTT 2.14.2** as an add-on, base topic `zigbee2mqtt`, adapter
  `ember` (a Home Assistant SkyConnect), channel 11. Home Assistant
  integration on. Availability on: active timeout 60 min, passive 1500 min.
- **The mesh:** the coordinator, 5 mains-powered routers and 25 battery end
  devices (radiator valves, temperature and humidity sensors, contact sensors,
  remotes). End devices sleep and do not answer neighbour-table queries; only
  the coordinator and routers do.
- **Broker:** the Mosquitto add-on (7.1.3) on the same host, discovery prefix
  `homeassistant`. A bridge forwards only `mirror/#` off-site; publish nothing
  there. Use your own base topic, `zigbee_watch/`.
- **Maintenance windows:** Zigbee2MQTT is restarted at 03:30 on the last Sunday
  of March and October (clock change), and occasionally by hand. A mesh that
  goes quiet while the coordinator is healthy is fixed by a cold power-cycle of
  the host, so expect gaps of minutes with nothing at all.
- **Storage:** `/share` is where the owner's add-ons keep their databases
  (`/share/<addon>/`), and from where they are copied for analysis.

## What to build

1. **Inventory**, from the retained `zigbee2mqtt/bridge/devices`: IEEE address,
   friendly name, type (Coordinator/Router/EndDevice), power source, model.
   Record when a device appears, disappears or is renamed.
2. **Link quality, continuously and cheaply:** every device message on
   `zigbee2mqtt/<friendly_name>` carries `linkquality`; store it (thinned, for
   example the last value per device per 10 minutes is enough).
3. **Availability:** `zigbee2mqtt/<friendly_name>/availability`; store the
   transitions.
4. **Errors from Zigbee2MQTT's log:** subscribe to `zigbee2mqtt/bridge/logging`
   and count per device and per kind (route error, delivery failure, timeout,
   ...). The exact message texts of the `ember` adapter must be checked against
   the Zigbee2MQTT 2.14 / zigbee-herdsman source rather than guessed. Keep the
   patterns in one table, and store a sample of each unmatched warning/error so
   new kinds can be added.
5. **The network map, once a night:** request
   `zigbee2mqtt/bridge/request/networkmap` with `{"type": "raw", "routes": true}`
   and store nodes and links (source, target, LQI, depth, relationship, routes)
   from the response. Check the payload shape against the 2.14 source. A scan
   loads the mesh, so: one at a time, a time-out, a configurable time (default
   02:15), never within 30 minutes of a Zigbee2MQTT restart, and a status
   (`ok`, `timeout`, `skipped: <why>`).
6. **Change detection**, after each scan and daily over the stored data:
   - a router's or end device's parent changes;
   - a link's median LQI over 7 days falls by a configurable amount against the
     previous 7 days;
   - a device's daily error count is well above its own baseline (the
     2026-10-17 case must be caught);
   - a device seen in the inventory has not been heard for longer than its
     availability timeout.
   Each finding is a row with time, device, kind and a one-line text.
7. **Home Assistant entities** (few; Norwegian names, fixed ids):
   - `sensor.zigbee_watch_funn`: number of open findings, with the latest
     findings' texts as an attribute;
   - `sensor.zigbee_watch_rutefeil_24t`: route errors in the last 24 h, with the
     top devices as an attribute;
   - `sensor.zigbee_watch_siste_kart` (timestamp) and
     `sensor.zigbee_watch_kartstatus`;
   - `binary_sensor.zigbee_watch_tilkoblet` as diagnostic.
   Per-device entities are not wanted.
8. **A report** the owner can read without SQL: `python -m zigbee_watch.report
   [--days N] [--device NAME]` against the database (works on a copy off the
   Pi), giving per device: parent history, LQI trend, availability drops, error
   counts per day.

## Rules

- **Observer only.** The nightly network-map request is the only thing it sends
  to Zigbee2MQTT. No permit-join, no configure, no bind, no OTA, no device
  writes, no restarts.
- If Zigbee2MQTT or the broker is down, it waits and carries on; it never
  crashes on a missing or odd message.
- SQLite at `/share/zigbee_watch/zigbee_watch.sqlite` (map `share:rw`), WAL
  mode, safe to copy while running. Retention configurable, default 400 days;
  it should stay well under 100 MB at this mesh size.
- Timestamps stored as UTC epoch seconds; the report shows local time
  (`Europe/Oslo`).

## Done when

- Tests with a fake Zigbee2MQTT (scripted messages and a networkmap responder)
  and the in-process broker show: inventory changes recorded; LQI thinned as
  specified; availability transitions stored; log lines classified, unknown
  ones sampled; a scan stored, a scan time-out reported, a scan skipped near a
  restart; each change-detection rule firing on synthetic data and staying
  quiet on steady data, including a replay of a 2026-10-17-like jump.
- Reconnect: broker restarted mid-run - it resubscribes and republishes.
- The report runs on a copy of a test database.
- `NOTES.md` lists what was checked against the Zigbee2MQTT source and what
  remains to be seen on the real mesh.

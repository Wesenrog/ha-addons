# Changelog

## 0.1.0

First version, as planned in `work/zigbee_watch/NOTES.md` and approved in
change request 01.

- **Inventory** from the retained `bridge/devices`, keyed by IEEE address:
  appear, disappear, rename, network-address and type changes are recorded.
- **Link quality** from every device message, thinned to the last value per
  device per 10 minutes. Zigbee2MQTT's cached-state republish (at its startup
  and 30 s after Home Assistant comes online) and retained device messages are
  not taken as fresh, so they neither store link quality nor count as heard.
- **Availability** transitions from `<device>/availability`.
- **Log lines** from `bridge/logging`, classified by one pattern table checked
  against the Zigbee2MQTT 2.14.2 / zigbee-herdsman 10.10.0 source: route
  errors (by network address), network status, broadcast delivery failures,
  failed commands split into delivery failures and timeouts, failed pings and
  reads, and failures of our own scan. Unknown warnings and errors are kept as
  one sample per shape with a count. Counts are lower bounds: Zigbee2MQTT skips
  a log payload identical to the previous one.
- **Nightly network map** (`raw`, with routes) at 02:15 local time, once per
  night, with a 15-minute time-out, never within 30 minutes of a Zigbee2MQTT
  restart (retried until 3 hours after the scheduled time), status `ok`,
  `timeout`, `error: <msg>` or `skipped: <why>`. Nodes, links and each device's
  parent are stored per scan.
- **Change detection** into a findings list: parent changed, link quality
  falling (per device and per map link), errors above the device's own
  baseline, and not heard for longer than the availability timeout, counting
  only time during which both the add-on and Zigbee2MQTT were up.
- **Entities**: `sensor.zigbee_watch_funn`, `sensor.zigbee_watch_rutefeil_24t`,
  `sensor.zigbee_watch_siste_kart`, `sensor.zigbee_watch_kartstatus`,
  `binary_sensor.zigbee_watch_tilkoblet`.
- **Report**: `python -m zigbee_watch.report [--days N] [--device NAME]
  [--until DATE]`, on the live database or a copy.
- SQLite in WAL mode at `/share/zigbee_watch/zigbee_watch.sqlite`, 400 days'
  retention, and a nightly complete copy `zigbee_watch-copy.sqlite` that is safe
  to copy at any time.

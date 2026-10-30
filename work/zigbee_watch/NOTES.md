# Zigbee Watch - notes

## Status

**Plan only; nothing built yet.** This is the plan for the first version
(0.1.0), for review before implementation starts. Open change requests: none.

Branch: `claude/laughing-newton-1l3am4`, the branch this session was assigned,
rather than `zigbee_watch/first-version` as CLAUDE.md asks.

## Checked against the Zigbee2MQTT source

Read in the npm packages `zigbee2mqtt@2.14.2` and the `zigbee-herdsman@10.10.0`
it depends on (paths are inside each package's `dist/`):

1. **Route errors are logged at `info`, not as warnings.**
   `adapter/ember/ezsp/ezsp.js:4303`, namespace `zh:ember:ezsp`:
   ``Received network/route error <EmberStackError> for "<target>".``
   `bridge/logging` carries only what passes Zigbee2MQTT's log level
   (`util/logger.js:187-191`), so with `log_level: warning` route errors never
   reach MQTT.
2. **Errors name a network address, not a device.** `<target>` above is a 16-bit
   network address, and so is the destination in
   ``Delivery of <type> failed for '<nwk>'.`` (`error`,
   `adapter/ember/adapter/emberAdapter.js:326`). Addresses are mapped to
   devices through `network_address` in `bridge/devices`, keeping the history,
   because the address changes when a device rejoins.
3. **Zigbee2MQTT drops repeated log lines on MQTT.** A payload identical to the
   one just before it is not published again (`extension/bridge.js:101-106`),
   so a burst of route errors for one device can arrive as a single message.
   Counts are lower bounds and will not match the log files. The baseline rule
   compares each device against its own history, so a jump like 2026-10-17 is
   still caught.
4. **Network map response** (`extension/networkMap.js`): arrives on
   `bridge/response/networkmap` with the request's `transaction` echoed, as
   `data.value = {nodes, links}`.
   - Each link reads "source is a neighbour in target's table". `relationship`
     is 0 = parent, 1 = child, 2 = sibling; relationships above 3 are already
     dropped.
   - `routes` are the target's routing-table entries whose next hop is the
     source.
   - `nodes[].failed` lists routers that did not answer (`lqi`,
     `routingTable`).
   - End devices are never queried, so an end device's parent is the router
     that lists it as a child.
   - Zigbee2MQTT has no time-out of its own and no way to cancel a scan, so the
     add-on times out itself and ignores late responses by transaction id.

## Layout

```
zigbee_watch/                     add-on: config.yaml, build.yaml, Dockerfile, run.sh,
                                  requirements.txt, CHANGELOG.md, README.md, DOCS.md
zigbee_watch/zigbee_watch/
  config.py      options from env (as mirror_bridge)
  mqtt.py        paho wrapper: every connect -> subscribe, online, discovery, state
  store.py       SQLite schema, WAL, retention, nightly copy-safe snapshot
  inventory.py   bridge/devices diff -> appear/disappear/rename/network-address events
  ingest.py      linkquality (thinned), availability transitions, bridge/state (restarts)
  logpatterns.py the one pattern table, each row citing its herdsman source line
  logs.py        classify bridge/logging, count per device and kind, sample unmatched
  networkmap.py  scheduler + single scan with time-out, skip rules, storage
  detect.py      the four rules -> findings (open/closed)
  entities.py    the 5 discovery entities
  report.py      python -m zigbee_watch.report
zigbee_watch/tests/              pytest, amqtt broker in-process, fake Zigbee2MQTT
```

- **Runtime:** paho-mqtt 2.1.0 plus `tzdata`, both pure Python and pinned, on
  the base image pinned in CLAUDE.md. Maps `share:rw`; database at
  `/share/zigbee_watch/zigbee_watch.sqlite`.
- **Threading:** one worker thread owns SQLite. Paho callbacks only enqueue,
  and a scheduler thread wakes the worker.
- **Not copied from `mirror_bridge`:** it predates CLAUDE.md in three ways
  (Python 3.12 base, dependencies pinned in the Dockerfile, no tests). This
  add-on follows CLAUDE.md instead.

## Behaviour

- **Inventory:** devices keyed by IEEE address, never by friendly name; appear,
  disappear, rename and network-address events. Device topics are resolved by
  longest match against current names, because names can contain `/`; topics
  matching no device are dropped.
- **Link quality:** last value per device per 10-minute bucket, upserted on an
  integer device id. At 31 devices x 144 buckets/day x 400 days that is about
  1.8 M rows, roughly 30-40 MB. A test generates a 400-day database and asserts
  it stays under 100 MB.
- **Availability:** only transitions are stored. A retained replay after a
  reconnect is compared with the last stored state, so it does not create a
  false transition.
- **Errors:** each matched log line is stored as a raw event (time, device,
  kind); about 100 a day. Counts are aggregated on query. Unmatched
  warning/error lines keep one sample per normalised text, with a count and
  first/last seen.
- **Network map:**
  - Schedule: 02:15 Europe/Oslo by default, once per local date. On the March
    night the clock skips that hour, so it runs at the first time after 02:15.
  - Request: `{"type": "raw", "routes": true, "transaction": ...}`.
  - Status:

    | Status | When |
    |---|---|
    | `ok` | the response arrived; noted as partial if any router is in `failed` |
    | `timeout` | no response within 15 min (configurable) |
    | `error: <msg>` | Zigbee2MQTT answered with an error |
    | `skipped: <why>` | Zigbee2MQTT offline, online for less than 30 min, or a scan already running |

  - Zigbee2MQTT restarts are recorded from `bridge/state` transitions.
- **Detection:**
  - *Parent changed:* checked after each scan. An end device that two routers
    both list as a child is recorded as ambiguous, with no finding.
  - *LQI drop:* 7-day median against the previous 7 days, for each device's
    `linkquality` and for each map link. Threshold 25 by default, with a
    minimum number of samples per window.
  - *Errors above baseline:* a day's count above both 10 and 4 x the median of
    the previous 14 days, that median floored at 1. All three configurable.
  - *Not heard:* runs every 5 min. Limits 60 min for mains devices and 1500 min
    for battery devices, matching the Zigbee2MQTT settings. The clock counts
    from the later of the device's last message and Zigbee2MQTT or this add-on
    coming back online, so maintenance gaps raise no findings.
  - *Closing:* condition findings close when the condition clears; event
    findings (parent changed) close after 7 days.
- **Entities:** under one "Zigbee Watch" device, with `default_entity_id` and an
  availability topic carrying a last will.

  | Entity | Name | Content |
  |---|---|---|
  | `sensor.zigbee_watch_funn` | Funn | open findings; latest 10 texts as an attribute |
  | `sensor.zigbee_watch_rutefeil_24t` | Rutefeil siste 24 t | route errors; top 5 devices and a `dekning` (coverage) attribute |
  | `sensor.zigbee_watch_siste_kart` | Siste nettverkskart | timestamp of the last map |
  | `sensor.zigbee_watch_kartstatus` | Kartstatus | the scan status |
  | `binary_sensor.zigbee_watch_tilkoblet` | Zigbee2MQTT tilkoblet | Zigbee2MQTT's own `bridge/state`; diagnostic, `connectivity` |

  The 24 h count is published as unavailable if the add-on listened for less
  than 90 % of those 24 hours.
- **Report:** `python -m zigbee_watch.report --db FILE [--days N] [--device NAME]`,
  standard library plus `tzdata`. Per device: parent history, LQI per day
  (median/min), availability drops, errors per day per kind, in Oslo time.
  `--device` also matches earlier names.
- **Copy safety:** WAL mode. Each night the add-on writes `VACUUM INTO` a
  temporary file and renames it to `zigbee_watch-copy.sqlite`, a complete file
  that is safe to copy at any time. Copying the live file alone can miss data
  still in the `-wal` file.

## Tests (one or more per "Done when" item)

- amqtt broker in a thread with its own event loop; a fake Zigbee2MQTT
  publishing scripted inventory, device, availability and log messages, with a
  networkmap responder that can answer, stay silent or return an error; an
  injectable clock so schedule, time-out and 30-minute rules run in seconds.
- Inventory changes; link quality thinned to one value per 10 minutes;
  availability transitions; log lines classified and unknown ones sampled; a
  scan stored, a scan timing out, a scan skipped near a restart.
- Each detection rule fires on synthetic data and stays quiet on steady data,
  including a replay of a 2026-10-17-like jump: three devices go from about 2
  to 20 errors a day while two others go silent.
- Reconnect: the broker is stopped and restarted on the same port, losing its
  retained messages; the add-on subscribes again and availability, discovery
  and state reappear.
- The report runs against a copy of a test database.

## Open questions (with the default used meanwhile)

1. **Zigbee2MQTT log level:** is it `info`? Default: assumed yes; DOCS.md will
   explain how to set just `zh:ember:ezsp` to `info` if not.
2. **Repeated log lines** (point 3 above): are lower-bound counts enough, or
   should the add-on also read Zigbee2MQTT's log files? That needs read access
   to the other add-on's config folder. Default: MQTT only.
3. **A router's "parent":** routers have no real parent once joined. Default:
   the router's relationship-0 neighbour if any, else its route to the
   coordinator, else none.
4. **Branch name:** see Status. Default: the session branch.

## Still to be seen on the real mesh

- The real log level and how many route errors arrive over MQTT compared with
  the log files.
- Scan duration with routes for 5 routers, and whether the 15-min time-out fits.
- Whether end devices show up in more than one router's child table.
- What a router's relationship-0 neighbour and routes look like in practice.

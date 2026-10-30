# Zigbee Watch - notes

## Status

**0.1.0 built, tested here, not yet run on the Pi.** Change request 01 (plan
review) is done. Branch: `claude/laughing-newton-1l3am4`.

Tests: 38, all passing, against an in-process amqtt broker and a fake
Zigbee2MQTT; they need no house. Run them from `zigbee_watch/`:

    pip install -r requirements-dev.txt
    python -m pytest

| "Done when" item | Tests |
|---|---|
| inventory changes recorded | `test_mqtt.py::test_inventory_changes_recorded`, `test_odd_inventory_ignored` |
| LQI thinned as specified | `test_lqi_thinned_to_last_value_per_10_minutes`, `test_cached_republish_and_retained_messages_not_stored` |
| availability transitions stored | `test_availability_transitions_stored` |
| log lines classified, unknown ones sampled | `test_log_lines_classified_and_unknown_sampled` |
| a scan stored / timing out / skipped near a restart | `test_scan_stored`, `test_scan_timeout_reported_and_late_answer_ignored`, `test_scan_skipped_near_restart_then_run`, `test_scan_skipped_while_z2m_offline`, `test_scan_error_reported` |
| each rule fires on synthetic data, quiet on steady data | `test_detect.py` (parent, LQI per device and per map link, errors, not heard) |
| a 2026-10-17-like jump | `test_errors_replay_of_2026_10_17_found`, `test_errors_steady_quiet` |
| reconnect: resubscribe and republish | `test_reconnect_resubscribes_and_republishes`, `test_republish_on_home_assistant_online`, `test_last_will_marks_unavailable` |
| the report runs on a copy | `test_report_runs_on_a_copy`, `test_report_one_device_by_earlier_name_and_days` |
| well under 100 MB | `test_400_days_stay_well_under_100_mb`: 35 MB for 31 devices, 400 days |

Also checked here: `python -m zigbee_watch` starts from the environment as
`run.sh` sets it, connects, and stops cleanly. Both pinned base-image tags exist
on ghcr.io.

## Checked against the Zigbee2MQTT source

Read in `zigbee2mqtt@2.14.2` and `zigbee-herdsman@10.10.0`. File and line
references are in the code: `logpatterns.py` (every log text), `networkmap.py`
(request and response) and `ingest.py` (cached republish).

- **Route errors:** `info` level, target given as a decimal network address
  (`zh:adapter/ember/ezsp/ezsp.js:4303`). The `EmberStackError` names split
  them: `ROUTE_ERROR_*` are counted as `route_error`, `NETWORK_STATUS_*` as
  `network_status` (`zh:adapter/ember/enums.js:513-532`).
- **"Delivery of X failed for 'N'" is logged only for broadcast and multicast**
  (`zh:adapter/ember/adapter/emberAdapter.js:318-333`). N is a group or
  broadcast address, so it is stored with no device. A failed unicast rejects
  the request instead ("Delivery failed for '<nwk>'.", `oneWaitress.js:62`).
  It then surfaces inside Zigbee2MQTT's lines that name the device:
  `Publish ... to '<name>' failed: '...'` (`publish.js:286`),
  `Failed to ping '<name>'` (`availability.js:174`) and
  `Failed to read state of '<name>'` (`availability.js:325`). Inside those,
  the cause is classified as `delivery_failed` or `timeout` ("timed out after",
  `oneWaitress.js:144, 210`).
- **`bridge/logging` format:** `{"message", "level"}`, the message prefixed with
  the namespace (`util/logger.js:191`). It carries only lines that pass the log
  level, and drops a payload identical to the one before it
  (`extension/bridge.js:101-106`). Counts are therefore lower bounds; this is
  said in DOCS.md and in the `dekning` attribute.
- **Network map:** see the header of `networkmap.py`. Zigbee2MQTT has no
  time-out and no way to cancel, so a late answer is told apart by its
  transaction and ignored.
- **Cached-state republish:** Zigbee2MQTT republishes every device's cached
  state, cached `linkquality` included, at its startup
  (`controller.js:208-212`) and 30 s after `homeassistant/status` online
  (`extension/homeassistant.js:1913-1922`). These look exactly like fresh
  messages.
- **Pings are invisible:** active availability pings mains devices that have
  been quiet for the timeout, and publishes nothing unless the state changes
  (`extension/availability.js:134-198`).

## Changed or added since the plan

- **Cached republishes are not fresh.** Device messages within 120 s of
  Zigbee2MQTT coming online, or 150 s of Home Assistant coming online, are
  neither stored as link quality nor counted as heard. Neither are retained
  device messages. A message carrying `last_seen` still counts as heard at that
  time.
- **"Not heard":**
  - a device that Zigbee2MQTT currently reports `online` is taken as heard,
    because its pings and unpublished frames cannot be seen from MQTT;
  - the nightly map's `lastSeen` counts as heard;
  - silence is counted only over time during which both the add-on and
    Zigbee2MQTT were up (from the `listening` and `bridge_state` history),
    rather than restarting the clock after a gap.
- **Skipped scans are retried** until 3 hours after the scheduled time, so a
  power-cycle at 02:00 does not cost the night's map. Each skip reason is one
  row. Two statuses were added: `error: <msg>` (Zigbee2MQTT answered with an
  error) and `running`.
- **Daily work runs after the night's scan** (or once the retry window has
  passed): the error and link-quality rules, retention, then the snapshot. The
  copy therefore holds the night's map.
- **Errors rule:** judged per device and per kind. It needs at least 7 days on
  which the add-on listened for half the day or more, so a fresh install raises
  nothing. Failures caused by our own scan, and broadcast failures, do not
  count.
- **Map-link LQI rule:** the map gives only one value per night, so it needs 4
  maps in each 7-day window rather than 5.
- **`rutefeil_24t`** is unavailable while the add-on listened for less than 90 %
  of the last 24 h. That includes the first ~22 hours after install.
  `dekning` is an object: `{"lyttet_andel", "merknad"}`.
- **Report:** output is in Norwegian. It has an extra `--until DATE` option, for
  looking at a past window such as 2026-10-17.
- **Testing quirk:** in tests, amqtt 0.12 hands a new subscriber retained
  messages from topics outside its filter. The test probe filters them out; the
  add-on routes by topic and ignores them anyway.

## Open questions (with the default used meanwhile)

1. **`rutefeil_24t` after install or a long stop:** it stays unavailable until
   the add-on has 90 % of a day's coverage. Default: keep it, since it follows
   the rule that an untrusted input is unavailable. Say if a partial count with
   `dekning` would be more useful.
2. **Findings cannot be acknowledged** from Home Assistant; they close by
   themselves. Default: no button in 0.1.0, to keep the add-on free of command
   topics.

## Still to be seen on the real mesh

- **Image build:** not built here; there is no Docker daemon in this
  environment. `requirements.txt` is pure Python, so nothing needs compiling.
- **Route errors:** do they arrive on `bridge/logging` with the expected text,
  and how do the counts compare with Zigbee2MQTT's log files?
- **Unknown lines:** after a week, `log_unmatched` (and the report's "Ukjente
  advarsler/feil") will show which warnings this table does not know yet.
- **Republish windows:** are 120 s and 150 s long enough for the cached-state
  republish of 31 devices? Does Zigbee2MQTT have `last_seen` enabled?
- **Scan timing:** how long a scan with routes takes for 5 routers, and whether
  15 minutes fits.
- **Parents:**
  - whether end devices appear in more than one router's child table (recorded
    as `ambiguous`);
  - whether routers show a relationship-0 entry or only a route to the
    coordinator.
- **Entity ids:** that `default_entity_id` gives the five entity ids on Core
  2026.10.2.

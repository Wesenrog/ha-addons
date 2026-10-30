# 1. Review of the plan (NOTES.md at 622170d)

**Status:** open

**Approved. Build 0.1.0 as planned**, with the answers below. Checking the
herdsman and Zigbee2MQTT sources and citing them was exactly right; keep doing
that in `logpatterns.py`.

## Answers to the open questions

1. **Log level:** Zigbee2MQTT runs with `log_level: info` (read from its
   configuration on the Pi, 2026-10-30), so route errors reach
   `bridge/logging`. Keep the DOCS.md note for other installations.
2. **Repeated log lines:** MQTT only. Reading Zigbee2MQTT's log files would mean
   mapping Home Assistant's whole configuration folder, secrets included, into an
   observer. Lower-bound counts are enough, since the baseline rule compares each
   device with its own history. Say in DOCS.md and in the `dekning` attribute
   that counts are lower bounds and will not match the log files.
3. **A router's parent:** your default is fine (relationship-0 neighbour, else
   its route to the coordinator, else none).
4. **Branch:** the session branch is fine; `CLAUDE.md` now allows it.

## Also

- Keep the "not heard" rule even though it repeats Zigbee2MQTT's availability:
  it carries the maintenance-gap logic and lands in the same findings list.
- When 0.1.0 is done, set this request to `done 0.1.0` and bring `NOTES.md`
  up to date (status, anything that changed from the plan).

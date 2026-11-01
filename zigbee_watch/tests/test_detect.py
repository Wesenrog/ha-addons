"""Each change-detection rule fires on synthetic data and stays quiet on steady
data, including a replay of a 2026-10-17-like jump."""
from datetime import date, timedelta

import pytest

from conftest import DEVICES, E1, E2, E3, OSLO, R1, R2, T0, topology
from helpers import DAY, Harness


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path / "zw.sqlite", T0)


# --- parent changed -------------------------------------------------------------------
def test_parent_change_found_and_steady_quiet(h):
    now = T0
    for night in range(3):
        assert h.scan(topology({E1: R1, E2: R1, E3: R2}), now + night * DAY) == "ok"
    assert h.open_findings() == []                         # steady
    now += 3 * DAY
    h.scan(topology({E1: R2, E2: R1, E3: R2}), now)
    found = h.open_findings("parent_changed")
    assert len(found) == 1
    assert found[0]["device_id"] == h.dev("Termometer bad")
    assert found[0]["text"] == "Termometer bad har byttet forelder: Ruter stue → Ruter kjeller"
    # The next scan with the same parent adds nothing; the finding expires.
    h.scan(topology({E1: R2, E2: R1, E3: R2}), now + DAY)
    assert len(h.open_findings("parent_changed")) == 1
    h.det.expire_events(now + 8 * DAY)
    assert h.open_findings("parent_changed") == []


def test_ambiguous_or_missing_parent_is_no_finding(h):
    h.scan(topology({E1: R1}), T0)
    both = topology({E1: R1})
    both["links"].append({**both["links"][-1], "target": {"ieeeAddr": R2}, "targetIeeeAddr": R2})
    h.scan(both, T0 + DAY)                                 # listed by two routers
    h.scan(topology({}), T0 + 2 * DAY)                     # listed by none
    assert h.open_findings() == []
    hows = {r[0] for r in h.store.all("SELECT how FROM parent WHERE device_id = ?",
                                      (h.dev("Termometer bad"),))}
    assert hows == {"child_of", "ambiguous", "none"}


def test_router_parent_by_route_change(h):
    h.scan(topology({}), T0)
    moved = topology({})
    for link in moved["links"]:                            # R1's way to the coordinator now via R2
        if link["target"]["ieeeAddr"] == R1:
            link["routes"] = []
    moved["links"].append({"source": {"ieeeAddr": R2}, "target": {"ieeeAddr": R1}, "lqi": 120,
                           "depth": 2, "relationship": 2,
                           "routes": [{"destinationAddress": 0, "nextHopAddress": 0x1002}]})
    h.scan(moved, T0 + DAY)
    found = h.open_findings("parent_changed")
    assert [f["text"] for f in found] == ["Ruter stue har byttet forelder: Coordinator → Ruter kjeller"]


# --- link quality ----------------------------------------------------------------------
def test_lqi_drop_found_steady_quiet_and_closed_on_recovery(h):
    now = T0
    h.lqi("Termometer bad", now - 14 * DAY, now - 7 * DAY, 150)
    h.lqi("Termometer bad", now - 7 * DAY, now, 120)       # fell by 30
    h.lqi("Termometer kontor", now - 14 * DAY, now - 7 * DAY, 150)
    h.lqi("Termometer kontor", now - 7 * DAY, now, 135)    # fell by 15: under the threshold
    h.det.lqi(now)
    found = h.open_findings("lqi_drop")
    assert [f["text"] for f in found] == ["Linkkvalitet for Termometer bad har falt fra 150 til 120 (median 7 døgn)"]
    # A week later it is back.
    h.lqi("Termometer bad", now, now + 7 * DAY, 150)
    h.det.lqi(now + 7 * DAY)
    assert h.open_findings("lqi_drop") == []


def test_lqi_too_few_samples_is_quiet(h):
    h.lqi("Termometer bad", T0 - 14 * DAY, T0 - 14 * DAY + 3 * 600, 200)
    h.lqi("Termometer bad", T0 - DAY, T0, 50)
    h.det.lqi(T0)
    assert h.open_findings() == []


def test_map_link_lqi_drop(h):
    for night in range(14, 7, -1):
        h.scan(topology({E1: R1}, router_lqi=200), T0 - night * DAY)
    for night in range(7, 0, -1):
        h.scan(topology({E1: R1}, router_lqi=160), T0 - night * DAY)
    h.det.lqi(T0)
    texts = sorted(f["text"] for f in h.open_findings("lqi_drop"))
    assert "Link Ruter stue → Coordinator har falt fra 200 til 160 (median 7 døgn)" in texts
    assert all("150" not in t for t in texts)               # the steady end-device link is quiet


# --- errors against the device's own baseline ----------------------------------------------
def replay_2026_10_17(h, jump=True):
    """Before: about 5 route errors a day between three temperature sensors.
    2026-10-17: 57-67 between them, as two other sensors stopped."""
    day = date(2026, 10, 17)
    start, _ = OSLO.day_bounds(day - timedelta(days=20))
    h.listening(start, OSLO.day_bounds(day + timedelta(days=1))[1] + 3 * 3600)
    for i in range(20, 0, -1):
        d = day - timedelta(days=i)
        h.route_errors("Termometer bad", d, 2)
        h.route_errors("Termometer kontor", d, 2)
        h.route_errors("Vindu soverom", d, 1 + i % 2)
    if jump:
        h.route_errors("Termometer bad", day, 22)
        h.route_errors("Termometer kontor", day, 20)
        h.route_errors("Vindu soverom", day, 19)
    else:
        h.route_errors("Termometer bad", day, 3)
        h.route_errors("Termometer kontor", day, 2)
        h.route_errors("Vindu soverom", day, 1)
    return day


def test_errors_replay_of_2026_10_17_found(h):
    day = replay_2026_10_17(h)
    judged_at = OSLO.at(day + timedelta(days=1), "02:30")
    assert h.det.errors(day, judged_at) == 3
    texts = sorted(f["text"] for f in h.open_findings("errors"))
    assert texts == [
        "Rutefeil for Termometer bad: 22 2026-10-17 mot normalt 2 per døgn",
        "Rutefeil for Termometer kontor: 20 2026-10-17 mot normalt 2 per døgn",
        "Rutefeil for Vindu soverom: 19 2026-10-17 mot normalt 1.5 per døgn",
    ]
    # The next day is normal again: closed.
    day2 = day + timedelta(days=1)
    for name in ("Termometer bad", "Termometer kontor", "Vindu soverom"):
        h.route_errors(name, day2, 2)
    h.listening(judged_at, OSLO.at(day2 + timedelta(days=1), "03:00"))
    h.det.errors(day2, OSLO.at(day2 + timedelta(days=1), "02:30"))
    assert h.open_findings("errors") == []


def test_errors_steady_quiet(h):
    day = replay_2026_10_17(h, jump=False)
    assert h.det.errors(day, OSLO.at(day + timedelta(days=1), "02:30")) == 0
    assert h.open_findings() == []


def test_errors_need_a_baseline(h):
    """A fresh install has no history: nothing is "above baseline" yet."""
    day = date(2026, 10, 17)
    h.listening(OSLO.day_bounds(day - timedelta(days=3))[0], OSLO.day_bounds(day)[1])
    h.route_errors("Termometer bad", day, 40)
    assert h.det.errors(day, OSLO.day_bounds(day)[1]) == 0


def test_errors_from_our_own_scan_are_not_held_against_a_device(h):
    day = replay_2026_10_17(h, jump=False)
    h.route_errors("Ruter kjeller", day, 30, kind="scan_lqi_failed")
    assert h.det.errors(day, OSLO.at(day + timedelta(days=1), "02:30")) == 0


# --- not heard -------------------------------------------------------------------------
def test_not_heard_found_and_steady_quiet(h):
    now = T0
    h.listening(now - 3 * DAY, now)
    h.z2m(now - 3 * DAY, "online")
    ing = h.watch.ingest
    ing.on_bridge_state('{"state": "online"}', now - 3 * DAY)
    for name in ("Ruter stue", "Ruter kjeller", "Termometer bad", "Termometer kontor", "Vindu soverom"):
        ing.on_availability(h.watch.inventory.by_name[name], '{"state": "offline"}', now - 3 * DAY)
    ing._heard(h.dev("Termometer bad"), now - 26 * 3600)          # battery, 25 h limit: silent
    ing._heard(h.dev("Termometer kontor"), now - 24 * 3600)       # within the limit
    ing._heard(h.dev("Vindu soverom"), now - 26 * 3600)
    ing.on_availability(h.watch.inventory.by_name["Vindu soverom"], '{"state": "online"}', now - 3600)
    ing._heard(h.dev("Ruter stue"), now - 61 * 60)                # mains, 60 min: silent
    ing._heard(h.dev("Ruter kjeller"), now - 59 * 60)
    h.det.not_heard(now)
    found = {f["device_id"]: f["text"] for f in h.open_findings("not_heard")}
    assert sorted(found) == sorted([h.dev("Termometer bad"), h.dev("Ruter stue")])
    assert found[h.dev("Termometer bad")] == \
        f"Termometer bad er ikke hørt siden {OSLO.format(now - 26 * 3600)}"
    # Heard again: closed.
    ing._heard(h.dev("Termometer bad"), now + 60)
    h.det.not_heard(now + 120)
    assert [f["device_id"] for f in h.open_findings("not_heard")] == [h.dev("Ruter stue")]


def test_not_heard_ignores_time_when_nobody_could_hear(h):
    """Maintenance gaps: time with Zigbee2MQTT down or this add-on not running
    does not count as silence."""
    now = T0
    h.listening(now - 3 * DAY, now - 20 * 3600)                    # add-on down 20 h ...
    h.listening(now - 18 * 3600, now)                              # ... for 2 h
    ing = h.watch.ingest
    h.z2m(now - 3 * DAY, "online")
    h.z2m(now - 10 * 3600, "offline")                              # Zigbee2MQTT down 1 h
    h.z2m(now - 9 * 3600, "online")
    ing.on_availability(h.watch.inventory.by_name["Termometer bad"], '{"state": "offline"}', now)
    ing._heard(h.dev("Termometer bad"), now - 26 * 3600)           # 26 h, of which 23 observed
    bad = h.dev("Termometer bad")

    def silent():
        return [f for f in h.open_findings("not_heard") if f["device_id"] == bad]
    h.det.not_heard(now)
    assert silent() == []
    h.listening(now, now + 3 * 3600)                               # 3 more observed hours
    h.det.not_heard(now + 3 * 3600)
    assert len(silent()) == 1


def test_not_heard_quiet_for_a_device_that_left(h):
    now = T0
    h.listening(now - 3 * DAY, now)
    h.z2m(now - 3 * DAY, "online")
    h.det.not_heard(now)                                   # nothing heard since added 60 days ago
    assert len(h.open_findings("not_heard")) == 6 - 1      # all but the coordinator
    h.watch.inventory.update([d for d in DEVICES if d["ieee_address"] != E3], now)
    h.det.not_heard(now + 300)
    gone = h.watch.inventory.by_ieee[E3].id
    assert gone not in [f["device_id"] for f in h.open_findings("not_heard")]
    assert len(h.open_findings("not_heard")) == 4


# --- the schedule around clock changes ------------------------------------------------------
@pytest.mark.parametrize("day, utc_hour", [
    (date(2027, 3, 28), 1),     # 02:15 does not exist: runs 03:15 CEST = 01:15 UTC
    (date(2026, 10, 25), 0),    # 02:15 happens twice: the first, CEST = 00:15 UTC
    (date(2026, 11, 2), 1),     # an ordinary night, CET
])
def test_scan_time_around_clock_changes(h, day, utc_hour):
    due = OSLO.at(day, "02:15")
    assert due % DAY == utc_hour * 3600 + 15 * 60
    sc = h.watch.scanner
    h.watch.ingest.on_bridge_state('{"state": "online"}', due - DAY)
    sc.tick(due - 1)
    assert sc.pending is None
    sc.tick(due)
    assert sc.pending is not None
    sc.on_response({"status": "ok", "transaction": sc.pending["txn"],
                    "data": {"value": topology({})}}, due + 30)
    for t in range(due + 60, due + 2 * 3600, 600):          # the repeated hour: still once
        sc.tick(t)
    assert sc.pending is None
    assert h.store.one("SELECT COUNT(*) FROM scan WHERE status = 'ok'")[0] == 1

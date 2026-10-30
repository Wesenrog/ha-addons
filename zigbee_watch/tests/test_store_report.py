"""Storage (retention, copy-safe snapshot, size) and the report."""
import json, os, random, shutil, sqlite3, subprocess, sys
from datetime import timedelta

import pytest

from conftest import DAY0, E1, E2, E3, OSLO, R1, R2, T0, topology
from helpers import DAY, Harness

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_retention_prunes_old_rows_keeps_recent_and_open(tmp_path):
    h = Harness(tmp_path / "zw.sqlite", T0, retention_days=30)
    old, new = T0 - 40 * DAY, T0 - DAY
    for t in (old, new):
        h.lqi("Termometer bad", t, t + 600, 100)
        h.route_errors("Termometer bad", OSLO.date_of(t), 1)
        h.scan(topology({E1: R1}), t)
    h.det.open("x", None, "test", "open since long", old)
    h.store.execute("INSERT INTO finding (ts, kind, key, text, closed_ts) VALUES (?,?,?,?,?)",
                    (old, "test", "y", "closed long ago", old))
    h.store.prune(T0, 30)
    assert h.store.one("SELECT COUNT(*) FROM lqi")[0] == 1
    assert h.store.one("SELECT COUNT(*) FROM log_event")[0] == 1
    assert h.store.one("SELECT COUNT(*) FROM scan")[0] == 1
    assert h.store.one("SELECT COUNT(DISTINCT scan_id) FROM scan_link")[0] == 1
    assert [r[0] for r in h.store.all("SELECT key FROM finding")] == ["x"]
    assert h.store.one("SELECT COUNT(*) FROM device")[0] == 6    # the inventory is kept


def test_snapshot_is_complete_and_safe_to_copy(tmp_path):
    h = Harness(tmp_path / "zw.sqlite", T0)
    h.watch.ingest.on_bridge_state('{"state": "online"}', T0 - DAY)
    h.lqi("Termometer bad", T0 - 3600, T0, 111)
    h.watch.scanner.done_date = OSLO.date_of(T0)
    h.watch.tick(T0)                                     # the daily work, after the scan
    copy = tmp_path / "zigbee_watch-copy.sqlite"
    assert copy.exists()
    db = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    assert db.execute("SELECT COUNT(*) FROM lqi").fetchone()[0] == 6
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert h.store.one("PRAGMA journal_mode")[0] == "wal"


@pytest.mark.slow
def test_400_days_stay_well_under_100_mb(tmp_path):
    """31 devices, link quality in every 10-minute bucket for 400 days, a
    nightly map and a generous log: the brief's size budget."""
    path = tmp_path / "big.sqlite"
    h = Harness(path, T0)
    devs = [h.watch.inventory.by_name[n].id for n in h.watch.inventory.by_name]
    devs = (devs * 6)[:31]
    rnd = random.Random(1)
    start = T0 - 400 * DAY
    rows = ((d * 1000 + i, t, rnd.randint(60, 220))
            for i, d in enumerate(devs) for t in range(start, T0, 600))
    with h.store.transaction() as db:
        db.executemany("INSERT INTO lqi VALUES (?,?,?)", rows)
        db.executemany("INSERT INTO log_event VALUES (?,?,?,?,?)",
                       ((start + k * 300, devs[k % 31], 0x2000 + k % 31, "route_error",
                         "ROUTE_ERROR_NON_TREE_LINK_FAILURE") for k in range(400 * 100)))
        db.executemany("INSERT INTO availability VALUES (?,?,?)",
                       ((start + k * 3600, devs[k % 31], ("online", "offline")[k % 2])
                        for k in range(400 * 5)))
    value = topology({E1: R1, E2: R1, E3: R2})
    value["links"] = value["links"] * 9                  # about 60 links a night
    for night in range(400):
        h.scan(value, start + night * DAY)
    h.store.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size = os.path.getsize(path) / 1e6
    print(f"400-day database: {size:.1f} MB")
    assert h.store.one("SELECT COUNT(*) FROM lqi")[0] == 31 * 400 * 144
    assert size < 60


def build_history(tmp_path):
    """Two weeks of a small house, ending on DAY0 + 1."""
    h = Harness(tmp_path / "zw.sqlite", T0)
    ing = h.watch.ingest
    h.listening(T0 - 20 * DAY, T0 + DAY)
    h.z2m(T0 - 20 * DAY, "online")
    for night in range(14, -1, -1):
        parents = {E1: R1 if night > 3 else R2, E2: R1, E3: R2}
        h.scan(topology(parents, router_lqi=200 - night), OSLO.at(DAY0 - timedelta(days=night), "02:15"))
    h.lqi("Termometer bad", T0 - 14 * DAY, T0 - 7 * DAY, 150)
    h.lqi("Termometer bad", T0 - 7 * DAY, T0, 110)
    for i in range(14, 0, -1):
        h.route_errors("Termometer bad", DAY0 - timedelta(days=i), 2)
    h.route_errors("Termometer bad", DAY0, 25)
    dev = h.watch.inventory.by_name["Ruter kjeller"]
    ing.on_availability(dev, '{"state": "offline"}', T0 - 2 * DAY)
    ing.on_availability(dev, '{"state": "online"}', T0 - 2 * DAY + 1800)
    h.watch.logs.handle({"message": "z2m: Something new went wrong", "level": "error"}, T0)
    renamed = [dict(d) for d in __import__("conftest").DEVICES]
    renamed[3]["friendly_name"] = "Termometer badet"
    h.watch.inventory.update(renamed, T0 - DAY)
    h.det.lqi(T0)
    h.det.errors(DAY0, T0)
    h.store.snapshot(str(tmp_path / "zigbee_watch-copy.sqlite"))
    return h


def run_report(db, *args):
    return subprocess.run([sys.executable, "-m", "zigbee_watch.report", "--db", str(db), *args],
                          cwd=PKG, capture_output=True, text=True, timeout=60)


def test_report_runs_on_a_copy(tmp_path):
    build_history(tmp_path)
    off_pi = tmp_path / "elsewhere"
    off_pi.mkdir()
    shutil.copy(tmp_path / "zigbee_watch-copy.sqlite", off_pi / "copy.sqlite")
    result = run_report(off_pi / "copy.sqlite", "--until", (DAY0 + timedelta(days=1)).isoformat())
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Funn (" in out and "Rutefeil for Termometer badet: 25" in out
    assert "Linkkvalitet for Termometer badet har falt fra 150 til 110" in out
    assert "Termometer badet  (EndDevice, Battery" in out
    assert "nytt navn Termometer bad → Termometer badet" in out
    assert "Ruter stue (barn av)" in out and "Ruter kjeller (barn av)" in out   # parent history
    assert "borte " in out and "(30 min)" in out                                # availability drop
    assert "route_error 25" in out                                              # errors per day
    assert "z2m: Something new went wrong" in out                               # unknown sampled
    assert " ok" in out                                                          # scans listed


def test_report_one_device_by_earlier_name_and_days(tmp_path):
    build_history(tmp_path)
    copy = tmp_path / "zigbee_watch-copy.sqlite"
    until = (DAY0 + timedelta(days=1)).isoformat()
    result = run_report(copy, "--device", "Termometer bad", "--days", "3", "--until", until)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("=" * 72)
    assert result.stdout.count("Termometer badet  (") == 1
    assert "Ruter kjeller" not in result.stdout.split("Linkkvalitet")[0].split("Forelder")[0]
    missing = run_report(copy, "--device", "Finnes ikke", "--until", until)
    assert missing.returncode != 0 and "Finnes ikke" in missing.stderr


def test_rutefeil_sensor_counts_and_trusts_only_full_coverage(tmp_path):
    h = Harness(tmp_path / "zw.sqlite", T0)
    w = h.watch
    for nwk, n in ((0x2001, 3), (0x2002, 1), (0xBEEF, 2)):
        for _ in range(n):
            w.logs.handle({"message": f'zh:ember:ezsp: Received network/route error '
                                      f'ROUTE_ERROR_NO_ROUTE_AVAILABLE for "{nwk}".', "level": "info"},
                          T0 - 3600)
    w.on_connected(T0 - 600)                               # listened only 10 min of 24 h
    w.publish_states(T0)
    assert h.published["zigbee_watch/rutefeil_24t/state"] == "6"
    assert h.published["zigbee_watch/rutefeil_24t/available"] == "offline"
    attrs = json.loads(h.published["zigbee_watch/rutefeil_24t/attributes"])
    assert attrs["enheter"] == {"Termometer bad": 3, "Termometer kontor": 1}
    assert attrs["ukjent_adresse"] == 2
    assert "Nedre grense" in attrs["dekning"]["merknad"]
    h.listening(T0 - DAY, T0 - 600)
    w.publish_states(T0)
    assert h.published["zigbee_watch/rutefeil_24t/available"] == "online"

"""A report the owner can read without SQL.

    python -m zigbee_watch.report [--db FILE] [--days N] [--device NAME] [--until DATE]

Works on a copy of the database off the Pi (standard library plus tzdata).
Per device: parent history, link quality per day, availability drops and
errors per day per kind, in local time.
"""
import argparse, sqlite3, sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

DEFAULT_DB = "/share/zigbee_watch/zigbee_watch.sqlite"

HOW = {"child_of": "barn av", "parent_entry": "nabotabell", "route": "rute mot koordinator"}


class Report:
    def __init__(self, db, tz, days, now=None):
        self.db = db
        self.tz = ZoneInfo(tz)
        self.now = int(now if now is not None else datetime.now().timestamp())
        self.since = self.now - days * 86400
        self.days = days
        self.names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM device")}

    def t(self, ts, fmt="%Y-%m-%d %H:%M"):
        return datetime.fromtimestamp(ts, self.tz).strftime(fmt)

    def day(self, ts):
        return datetime.fromtimestamp(ts, self.tz).date().isoformat()

    def name(self, device_id):
        return self.names.get(device_id, "?") if device_id is not None else "-"

    def find(self, name):
        row = self.db.execute("SELECT id FROM device WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        row = self.db.execute(
            "SELECT device_id FROM device_event WHERE kind = 'renamed' AND old = ?"
            " ORDER BY ts DESC LIMIT 1", (name,)).fetchone()
        return row["device_id"] if row else None

    # --- sections -------------------------------------------------------------
    def overview(self, out):
        out.append(f"Zigbee Watch - {self.t(self.since)} til {self.t(self.now)} ({self.tz.key})")
        out.append("")
        findings = self.db.execute(
            "SELECT * FROM finding WHERE closed_ts IS NULL OR closed_ts >= ? ORDER BY ts",
            (self.since,)).fetchall()
        out.append(f"Funn ({sum(1 for f in findings if f['closed_ts'] is None)} åpne)")
        for f in findings:
            state = "åpen" if f["closed_ts"] is None else f"lukket {self.t(f['closed_ts'])}"
            out.append(f"  {self.t(f['ts'])}  {f['text']}  [{state}]")
        if not findings:
            out.append("  ingen")
        out.append("")
        out.append("Nettverkskart")
        for s in self.db.execute("SELECT * FROM scan WHERE requested >= ? ORDER BY id", (self.since,)):
            took = f", {s['finished'] - s['requested']} s" if s["finished"] and s["status"] == "ok" else ""
            detail = f" ({s['detail']})" if s["detail"] else ""
            out.append(f"  {self.t(s['requested'])}  {s['status']}{detail}{took}")
        out.append("")
        events = self.db.execute(
            "SELECT * FROM device_event WHERE ts >= ? ORDER BY ts", (self.since,)).fetchall()
        if events:
            out.append("Inventar")
            for e in events:
                change = {"appeared": "dukket opp", "disappeared": "forsvant",
                          "renamed": f"nytt navn {e['old']} → {e['new']}",
                          "network_address": f"ny nettverksadresse {e['old']} → {e['new']}",
                          "type": f"ny type {e['old']} → {e['new']}"}.get(e["kind"], e["kind"])
                out.append(f"  {self.t(e['ts'])}  {self.name(e['device_id'])}: {change}")
            out.append("")
        unmatched = self.db.execute(
            "SELECT * FROM log_unmatched WHERE last_ts >= ? ORDER BY count DESC LIMIT 10",
            (self.since,)).fetchall()
        if unmatched:
            out.append("Ukjente advarsler/feil i loggen (mest vanlige)")
            for u in unmatched:
                out.append(f"  {u['count']:>5}  {u['level']}: {u['sample'][:160]}")
            out.append("")

    def device(self, out, dev):
        out.append("=" * 72)
        removed = f", borte siden {self.t(dev['removed_at'])}" if dev["removed_at"] else ""
        out.append(f"{dev['name']}  ({dev['type'] or '?'}, {dev['power_source'] or '?'},"
                   f" {dev['model'] or '?'}, {dev['ieee']}{removed})")
        heard = self.db.execute("SELECT ts FROM heard WHERE device_id = ?", (dev["id"],)).fetchone()
        if heard:
            out.append(f"  sist hørt {self.t(heard['ts'])}")

        out.append("  Forelder")
        last = object()
        rows = self.db.execute(
            "SELECT s.requested, p.parent_id, p.how FROM parent p JOIN scan s ON s.id = p.scan_id"
            " WHERE p.device_id = ? AND s.requested >= ? ORDER BY s.id", (dev["id"], self.since))
        any_row = False
        for r in rows:
            any_row = True
            current = (r["parent_id"], r["how"])
            if current != last:
                how = HOW.get(r["how"], {"ambiguous": "tvetydig", "none": "ukjent"}.get(r["how"], r["how"]))
                out.append(f"    fra {self.t(r['requested'], '%Y-%m-%d')}: {self.name(r['parent_id'])} ({how})")
                last = current
        if not any_row:
            out.append("    ingen kart i perioden")

        out.append("  Linkkvalitet per døgn (median / min / antall)")
        per_day = defaultdict(list)
        for r in self.db.execute("SELECT bucket, lqi FROM lqi WHERE device_id = ? AND bucket >= ?",
                                 (dev["id"], self.since)):
            per_day[self.day(r["bucket"])].append(r["lqi"])
        for d in sorted(per_day):
            v = per_day[d]
            out.append(f"    {d}  {median(v):5.0f} {min(v):5d} {len(v):5d}  {'#' * (int(median(v)) // 10)}")
        if not per_day:
            out.append("    ingen målinger")
        links = self.db.execute(
            "SELECT s.requested, l.source_id, l.target_id, l.source_ieee, l.target_ieee, l.lqi,"
            " l.relationship FROM scan_link l JOIN scan s ON s.id = l.scan_id"
            " WHERE (l.source_id = ? OR l.target_id = ?) AND s.requested >= ? ORDER BY s.id",
            (dev["id"], dev["id"], self.since)).fetchall()
        if links:
            out.append("  Linker i nettverkskartene (LQI per kart)")
            by_link = defaultdict(list)
            for l in links:
                other = l["target_id"] if l["source_id"] == dev["id"] else l["source_id"]
                other_name = self.name(other) if other else (
                    l["target_ieee"] if l["source_id"] == dev["id"] else l["source_ieee"])
                by_link[other_name].append(str(l["lqi"]))
            for other, values in sorted(by_link.items()):
                out.append(f"    {other}: {' '.join(values[-14:])}")

        out.append("  Tilgjengelighet")
        drops = self.db.execute(
            "SELECT ts, state FROM availability WHERE device_id = ? AND ts >= ? ORDER BY ts",
            (dev["id"], self.since)).fetchall()
        shown = False
        for i, a in enumerate(drops):
            if a["state"] != "offline":
                continue
            back = next((b["ts"] for b in drops[i + 1:] if b["state"] == "online"), None)
            span = f"tilbake {self.t(back)} ({(back - a['ts']) // 60} min)" if back else "fortsatt borte"
            out.append(f"    borte {self.t(a['ts'])}, {span}")
            shown = True
        if not shown:
            out.append("    ingen fall")

        out.append("  Feil per døgn")
        counts = defaultdict(lambda: defaultdict(int))
        for r in self.db.execute("SELECT ts, kind FROM log_event WHERE device_id = ? AND ts >= ?",
                                 (dev["id"], self.since)):
            counts[self.day(r["ts"])][r["kind"]] += 1
        for d in sorted(counts):
            kinds = ", ".join(f"{k} {n}" for k, n in sorted(counts[d].items()))
            out.append(f"    {d}  {kinds}")
        if not counts:
            out.append("    ingen")

    def render(self, device_name=None):
        out = []
        if device_name:
            device_id = self.find(device_name)
            if device_id is None:
                raise SystemExit(f"ingen enhet heter {device_name!r}")
            devices = self.db.execute("SELECT * FROM device WHERE id = ?", (device_id,)).fetchall()
        else:
            self.overview(out)
            devices = self.db.execute(
                "SELECT * FROM device WHERE removed_at IS NULL OR removed_at >= ?"
                " ORDER BY type = 'Coordinator' DESC, type, name", (self.since,)).fetchall()
        for dev in devices:
            self.device(out, dev)
        return "\n".join(out) + "\n"


def open_db(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("SELECT 1 FROM device LIMIT 1")
    return db


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m zigbee_watch.report", description=__doc__.split("\n")[0])
    p.add_argument("--db", default=DEFAULT_DB, help=f"the database (default {DEFAULT_DB})")
    p.add_argument("--days", type=int, default=14, help="how far back (default 14)")
    p.add_argument("--device", help="only this device (current or earlier name)")
    p.add_argument("--until", help="end of the period, a local date YYYY-MM-DD (default now)")
    p.add_argument("--tz", default="Europe/Oslo", help="time zone for the report")
    args = p.parse_args(argv)
    now = None
    if args.until:
        end = datetime.combine(date.fromisoformat(args.until) + timedelta(days=1), time(0),
                               ZoneInfo(args.tz))
        now = end.timestamp()
    try:
        db = open_db(args.db)
    except sqlite3.Error as exc:
        raise SystemExit(f"kan ikke lese {args.db}: {exc}")
    sys.stdout.write(Report(db, args.tz, args.days, now).render(args.device))


if __name__ == "__main__":
    main()

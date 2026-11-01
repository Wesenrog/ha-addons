"""SQLite storage: schema, retention and the copy-safe nightly snapshot.

All timestamps are UTC epoch seconds. Devices are referred to by an integer id
(`device.id`) so the large tables stay small; the IEEE address is the identity.
"""
import logging, os, sqlite3

_LOG = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS device (
    id INTEGER PRIMARY KEY,
    ieee TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    type TEXT,
    power_source TEXT,
    model TEXT,
    vendor TEXT,
    network_address INTEGER,
    first_seen INTEGER NOT NULL,
    removed_at INTEGER
);

-- appeared, disappeared, renamed, network_address, type
CREATE TABLE IF NOT EXISTS device_event (
    ts INTEGER NOT NULL, device_id INTEGER NOT NULL,
    kind TEXT NOT NULL, old TEXT, new TEXT
);
CREATE INDEX IF NOT EXISTS device_event_dev ON device_event (device_id, ts);

-- Last linkquality per device per bucket (bucket = start, epoch seconds).
CREATE TABLE IF NOT EXISTS lqi (
    device_id INTEGER NOT NULL, bucket INTEGER NOT NULL, lqi INTEGER NOT NULL,
    PRIMARY KEY (device_id, bucket)
) WITHOUT ROWID;

-- When each device was last really heard (not a cached republish).
CREATE TABLE IF NOT EXISTS heard (
    device_id INTEGER PRIMARY KEY, ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS availability (
    ts INTEGER NOT NULL, device_id INTEGER NOT NULL, state TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS availability_dev ON availability (device_id, ts);

-- Zigbee2MQTT's own bridge/state transitions (restarts, outages).
CREATE TABLE IF NOT EXISTS bridge_state (ts INTEGER NOT NULL, state TEXT NOT NULL);

-- Intervals during which this add-on was connected to the broker.
CREATE TABLE IF NOT EXISTS listening (
    id INTEGER PRIMARY KEY, start INTEGER NOT NULL, end INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS log_event (
    ts INTEGER NOT NULL, device_id INTEGER, network_address INTEGER,
    kind TEXT NOT NULL, detail TEXT
);
CREATE INDEX IF NOT EXISTS log_event_ts ON log_event (ts);
CREATE INDEX IF NOT EXISTS log_event_dev ON log_event (device_id, ts);

CREATE TABLE IF NOT EXISTS log_unmatched (
    key TEXT PRIMARY KEY, level TEXT, sample TEXT NOT NULL,
    count INTEGER NOT NULL, first_ts INTEGER NOT NULL, last_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS scan (
    id INTEGER PRIMARY KEY, requested INTEGER NOT NULL, finished INTEGER,
    txn TEXT, status TEXT NOT NULL, detail TEXT
);

CREATE TABLE IF NOT EXISTS scan_node (
    scan_id INTEGER NOT NULL, device_id INTEGER, ieee TEXT NOT NULL,
    name TEXT, type TEXT, network_address INTEGER, failed TEXT, last_seen INTEGER
);
CREATE INDEX IF NOT EXISTS scan_node_scan ON scan_node (scan_id);

CREATE TABLE IF NOT EXISTS scan_link (
    scan_id INTEGER NOT NULL,
    source_ieee TEXT NOT NULL, target_ieee TEXT NOT NULL,
    source_id INTEGER, target_id INTEGER,
    lqi INTEGER, depth INTEGER, relationship INTEGER, routes TEXT
);
CREATE INDEX IF NOT EXISTS scan_link_scan ON scan_link (scan_id);

-- Each device's parent as derived from one scan. how: child_of (an end
-- device listed as child), parent_entry (relationship 0), route (next hop
-- to the coordinator), ambiguous, none.
CREATE TABLE IF NOT EXISTS parent (
    scan_id INTEGER NOT NULL, device_id INTEGER NOT NULL,
    parent_id INTEGER, how TEXT NOT NULL,
    PRIMARY KEY (scan_id, device_id)
);

CREATE TABLE IF NOT EXISTS finding (
    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, device_id INTEGER,
    kind TEXT NOT NULL, key TEXT NOT NULL, text TEXT NOT NULL,
    closed_ts INTEGER
);
CREATE INDEX IF NOT EXISTS finding_open ON finding (closed_ts, key);
"""

# Rows older than the retention are dropped from these, by their time column.
_RETAINED = [
    ("device_event", "ts"), ("lqi", "bucket"), ("availability", "ts"),
    ("bridge_state", "ts"), ("listening", "end"), ("log_event", "ts"),
    ("log_unmatched", "last_ts"), ("finding", "closed_ts"),
]


class Store:
    def __init__(self, path):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        new = path == ":memory:" or not os.path.exists(path)
        # One worker thread owns the connection; the flag only allows that
        # thread to be a different one from the thread that opened it.
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if new:
            # Must precede the first table to take effect.
            self.db.execute("PRAGMA auto_vacuum = INCREMENTAL")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))

    def close(self):
        self.db.close()

    def execute(self, sql, params=()):
        return self.db.execute(sql, params)

    def one(self, sql, params=()):
        return self.db.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.db.execute(sql, params).fetchall()

    def transaction(self):
        return _Transaction(self.db)

    # --- maintenance ----------------------------------------------------------
    def prune(self, now, retention_days):
        cutoff = now - retention_days * 86400
        with self.transaction():
            for table, column in _RETAINED:
                self.db.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
            old = "SELECT id FROM scan WHERE requested < ?"
            for table in ("scan_node", "scan_link", "parent"):
                self.db.execute(f"DELETE FROM {table} WHERE scan_id IN ({old})", (cutoff,))
            self.db.execute("DELETE FROM scan WHERE requested < ?", (cutoff,))
        self.db.execute("PRAGMA incremental_vacuum")

    def snapshot(self, target):
        """Write a complete, self-contained copy that is safe to copy at any
        time. The live file alone can miss what is still in the -wal file."""
        tmp = target + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        self.db.execute("VACUUM INTO ?", (tmp,))
        os.replace(tmp, target)
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


class _Transaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        self.db.execute("COMMIT" if exc_type is None else "ROLLBACK")
        return False

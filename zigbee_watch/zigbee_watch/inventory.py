"""The device inventory from Zigbee2MQTT's retained `bridge/devices`.

Devices are keyed by IEEE address, never by friendly name, so a rename keeps
the history. Lookups by name (device topics, Zigbee2MQTT log lines) and by
network address (herdsman log lines) go through the maps kept here.
"""
import logging
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)


@dataclass
class Device:
    id: int
    ieee: str
    name: str
    type: str | None
    power_source: str | None
    model: str | None
    vendor: str | None
    network_address: int | None
    removed_at: int | None

    @property
    def battery(self):
        return (self.power_source or "").lower() == "battery"


class Inventory:
    def __init__(self, store):
        self.store = store
        self.devices = {}          # id -> Device
        self.by_ieee = {}
        self.by_name = {}          # current names of present devices
        self.by_nwk = {}
        for row in store.all("SELECT * FROM device"):
            self._remember(Device(**{k: row[k] for k in Device.__dataclass_fields__}))
        self.known = bool(self.devices)

    def _remember(self, dev):
        old = self.devices.get(dev.id)
        if old:
            if self.by_name.get(old.name) is old:
                del self.by_name[old.name]
            if old.network_address is not None and self.by_nwk.get(old.network_address) is old:
                del self.by_nwk[old.network_address]
        self.devices[dev.id] = dev
        self.by_ieee[dev.ieee] = dev
        if dev.removed_at is None:
            self.by_name[dev.name] = dev
            if dev.network_address is not None:
                self.by_nwk[dev.network_address] = dev

    def present(self):
        return [d for d in self.devices.values() if d.removed_at is None]

    def find_name(self, name):
        """Current name first, then any earlier name (for the report and for
        log lines that race a rename)."""
        dev = self.by_name.get(name)
        if dev:
            return dev
        row = self.store.one(
            "SELECT device_id FROM device_event WHERE kind = 'renamed' AND old = ? "
            "ORDER BY ts DESC LIMIT 1", (name,))
        return self.devices.get(row["device_id"]) if row else None

    # --- bridge/devices -------------------------------------------------------
    def update(self, payload, now):
        """Apply one bridge/devices message. Returns the number of changes.

        A payload that is not a non-empty list of devices with IEEE addresses
        is ignored rather than taken to mean "every device left".
        """
        if not isinstance(payload, list) or not payload:
            _LOG.warning("bridge/devices is not a device list - ignored")
            return 0
        seen = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            ieee = item.get("ieee_address")
            name = item.get("friendly_name")
            if not isinstance(ieee, str) or not isinstance(name, str):
                continue
            definition = item.get("definition") if isinstance(item.get("definition"), dict) else {}
            nwk = item.get("network_address")
            seen[ieee] = dict(
                name=name,
                type=item.get("type"),
                power_source=item.get("power_source"),
                model=definition.get("model") or item.get("model_id"),
                vendor=definition.get("vendor") or item.get("manufacturer"),
                network_address=nwk if isinstance(nwk, int) else None,
            )
        if not seen:
            _LOG.warning("bridge/devices carried no usable device - ignored")
            return 0

        changes = 0
        with self.store.transaction() as db:
            for ieee, info in seen.items():
                dev = self.by_ieee.get(ieee)
                if dev is None:
                    cur = db.execute(
                        "INSERT INTO device (ieee, name, type, power_source, model, vendor,"
                        " network_address, first_seen) VALUES (?,?,?,?,?,?,?,?)",
                        (ieee, info["name"], info["type"], info["power_source"],
                         info["model"], info["vendor"], info["network_address"], now))
                    dev = Device(id=cur.lastrowid, ieee=ieee, removed_at=None, **info)
                    self._event(db, now, dev.id, "appeared", None, info["name"])
                    self._remember(dev)
                    changes += 1
                    continue
                events = []
                if dev.removed_at is not None:
                    events.append(("appeared", None, info["name"]))
                if dev.name != info["name"]:
                    events.append(("renamed", dev.name, info["name"]))
                if info["network_address"] is not None and dev.network_address != info["network_address"]:
                    events.append(("network_address", _hex(dev.network_address),
                                   _hex(info["network_address"])))
                if info["type"] and dev.type and dev.type != info["type"]:
                    events.append(("type", dev.type, info["type"]))
                for kind, old, new in events:
                    self._event(db, now, dev.id, kind, old, new)
                changes += len(events)
                if events or any(getattr(dev, k) != v for k, v in info.items() if v is not None):
                    merged = {k: (v if v is not None else getattr(dev, k)) for k, v in info.items()}
                    db.execute(
                        "UPDATE device SET name=?, type=?, power_source=?, model=?, vendor=?,"
                        " network_address=?, removed_at=NULL WHERE id=?",
                        (merged["name"], merged["type"], merged["power_source"], merged["model"],
                         merged["vendor"], merged["network_address"], dev.id))
                    self._remember(Device(id=dev.id, ieee=ieee, removed_at=None, **merged))
            for dev in list(self.present()):
                if dev.ieee not in seen:
                    db.execute("UPDATE device SET removed_at=? WHERE id=?", (now, dev.id))
                    self._event(db, now, dev.id, "disappeared", dev.name, None)
                    dev.removed_at = now
                    self._remember(dev)
                    changes += 1
        self.known = True
        if changes:
            _LOG.info("inventory: %d change(s), %d devices present", changes, len(self.present()))
        return changes

    @staticmethod
    def _event(db, ts, device_id, kind, old, new):
        db.execute("INSERT INTO device_event VALUES (?,?,?,?,?)", (ts, device_id, kind, old, new))


def _hex(nwk):
    return None if nwk is None else f"0x{nwk:04x}"

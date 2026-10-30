"""The nightly network map: the only request Zigbee Watch sends to Zigbee2MQTT.

Request and response, read in z2m:extension/networkMap.js (2.14.2):

- request  `<base>/bridge/request/networkmap` {"type": "raw", "routes": true,
  "transaction": ...}; the response on `<base>/bridge/response/networkmap`
  echoes the transaction, as {"data": {"type", "routes", "value": {nodes,
  links}}, "status": "ok"} or {"data": {}, "status": "error", "error": ...}
  (lines 63-90).
- `nodes[]`: ieeeAddr, friendlyName, type, networkAddress, failed (["lqi",
  "routingTable"] for a router that did not answer), lastSeen in ms (262-272).
- `links[]`: one per neighbour-table entry with relationship <= 3, as
  source = the neighbour, target = the router whose table it is; lqi, depth,
  relationship (0 parent, 1 child, 2 sibling, 3 none), and routes = the
  target's routing-table entries whose next hop is the source (275-321).
- End devices are never queried (211); they appear only as neighbours.
- Zigbee2MQTT has no time-out and no way to cancel a scan, so the time-out is
  ours and a late response is recognised by its transaction and ignored.
"""
import json, logging

_LOG = logging.getLogger(__name__)

TXN_PREFIX = "zigbee_watch-"
# A skipped scan is retried until this long after the scheduled time, so a
# power-cycle shortly before 02:15 does not cost the night's map.
RETRY_WINDOW = 3 * 3600


class Scanner:
    def __init__(self, store, inventory, ingest, cfg, local, publish):
        self.store = store
        self.inventory = inventory
        self.ingest = ingest
        self.cfg = cfg
        self.local = local
        self.publish = publish
        self.pending = None            # {"id", "txn", "deadline"}
        self.done_date = None          # local date whose scan is settled
        self.skip_reason = None        # last skip recorded for the current date
        last = store.one("SELECT requested, status FROM scan ORDER BY id DESC LIMIT 1")
        if last and not last["status"].startswith("skipped"):
            self.done_date = local.date_of(last["requested"])
        # A scan left running by a previous process never gets its answer.
        store.execute("UPDATE scan SET status = 'timeout', detail = 'add-on restarted'"
                      " WHERE status = 'running'")
        self.on_finished = None        # callback(scan_id, status, now)

    def latest(self):
        return self.store.one("SELECT * FROM scan ORDER BY id DESC LIMIT 1")

    # --- schedule -------------------------------------------------------------
    def tick(self, now):
        if self.pending and now >= self.pending["deadline"]:
            self._finish(self.pending["id"], "timeout", now,
                         f"no answer in {self.cfg.scan_timeout_min} min")
        if self.pending:
            return
        today = self.local.date_of(now)
        if self.done_date == today:
            return
        due = self.local.at(today, self.cfg.scan_time)
        if now < due:
            return
        if now > due + RETRY_WINDOW:
            self.done_date = today     # the window passed; the skip row stands
            return
        why = self._skip_reason(now)
        if why:
            if why != self.skip_reason:
                self.skip_reason = why
                self.store.execute("INSERT INTO scan (requested, finished, status) VALUES (?,?,?)",
                                   (now, now, f"skipped: {why}"))
                _LOG.info("network map skipped: %s", why)
                if self.on_finished:
                    self.on_finished(None, f"skipped: {why}", now)
            return
        self.request(now)

    def _skip_reason(self, now):
        state = self.ingest.z2m_state
        if state != "online":
            return "Zigbee2MQTT offline" if state == "offline" else "Zigbee2MQTT state unknown"
        since = self.ingest.z2m_online_since
        guard = self.cfg.scan_restart_guard_min * 60
        if since is not None and now - since < guard:
            return f"Zigbee2MQTT restarted {self.local.format(since, '%H:%M')}"
        return None

    def request(self, now):
        cur = self.store.execute("INSERT INTO scan (requested, status) VALUES (?, 'running')", (now,))
        scan_id = cur.lastrowid
        txn = f"{TXN_PREFIX}{scan_id}"
        self.store.execute("UPDATE scan SET txn = ? WHERE id = ?", (txn, scan_id))
        self.pending = {"id": scan_id, "txn": txn,
                        "deadline": now + self.cfg.scan_timeout_min * 60}
        self.publish(f"{self.cfg.z2m_base_topic}/bridge/request/networkmap",
                     json.dumps({"type": "raw", "routes": True, "transaction": txn}),
                     retain=False)
        _LOG.info("network map requested (%s)", txn)
        return scan_id

    # --- response -------------------------------------------------------------
    def on_response(self, payload, now):
        if not isinstance(payload, dict):
            return None
        txn = payload.get("transaction")
        if not self.pending or txn != self.pending["txn"]:
            if isinstance(txn, str) and txn.startswith(TXN_PREFIX):
                _LOG.warning("late network map response %s ignored", txn)
            return None
        scan_id = self.pending["id"]
        if payload.get("status") != "ok":
            error = payload.get("error") or "unknown error"
            return self._finish(scan_id, f"error: {str(error)[:200]}", now)
        data = payload.get("data")
        value = data.get("value") if isinstance(data, dict) else None
        if not isinstance(value, dict) or not isinstance(value.get("nodes"), list) \
                or not isinstance(value.get("links"), list):
            return self._finish(scan_id, "error: response without nodes and links", now)
        failed = self._store_topology(scan_id, value)
        detail = f"partial: {', '.join(failed)} did not answer" if failed else None
        return self._finish(scan_id, "ok", now, detail)

    def _finish(self, scan_id, status, now, detail=None):
        self.store.execute("UPDATE scan SET finished = ?, status = ?, detail = ? WHERE id = ?",
                           (now, status, detail, scan_id))
        self.pending = None
        self.done_date = self.local.date_of(now)
        self.skip_reason = None
        _LOG.info("network map %s%s", status, f" ({detail})" if detail else "")
        if self.on_finished:
            self.on_finished(scan_id, status, now)
        return status

    def _store_topology(self, scan_id, value):
        failed_names = []
        by_ieee = self.inventory.by_ieee
        with self.store.transaction() as db:
            for node in value["nodes"]:
                if not isinstance(node, dict) or not isinstance(node.get("ieeeAddr"), str):
                    continue
                dev = by_ieee.get(node["ieeeAddr"])
                failed = node.get("failed") if isinstance(node.get("failed"), list) else []
                last_seen = node.get("lastSeen")
                last_seen = int(last_seen / 1000) if isinstance(last_seen, (int, float)) else None
                db.execute("INSERT INTO scan_node VALUES (?,?,?,?,?,?,?,?)",
                           (scan_id, dev.id if dev else None, node["ieeeAddr"],
                            node.get("friendlyName"), node.get("type"),
                            node.get("networkAddress"), ",".join(failed) or None, last_seen))
                if failed:
                    failed_names.append(node.get("friendlyName") or node["ieeeAddr"])
                if dev and last_seen:
                    self.ingest.seen_in_scan(dev.id, last_seen)
            for link in value["links"]:
                if not isinstance(link, dict):
                    continue
                src = (link.get("source") or {}).get("ieeeAddr") or link.get("sourceIeeeAddr")
                tgt = (link.get("target") or {}).get("ieeeAddr") or link.get("targetIeeeAddr")
                if not isinstance(src, str) or not isinstance(tgt, str):
                    continue
                lqi = link.get("lqi", link.get("linkquality"))
                routes = link.get("routes") if isinstance(link.get("routes"), list) else []
                s, t = by_ieee.get(src), by_ieee.get(tgt)
                db.execute("INSERT INTO scan_link VALUES (?,?,?,?,?,?,?,?,?)",
                           (scan_id, src, tgt, s.id if s else None, t.id if t else None,
                            lqi, link.get("depth"), link.get("relationship"),
                            json.dumps(routes) if routes else None))
            for device_id, parent_id, how in derive_parents(db, scan_id):
                db.execute("INSERT INTO parent VALUES (?,?,?,?)", (scan_id, device_id, parent_id, how))
        return failed_names


def derive_parents(db, scan_id):
    """Each device's parent in one scan, as (device_id, parent_id, how).

    - An end device's parent is the router or coordinator that lists it with
      relationship 1 (child). Listed by two: `ambiguous`, no parent.
    - A router has no parent once joined. Its relationship-0 neighbour if one
      is listed (`parent_entry`), else the next hop of its route to the
      coordinator, network address 0 (`route`), else none.
    """
    nodes = db.execute("SELECT device_id, ieee, type FROM scan_node WHERE scan_id = ?"
                       " AND device_id IS NOT NULL", (scan_id,)).fetchall()
    links = db.execute("SELECT * FROM scan_link WHERE scan_id = ?", (scan_id,)).fetchall()
    for node in nodes:
        if node["type"] == "Coordinator":
            continue
        dev, ieee = node["device_id"], node["ieee"]
        if node["type"] == "EndDevice":
            parents = {l["target_id"] for l in links
                       if l["source_ieee"] == ieee and l["relationship"] == 1 and l["target_id"]}
            if len(parents) == 1:
                yield dev, parents.pop(), "child_of"
            else:
                yield dev, None, "ambiguous" if parents else "none"
            continue
        parents = {l["source_id"] for l in links
                   if l["target_ieee"] == ieee and l["relationship"] == 0 and l["source_id"]}
        if len(parents) == 1:
            yield dev, parents.pop(), "parent_entry"
            continue
        if len(parents) > 1:
            yield dev, None, "ambiguous"
            continue
        hops = set()
        for l in links:
            if l["target_ieee"] != ieee or not l["routes"] or not l["source_id"]:
                continue
            for entry in json.loads(l["routes"]):
                if isinstance(entry, dict) and entry.get("destinationAddress") == 0:
                    hops.add(l["source_id"])
        if len(hops) == 1:
            yield dev, hops.pop(), "route"
        else:
            yield dev, None, "ambiguous" if hops else "none"

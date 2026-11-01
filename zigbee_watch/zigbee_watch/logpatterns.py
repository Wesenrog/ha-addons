"""Every log text Zigbee Watch recognises, in one table.

Each pattern was read in the source of the versions running in the house:
Zigbee2MQTT 2.14.2 (`z2m:` below) and the zigbee-herdsman 10.10.0 it depends
on (`zh:`), paths inside each npm package's `dist/`. A new kind is added here,
from a sample in the `log_unmatched` table, with its source line.

Zigbee2MQTT publishes `bridge/logging` as {"message", "level"}, the message
prefixed with the logger namespace ("zh:ember:ezsp: ...", z2m:util/logger.js:191),
and only for lines that pass its log level (same lines). It also skips a
payload identical to the one before it (z2m:extension/bridge.js:101-106), so
counts are lower bounds.

`who` says how the device is named in the text:
  nwk  - a decimal 16-bit network address
  name - a Zigbee2MQTT friendly name
  None - no device (broadcast, group)
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Pattern:
    kind: str
    level: str
    regex: re.Pattern
    who: str | None
    source: str
    # Optional refinement of kind from the matched groups.
    refine: object = None


def _route_kind(m):
    # EmberStackError, zh:adapter/ember/enums.js:513-532. ROUTE_ERROR_* are
    # route errors proper; NETWORK_STATUS_* are other network status commands
    # arriving through the same handler.
    return "route_error" if m["detail"].startswith("ROUTE_ERROR_") else "network_status"


def _cause_kind(m):
    # The cause inside a failed request, from the ember waitress:
    #   "Delivery failed for '<nwk>'."  zh:adapter/ember/adapter/oneWaitress.js:62
    #   "... timed out after <n>ms"     zh:adapter/ember/adapter/oneWaitress.js:144, 210
    cause = m["detail"]
    if "Delivery failed for" in cause:
        return "delivery_failed"
    if "timed out after" in cause:
        return "timeout"
    return None


PATTERNS = [
    Pattern(
        kind="route_error", level="info",
        regex=re.compile(r'Received network/route error (?P<detail>\w+) for "(?P<who>\d+)"\.'),
        who="nwk", refine=_route_kind,
        source="zh:adapter/ember/ezsp/ezsp.js:4303 (ezspIncomingNetworkStatusHandler)"),
    Pattern(
        kind="delivery_failed_broadcast", level="error",
        regex=re.compile(r"Delivery of (?P<detail>\w+) failed for '(?P<who>\d+)'\."),
        # Logged only for BROADCAST/MULTICAST types; the number is a group or
        # broadcast address, not a device. Unicast failures reject the request
        # instead and surface in the Publish/ping lines below.
        who=None,
        source="zh:adapter/ember/adapter/emberAdapter.js:326"),
    Pattern(
        kind="publish_failed", level="error",
        regex=re.compile(r"Publish '\w+' '[^']*' to '(?P<who>.+?)' failed: '(?P<detail>.*)'$", re.S),
        who="name", refine=_cause_kind,
        source="z2m:extension/publish.js:286"),
    Pattern(
        kind="ping_failed", level="warning",
        regex=re.compile(r"Failed to ping '(?P<who>.+?)' \(attempt \d+/\d+, (?P<detail>.*)\)$", re.S),
        who="name",
        source="z2m:extension/availability.js:174"),
    Pattern(
        kind="read_failed", level="error",
        regex=re.compile(r"Failed to read state of '(?P<who>.+?)' after reconnect \((?P<detail>.*)\)$", re.S),
        who="name", refine=_cause_kind,
        source="z2m:extension/availability.js:325"),
    Pattern(
        kind="scan_lqi_failed", level="error",
        regex=re.compile(r"Failed to execute LQI for '(?P<who>.+?)'$"),
        who="name",
        source="z2m:extension/networkMap.js:225"),
    Pattern(
        kind="scan_routes_failed", level="error",
        regex=re.compile(r"Failed to execute routing table for '(?P<who>.+?)'$"),
        who="name",
        source="z2m:extension/networkMap.js:239"),
]

# Kinds caused by our own nightly scan; not held against a device's baseline.
SCAN_KINDS = {"scan_lqi_failed", "scan_routes_failed"}

_NAMESPACE = re.compile(r"^(?P<ns>[a-z0-9]+(?::[A-Za-z0-9_-]+)*): (?P<text>.*)$", re.S)


def split_namespace(message):
    m = _NAMESPACE.match(message)
    return (m["ns"], m["text"]) if m else (None, message)


def classify(message, level):
    """Return (kind, who_type, who, detail) for a known line, else None."""
    _, text = split_namespace(message)
    for p in PATTERNS:
        m = p.regex.search(text)
        if not m:
            continue
        kind = p.kind
        if p.refine:
            kind = p.refine(m) or kind
        groups = m.groupdict()
        return kind, p.who, groups.get("who"), groups.get("detail")
    return None

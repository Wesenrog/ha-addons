"""Local-time helpers. Storage is UTC epoch seconds; days, the scan schedule
and the report are in local time (Europe/Oslo by default)."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


class LocalTime:
    def __init__(self, name):
        self.tz = ZoneInfo(name)

    def date_of(self, ts):
        return datetime.fromtimestamp(ts, self.tz).date()

    def at(self, day, hhmm):
        """Epoch seconds of a local wall-clock time on a day. A time that does
        not exist (the spring-forward hour) lands just after the gap; a time
        that occurs twice (autumn) is the first one."""
        h, m = (int(x) for x in hhmm.split(":"))
        local = datetime.combine(day, time(h, m), self.tz)
        return int(local.timestamp())

    def day_bounds(self, day):
        """[start, end) of a local day in epoch seconds (23 or 25 h at DST)."""
        return self.at(day, "00:00"), self.at(day + timedelta(days=1), "00:00")

    def format(self, ts, fmt="%Y-%m-%d %H:%M"):
        return datetime.fromtimestamp(ts, self.tz).strftime(fmt)


def overlap(intervals, start, end):
    """Seconds of [start, end) covered by a list of (s, e) intervals, which may
    overlap each other."""
    spans = sorted((max(s, start), min(e, end)) for s, e in intervals if e > start and s < end)
    total, cur_s, cur_e = 0, None, None
    for s, e in spans:
        if e <= s:
            continue
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def intersect(a, b):
    """Pairwise intersection of two interval lists."""
    out = []
    for s1, e1 in a:
        for s2, e2 in b:
            s, e = max(s1, s2), min(e1, e2)
            if e > s:
                out.append((s, e))
    return out


__all__ = ["LocalTime", "overlap", "intersect", "date", "timedelta"]

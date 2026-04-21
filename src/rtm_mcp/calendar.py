"""ICS calendar parsing and capacity computation.

Fetches a freebusy ICS feed, extracts busy intervals, and computes
available working capacity per day.
"""

from __future__ import annotations

import os
import re
import urllib.request
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import DayCapacity, FreeBlock

# ---------------------------------------------------------------------------
# Configuration (from env vars with sensible defaults)
# ---------------------------------------------------------------------------

def _tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("RTM_TZ", "Europe/Zurich"))


def _meeting_buffer_min() -> int:
    return int(os.environ.get("RTM_MEETING_BUFFER_MIN", "10"))


def _focus_block_min() -> int:
    return int(os.environ.get("RTM_FOCUS_BLOCK_MIN", "30"))


def _work_windows_spec() -> str:
    return os.environ.get("RTM_WORK_WINDOWS", "09:00-12:00,13:30-18:00")


def _calendar_url() -> str:
    return os.environ.get("RTM_CALENDAR_ICS_URL", "").strip()


# ---------------------------------------------------------------------------
# Public helpers (used by planner and server)
# ---------------------------------------------------------------------------

def get_tz() -> ZoneInfo:
    """Return the configured timezone."""
    return _tz()


def get_work_windows_spec() -> str:
    """Return the raw work windows spec string."""
    return _work_windows_spec()


def get_meeting_buffer() -> int:
    """Return meeting buffer in minutes."""
    return _meeting_buffer_min()


def get_focus_block() -> int:
    """Return minimum focus block in minutes."""
    return _focus_block_min()


# ---------------------------------------------------------------------------
# ICS parsing
# ---------------------------------------------------------------------------

def _read_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "rtm-mcp/1.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")


def _unfold_ics_lines(text: str) -> list[str]:
    """Handle RFC 5545 line folding (continuation lines start with space/tab)."""
    lines = text.splitlines()
    out: list[str] = []
    for ln in lines:
        if ln.startswith((" ", "\t")) and out:
            out[-1] += ln[1:]
        else:
            out.append(ln.rstrip("\r"))
    return out


def _parse_ics_dt(value: str, tzid: str | None) -> datetime:
    """Parse an ICS datetime value into an aware datetime in local tz."""
    tz = _tz()
    value = value.strip()

    # Date only: YYYYMMDD
    if re.fullmatch(r"\d{8}", value):
        d = date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
        return datetime.combine(d, time(0, 0), tzinfo=tz)

    # UTC: YYYYMMDDTHHMMSSZ
    if value.endswith("Z"):
        dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(tz)

    # Local with optional TZID
    dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
    if tzid:
        return dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(tz)
    return dt.replace(tzinfo=tz)


def _extract_busy_intervals(ics_text: str) -> list[tuple[datetime, datetime]]:
    """Extract VEVENT intervals from ICS text."""
    lines = _unfold_ics_lines(ics_text)
    in_event = False
    ev: dict[str, str] = {}
    intervals: list[tuple[datetime, datetime]] = []

    def flush(e: dict[str, str]) -> None:
        dtstart_raw = e.get("DTSTART")
        dtend_raw = e.get("DTEND")
        if not dtstart_raw or not dtend_raw:
            return
        tzid = e.get("DTSTART_TZID") or e.get("DTEND_TZID")
        try:
            start = _parse_ics_dt(dtstart_raw, tzid)
            end = _parse_ics_dt(dtend_raw, tzid)
        except Exception:
            return
        if end > start:
            intervals.append((start, end))

    for ln in lines:
        if ln == "BEGIN:VEVENT":
            in_event = True
            ev = {}
            continue
        if ln == "END:VEVENT":
            flush(ev)
            in_event = False
            ev = {}
            continue
        if not in_event:
            continue

        if ln.startswith("DTSTART"):
            k, v = ln.split(":", 1)
            ev["DTSTART"] = v
            m = re.search(r"TZID=([^;:]+)", k)
            if m:
                ev["DTSTART_TZID"] = m.group(1)
        elif ln.startswith("DTEND"):
            k, v = ln.split(":", 1)
            ev["DTEND"] = v
            m = re.search(r"TZID=([^;:]+)", k)
            if m:
                ev["DTEND_TZID"] = m.group(1)

    return intervals


# ---------------------------------------------------------------------------
# Interval arithmetic
# ---------------------------------------------------------------------------

def merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Merge overlapping intervals."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ps, pe = merged[-1]
        if s <= pe:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def _subtract_intervals(
    base: list[tuple[datetime, datetime]],
    busy: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Subtract busy intervals from base intervals."""
    out: list[tuple[datetime, datetime]] = []
    busy = merge_intervals(busy)
    for bs, be in base:
        cur_s, cur_e = bs, be
        for s, e in busy:
            if e <= cur_s or s >= cur_e:
                continue
            if s <= cur_s and e >= cur_e:
                cur_s = cur_e
                break
            if s <= cur_s < e < cur_e:
                cur_s = e
                continue
            if cur_s < s < cur_e <= e:
                cur_e = s
                break
            if cur_s < s and e < cur_e:
                out.append((cur_s, s))
                cur_s = e
        if cur_s < cur_e:
            out.append((cur_s, cur_e))
    return out


# ---------------------------------------------------------------------------
# Work windows & capacity
# ---------------------------------------------------------------------------

def _parse_work_windows(spec: str) -> list[tuple[time, time]]:
    out: list[tuple[time, time]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, b = part.split("-", 1)
        sh, sm = map(int, a.split(":"))
        eh, em = map(int, b.split(":"))
        out.append((time(sh, sm), time(eh, em)))
    return out


def _day_work_windows(d: date) -> list[tuple[datetime, datetime]]:
    tz = _tz()
    windows: list[tuple[datetime, datetime]] = []
    for a, b in _parse_work_windows(_work_windows_spec()):
        windows.append((
            datetime.combine(d, a, tzinfo=tz),
            datetime.combine(d, b, tzinfo=tz),
        ))
    return windows


def capacity_for_day(
    d: date,
    busy: list[tuple[datetime, datetime]],
) -> DayCapacity:
    """Compute free working capacity for a single day."""
    tz = _tz()
    day_start = datetime.combine(d, time(0, 0), tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    buf = timedelta(minutes=_meeting_buffer_min())
    buffered: list[tuple[datetime, datetime]] = []
    meeting_count = 0
    for s, e in busy:
        if e <= day_start or s >= day_end:
            continue
        meeting_count += 1
        ss = max(day_start, s - buf)
        ee = min(day_end, e + buf)
        buffered.append((ss, ee))

    base = _day_work_windows(d)
    free = _subtract_intervals(base, buffered)

    # Enforce minimum focus block
    min_block = timedelta(minutes=_focus_block_min())
    free = [(s, e) for (s, e) in free if (e - s) >= min_block]

    free_min = int(sum((e - s).total_seconds() // 60 for s, e in free))
    blocks = [FreeBlock(start=s, end=e) for s, e in free]

    return DayCapacity(
        day=d,
        free_minutes=free_min,
        meeting_count=meeting_count,
        free_blocks=blocks,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def fetch_busy_intervals() -> list[tuple[datetime, datetime]]:
    """Fetch and merge busy intervals from the configured ICS calendar feed."""
    url = _calendar_url()
    if not url:
        raise RuntimeError(
            "No calendar URL configured. Set RTM_CALENDAR_ICS_URL."
        )
    ics_text = _read_url(url)
    return merge_intervals(_extract_busy_intervals(ics_text))


def compute_capacity(days: int) -> list[DayCapacity]:
    """Compute capacity for the next N working days (skipping weekends)."""
    busy = fetch_busy_intervals()
    tz = _tz()
    d = datetime.now(tz).date()
    result: list[DayCapacity] = []
    while len(result) < days:
        if d.weekday() < 5:  # skip weekends
            result.append(capacity_for_day(d, busy))
        d += timedelta(days=1)
    return result

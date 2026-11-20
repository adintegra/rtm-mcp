from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from rtm_mcp import calendar as cal

UTC = ZoneInfo("UTC")
DAY = date(2026, 3, 2)  # a Monday


def dt(h: int, m: int = 0) -> datetime:
    return datetime.combine(DAY, time(h, m), tzinfo=UTC)


class TestUnfold:
    def test_joins_continuation_lines(self):
        text = "SUMMARY:Hello\r\n  World\r\nDTSTART:20260302T090000Z"
        assert cal._unfold_ics_lines(text) == [
            "SUMMARY:Hello World",
            "DTSTART:20260302T090000Z",
        ]

    def test_tab_continuation(self):
        assert cal._unfold_ics_lines("A:1\n\t2") == ["A:12"]


class TestParseIcsDt:
    def test_utc_converted_to_local(self, monkeypatch):
        monkeypatch.setenv("RTM_TZ", "Europe/Zurich")
        result = cal._parse_ics_dt("20260302T090000Z", None)
        assert result.hour == 10  # CET = UTC+1 in March

    def test_floating_uses_local_tz(self):
        result = cal._parse_ics_dt("20260302T090000", None)
        assert result == dt(9)

    def test_tzid(self):
        result = cal._parse_ics_dt("20260302T090000", "America/New_York")
        assert result == datetime(2026, 3, 2, 14, tzinfo=UTC)

    def test_date_only(self):
        assert cal._parse_ics_dt("20260302", None) == dt(0)


class TestExtractBusyIntervals:
    def test_basic_event(self):
        ics = (
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
            "DTSTART:20260302T100000Z\nDTEND:20260302T110000Z\n"
            "END:VEVENT\nEND:VCALENDAR\n"
        )
        assert cal._extract_busy_intervals(ics) == [(dt(10), dt(11))]

    def test_tzid_params(self):
        ics = (
            "BEGIN:VEVENT\n"
            "DTSTART;TZID=America/New_York:20260302T090000\n"
            "DTEND;TZID=America/New_York:20260302T100000\n"
            "END:VEVENT\n"
        )
        assert cal._extract_busy_intervals(ics) == [(dt(14), dt(15))]

    def test_skips_incomplete_and_invalid_events(self):
        ics = (
            "BEGIN:VEVENT\nDTSTART:20260302T100000Z\nEND:VEVENT\n"
            "BEGIN:VEVENT\nDTSTART:garbage\nDTEND:garbage\nEND:VEVENT\n"
            "BEGIN:VEVENT\nDTSTART:20260302T110000Z\nDTEND:20260302T100000Z\nEND:VEVENT\n"
        )
        assert cal._extract_busy_intervals(ics) == []

    def test_ignores_content_outside_events(self):
        ics = "DTSTART:20260302T100000Z\nDTEND:20260302T110000Z\n"
        assert cal._extract_busy_intervals(ics) == []


class TestMergeIntervals:
    def test_empty(self):
        assert cal.merge_intervals([]) == []

    def test_merges_overlapping_and_touching(self):
        result = cal.merge_intervals([(dt(11), dt(12)), (dt(9), dt(10)), (dt(10), dt(11, 30))])
        assert result == [(dt(9), dt(12))]

    def test_keeps_disjoint(self):
        result = cal.merge_intervals([(dt(9), dt(10)), (dt(11), dt(12))])
        assert result == [(dt(9), dt(10)), (dt(11), dt(12))]


class TestSubtractIntervals:
    base = [(dt(9), dt(12))]

    def test_no_overlap(self):
        assert cal._subtract_intervals(self.base, [(dt(13), dt(14))]) == self.base

    def test_fully_covered(self):
        assert cal._subtract_intervals(self.base, [(dt(8), dt(13))]) == []

    def test_trims_start(self):
        assert cal._subtract_intervals(self.base, [(dt(8), dt(10))]) == [(dt(10), dt(12))]

    def test_trims_end(self):
        assert cal._subtract_intervals(self.base, [(dt(11), dt(13))]) == [(dt(9), dt(11))]

    def test_splits_in_middle(self):
        result = cal._subtract_intervals(self.base, [(dt(10), dt(11))])
        assert result == [(dt(9), dt(10)), (dt(11), dt(12))]

    def test_multiple_busy(self):
        result = cal._subtract_intervals(self.base, [(dt(9, 30), dt(10)), (dt(10, 30), dt(11))])
        assert result == [(dt(9), dt(9, 30)), (dt(10), dt(10, 30)), (dt(11), dt(12))]


class TestWorkWindows:
    def test_parse(self):
        assert cal._parse_work_windows("09:00-12:00, 13:30-18:00") == [
            (time(9), time(12)),
            (time(13, 30), time(18)),
        ]

    def test_parse_ignores_empty_parts(self):
        assert cal._parse_work_windows("09:00-12:00,,") == [(time(9), time(12))]

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            cal._parse_work_windows("9-12")


class TestCapacityForDay:
    def test_no_meetings(self):
        cap = cal.capacity_for_day(DAY, [])
        assert cap.free_minutes == 7 * 60
        assert cap.meeting_count == 0
        assert len(cap.free_blocks) == 2

    def test_meeting_with_buffer(self, monkeypatch):
        monkeypatch.setenv("RTM_MEETING_BUFFER_MIN", "10")
        cap = cal.capacity_for_day(DAY, [(dt(10), dt(11))])
        # 09:00-09:50 and 11:10-12:00 and 13:00-17:00
        assert cap.meeting_count == 1
        assert cap.free_minutes == 50 + 50 + 240

    def test_short_gaps_dropped(self, monkeypatch):
        monkeypatch.setenv("RTM_MEETING_BUFFER_MIN", "0")
        monkeypatch.setenv("RTM_FOCUS_BLOCK_MIN", "30")
        # leaves 09:00-09:15 (too short) and 09:45-12:00
        cap = cal.capacity_for_day(DAY, [(dt(9, 15), dt(9, 45))])
        assert [b.minutes for b in cap.free_blocks] == [135, 240]

    def test_meeting_on_other_day_ignored(self):
        other = datetime(2026, 3, 3, 10, tzinfo=UTC)
        cap = cal.capacity_for_day(DAY, [(other, other.replace(hour=11))])
        assert cap.meeting_count == 0
        assert cap.free_minutes == 7 * 60

    def test_fully_booked(self, monkeypatch):
        monkeypatch.setenv("RTM_MEETING_BUFFER_MIN", "0")
        cap = cal.capacity_for_day(DAY, [(dt(8), dt(18))])
        assert cap.free_minutes == 0
        assert cap.free_blocks == []


class TestFetchBusyIntervals:
    def test_requires_url(self):
        with pytest.raises(RuntimeError, match="RTM_CALENDAR_ICS_URL"):
            cal.fetch_busy_intervals()

    def test_fetches_and_merges(self, monkeypatch):
        monkeypatch.setenv("RTM_CALENDAR_ICS_URL", "https://example.test/cal.ics")
        ics = (
            "BEGIN:VEVENT\nDTSTART:20260302T100000Z\nDTEND:20260302T110000Z\nEND:VEVENT\n"
            "BEGIN:VEVENT\nDTSTART:20260302T103000Z\nDTEND:20260302T120000Z\nEND:VEVENT\n"
        )
        monkeypatch.setattr(cal, "_read_url", lambda url: ics)
        assert cal.fetch_busy_intervals() == [(dt(10), dt(12))]

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from rtm_mcp import planner
from rtm_mcp.models import DayCapacity, FreeBlock, RTMTask

MONDAY = date(2026, 3, 2)


def task(
    name="t",
    priority="N",
    est=60,
    due="2026-03-02T00:00:00Z",
    created="2026-01-01T00:00:00Z",
    tags=(),
    task_id=None,
) -> RTMTask:
    return RTMTask(
        name=name,
        priority=priority,
        estimate_min=est,
        due_iso=due,
        created_iso=created,
        tags=frozenset(tags),
        list_id="L",
        taskseries_id="S",
        task_id=task_id or name,
    )


class TestDateHelpers:
    def test_due_empty(self):
        assert planner._rtm_due_local_date("") is None

    def test_due_utc_converted_to_local(self, monkeypatch):
        monkeypatch.setenv("RTM_TZ", "Pacific/Auckland")  # UTC+13 in March
        assert planner._rtm_due_local_date("2026-03-01T23:00:00Z") == date(2026, 3, 2)

    def test_due_date_only_fallback(self):
        assert planner._rtm_due_local_date("2026-03-02") == date(2026, 3, 2)

    def test_due_garbage(self):
        assert planner._rtm_due_local_date("nonsense") is None

    def test_created_missing_sorts_last(self):
        assert planner._rtm_created_dt("") > planner._rtm_created_dt("2026-01-01T00:00:00Z")

    def test_created_naive_gets_local_tz(self):
        assert planner._rtm_created_dt("2026-01-01T00:00:00").tzinfo is not None


class TestSortPool:
    def test_priority_then_age_then_estimate_then_name(self):
        tasks = [
            task("none", priority="N"),
            task("p2", priority="2"),
            task("p1-new", priority="1", created="2026-02-01T00:00:00Z"),
            task("p1-old-long", priority="1", created="2026-01-01T00:00:00Z", est=120),
            task("p1-old-short", priority="1", created="2026-01-01T00:00:00Z", est=15),
        ]
        assert [t.name for t in planner._sort_pool(tasks)] == [
            "p1-old-short",
            "p1-old-long",
            "p1-new",
            "p2",
            "none",
        ]


@pytest.fixture
def fake_world(monkeypatch):
    """Patch I/O so plan_tasks is deterministic: 120 free minutes on every weekday."""
    tz = ZoneInfo("UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz_=None):
            return datetime(2026, 3, 2, 8, tzinfo=tz)  # Monday

    def make(tasks, free=120):
        monkeypatch.setattr(planner, "datetime", FrozenDatetime)
        monkeypatch.setattr(planner, "fetch_busy_intervals", lambda: [])
        monkeypatch.setattr(planner, "fetch_tasks", lambda f: tasks)
        monkeypatch.setattr(
            planner,
            "capacity_for_day",
            lambda d, busy: DayCapacity(
                day=d,
                free_minutes=free,
                meeting_count=0,
                free_blocks=[
                    FreeBlock(
                        datetime(d.year, d.month, d.day, 9, tzinfo=tz),
                        datetime(d.year, d.month, d.day, 11, tzinfo=tz),
                    )
                ],
            ),
        )

    return make


class TestPlanTasks:
    def test_no_dated_tasks(self, fake_world):
        fake_world([task(due="")])
        result = planner.plan_tasks(3)
        assert result.days == [] and result.unscheduled == []

    def test_fills_days_by_priority(self, fake_world):
        fake_world(
            [
                task("low", priority="3", est=60),
                task("high", priority="1", est=60),
                task("mid", priority="2", est=60),
            ]
        )
        result = planner.plan_tasks(2)
        day1, day2 = result.days
        assert [a.task.name for a in day1.assigned_tasks] == ["high", "mid"]
        assert [a.task.name for a in day2.assigned_tasks] == ["low"]
        assert day1.leftover_minutes == 0
        assert day2.leftover_minutes == 60

    def test_skips_weekends(self, fake_world):
        fake_world([task()])
        result = planner.plan_tasks(6)
        assert [d.day.weekday() for d in result.days] == [0, 1, 2, 3, 4, 0]

    def test_nomove_reduces_capacity_and_is_not_reassigned(self, fake_world):
        fixed = task("fixed", est=90, tags={"nomove"}, due="2026-03-02T00:00:00Z")
        movable = task("movable", est=60)
        fake_world([fixed, movable])
        result = planner.plan_tasks(2)
        day1, day2 = result.days
        assert day1.fixed_tasks == [fixed]
        assert day1.fixed_minutes == 90
        assert day1.assigned_tasks == []  # only 30 min left, movable needs 60
        assert [a.task for a in day2.assigned_tasks] == [movable]

    def test_unscheduled_when_too_big(self, fake_world):
        fake_world([task("huge", est=500)])
        result = planner.plan_tasks(2)
        assert [t.name for t in result.unscheduled] == ["huge"]
        assert result.unscheduled_total_minutes == 500

    def test_small_task_backfills_after_large_skipped(self, fake_world):
        fake_world(
            [
                task("big", priority="1", est=100),
                task("big2", priority="2", est=100),
                task("small", priority="3", est=20),
            ]
        )
        day1 = planner.plan_tasks(1).days[0]
        assert [a.task.name for a in day1.assigned_tasks] == ["big", "small"]

    def test_delta_days(self, fake_world):
        fake_world([task(due="2026-03-02T00:00:00Z")])
        [assignment] = planner.plan_tasks(1).days[0].assigned_tasks
        assert assignment.original_due == MONDAY
        assert assignment.delta_days == 0


class TestApplyPlan:
    def test_only_writes_changed_due_dates(self, fake_world, monkeypatch):
        unchanged = task("same", priority="1", est=120, due="2026-03-02T00:00:00Z")
        moved = task("moved", priority="2", est=60, due="2026-03-02T00:00:00Z")
        fake_world([unchanged, moved])
        monkeypatch.setattr(planner, "timeline_create", lambda: "TL")
        writes = []
        monkeypatch.setattr(
            planner, "task_set_due_date", lambda tl, t, d: writes.append((tl, t.name, d))
        )

        changes = planner.apply_plan(2)

        assert writes == [("TL", "moved", date(2026, 3, 3))]
        assert len(changes) == 1
        assert changes[0].task_name == "moved"
        assert changes[0].old_due == MONDAY
        assert changes[0].delta_days == 1

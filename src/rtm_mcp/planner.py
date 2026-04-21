"""Task scheduling / planning algorithm.

Takes RTM tasks and calendar busy intervals, produces a PlanResult
mapping tasks into available working slots over the next N working days.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .calendar import (
    capacity_for_day,
    fetch_busy_intervals,
    get_tz,
)
from .models import (
    ChangeRecord,
    DaySummary,
    PlanAssignment,
    PlanResult,
    RTMTask,
)
from .rtm_client import (
    fetch_tasks,
    task_set_due_date,
    timeline_create,
)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _rtm_due_local_date(due_iso: str) -> date | None:
    """Convert an RTM due date (UTC ISO string) to a local date."""
    if not due_iso:
        return None
    tz = get_tz()
    try:
        utc_dt = datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
        return utc_dt.astimezone(tz).date()
    except Exception:
        try:
            return date.fromisoformat(due_iso[:10])
        except Exception:
            return None


def _rtm_created_dt(created_iso: str) -> datetime:
    """Parse RTM taskseries.created into an aware datetime in local tz."""
    tz = get_tz()
    if not created_iso:
        return datetime.max.replace(tzinfo=tz)
    try:
        dt = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except Exception:
        return datetime.max.replace(tzinfo=tz)


def _sort_pool(tasks: list[RTMTask]) -> list[RTMTask]:
    """Sort tasks by priority, then creation date, then estimate, then name."""
    pri_rank = {"1": 0, "2": 1, "3": 2}
    return sorted(
        tasks,
        key=lambda t: (
            pri_rank.get(t.priority, 9),
            _rtm_created_dt(t.created_iso),
            t.estimate_min,
            t.name.lower(),
        ),
    )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def plan_tasks(days: int = 5) -> PlanResult:
    """Plan tasks into available calendar slots.

    Only tasks with an existing due date and a time estimate are scheduled.
    Tasks tagged 'nomove' remain on their current day (fixed).

    Returns a PlanResult with per-day summaries and unscheduled tasks.
    """
    busy = fetch_busy_intervals()
    all_tasks = fetch_tasks("status:incomplete")

    # Only tasks with existing due dates enter the pool
    pool = [t for t in all_tasks if _rtm_due_local_date(t.due_iso) is not None]
    if not pool:
        return PlanResult(days=[], unscheduled=[], unscheduled_total_minutes=0)

    pool = _sort_pool(pool)

    fixed = [t for t in pool if "nomove" in t.tags]
    remaining = [t for t in pool if "nomove" not in t.tags]

    tz = get_tz()
    d = datetime.now(tz).date()
    day_summaries: list[DaySummary] = []
    planned_count = 0

    while planned_count < days:
        if d.weekday() >= 5:  # skip weekends
            d += timedelta(days=1)
            continue

        cap = capacity_for_day(d, busy)
        fixed_today = [t for t in fixed if _rtm_due_local_date(t.due_iso) == d]
        fixed_used = sum(t.estimate_min for t in fixed_today)
        available = max(0, cap.free_minutes - fixed_used)

        day_tasks: list[PlanAssignment] = []
        used = 0
        i = 0
        while i < len(remaining):
            t = remaining[i]
            if t.estimate_min <= (available - used):
                original = _rtm_due_local_date(t.due_iso)
                delta = (d - original).days if original else None
                day_tasks.append(PlanAssignment(
                    task=t,
                    assigned_date=d,
                    original_due=original,
                    delta_days=delta,
                ))
                used += t.estimate_min
                remaining.pop(i)
            else:
                i += 1

        day_summaries.append(DaySummary(
            day=d,
            meeting_count=cap.meeting_count,
            free_minutes=cap.free_minutes,
            fixed_minutes=fixed_used,
            planned_minutes=used,
            leftover_minutes=cap.free_minutes - fixed_used - used,
            free_blocks=cap.free_blocks,
            fixed_tasks=fixed_today,
            assigned_tasks=day_tasks,
        ))

        planned_count += 1
        d += timedelta(days=1)

    unscheduled_min = sum(t.estimate_min for t in remaining)
    return PlanResult(
        days=day_summaries,
        unscheduled=remaining,
        unscheduled_total_minutes=unscheduled_min,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_plan(days: int = 5) -> list[ChangeRecord]:
    """Run the planner and write new due dates back to RTM.

    Returns a list of ChangeRecords for tasks whose due date was changed.
    """
    result = plan_tasks(days)
    timeline = timeline_create()

    changes: list[ChangeRecord] = []
    for day_summary in result.days:
        for assignment in day_summary.assigned_tasks:
            task = assignment.task
            old_due = _rtm_due_local_date(task.due_iso)
            new_due = assignment.assigned_date
            if old_due == new_due:
                continue  # no change needed
            task_set_due_date(timeline, task, new_due)
            delta = (new_due - old_due).days if old_due else 0
            changes.append(ChangeRecord(
                task_name=task.name,
                old_due=old_due,
                new_due=new_due,
                delta_days=delta,
            ))

    return changes

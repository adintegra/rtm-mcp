"""Data models for RTM MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time


@dataclass(frozen=True)
class RTMTask:
    """A single incomplete RTM task with an estimate."""

    name: str
    priority: str  # "1", "2", "3", or "N"
    estimate_min: int
    due_iso: str  # raw ISO string from RTM (UTC)
    created_iso: str
    tags: frozenset[str]
    list_id: str
    taskseries_id: str
    task_id: str


@dataclass(frozen=True)
class FreeBlock:
    """A contiguous block of free working time."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class DayCapacity:
    """Calendar capacity summary for a single working day."""

    day: date
    free_minutes: int
    meeting_count: int
    free_blocks: list[FreeBlock]


@dataclass(frozen=True)
class PlanAssignment:
    """A task assigned to a specific day in the plan."""

    task: RTMTask
    assigned_date: date
    original_due: date | None
    delta_days: int | None  # positive = pushed later, negative = pulled earlier


@dataclass
class DaySummary:
    """Summary of one day in the plan output."""

    day: date
    meeting_count: int
    free_minutes: int
    fixed_minutes: int  # nomove tasks
    planned_minutes: int
    leftover_minutes: int
    free_blocks: list[FreeBlock]
    fixed_tasks: list[RTMTask]
    assigned_tasks: list[PlanAssignment]


@dataclass
class PlanResult:
    """Full result of a planning run."""

    days: list[DaySummary]
    unscheduled: list[RTMTask]
    unscheduled_total_minutes: int


@dataclass
class ChangeRecord:
    """Record of a single due-date change applied to RTM."""

    task_name: str
    old_due: date | None
    new_due: date
    delta_days: int

"""RTM MCP Server.

Exposes Remember The Milk task planning tools via the Model Context Protocol.

Tools:
  list_tasks  - List incomplete RTM tasks (with filter options)
  plan        - Preview task scheduling against calendar availability
  apply       - Write rescheduled due dates to RTM
  capacity    - Show calendar availability per day
"""

from __future__ import annotations

import json
from datetime import date, datetime

from mcp.server.fastmcp import FastMCP

from .calendar import compute_capacity, get_focus_block, get_meeting_buffer, get_tz, get_work_windows_spec
from .models import ChangeRecord, DayCapacity, DaySummary, PlanResult
from .planner import apply_plan, plan_tasks
from .rtm_client import fetch_tasks_all

mcp = FastMCP(
    "rtm-planner",
    instructions=(
        "This server provides tools to interact with Remember The Milk (RTM) "
        "tasks and plan them against calendar availability. Use 'plan' to "
        "preview scheduling changes before using 'apply' to write them. "
        "The 'capacity' tool shows raw calendar availability without task data. "
        "The 'list_tasks' tool retrieves current tasks from RTM."
    ),
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_PRI_LABEL = {"1": "P1/high", "2": "P2/medium", "3": "P3/low"}


def _pri(p: str) -> str:
    return _PRI_LABEL.get(p, "none")


def _due_str(due_iso: str) -> str:
    if not due_iso:
        return "no date"
    try:
        tz = get_tz()
        utc_dt = datetime.fromisoformat(due_iso.replace("Z", "+00:00"))
        local_dt = utc_dt.astimezone(tz)
        d = local_dt.date()
        today = datetime.now(tz).date()
        if d < today:
            return f"{d.isoformat()} (OVERDUE)"
        if d == today:
            return f"{d.isoformat()} (today)"
        return d.isoformat()
    except Exception:
        return due_iso[:10] if len(due_iso) >= 10 else due_iso


def _format_capacity(caps: list[DayCapacity]) -> tuple[str, list[dict]]:
    """Format capacity data as (summary_text, json_data)."""
    tz = get_tz()
    lines = [
        f"Calendar capacity ({len(caps)} working days)",
        f"Timezone: {tz.key} | Work windows: {get_work_windows_spec()} "
        f"| Buffer: {get_meeting_buffer()}m | Min focus block: {get_focus_block()}m",
        "",
    ]
    json_days = []

    for cap in caps:
        blocks_str = ", ".join(
            f"{b.start.strftime('%H:%M')}-{b.end.strftime('%H:%M')}"
            for b in cap.free_blocks
        )
        lines.append(
            f"{cap.day.isoformat()}  "
            f"meetings: {cap.meeting_count}, "
            f"free: {cap.free_minutes}m"
        )
        if blocks_str:
            lines.append(f"  blocks: {blocks_str}")

        json_days.append({
            "date": cap.day.isoformat(),
            "free_minutes": cap.free_minutes,
            "meeting_count": cap.meeting_count,
            "free_blocks": [
                {"start": b.start.isoformat(), "end": b.end.isoformat(), "minutes": b.minutes}
                for b in cap.free_blocks
            ],
        })

    summary = "\n".join(lines)
    return summary, json_days


def _format_plan(result: PlanResult) -> tuple[str, dict]:
    """Format a PlanResult as (summary_text, json_data)."""
    tz = get_tz()
    lines = [
        f"Schedule preview ({len(result.days)} working days, no changes written)",
        f"Timezone: {tz.key} | Work windows: {get_work_windows_spec()} "
        f"| Buffer: {get_meeting_buffer()}m | Min focus block: {get_focus_block()}m",
        "",
    ]
    json_days = []

    for ds in result.days:
        lines.append(
            f"{ds.day.isoformat()}  "
            f"meetings: {ds.meeting_count}, free: {ds.free_minutes}m, "
            f"fixed(nomove): {ds.fixed_minutes}m, "
            f"planned: {ds.planned_minutes}m, "
            f"leftover: {ds.leftover_minutes}m"
        )

        blocks_str = ", ".join(
            f"{b.start.strftime('%H:%M')}-{b.end.strftime('%H:%M')}"
            for b in ds.free_blocks
        )
        if blocks_str:
            lines.append(f"  free blocks: {blocks_str}")

        day_json: dict = {
            "date": ds.day.isoformat(),
            "meeting_count": ds.meeting_count,
            "free_minutes": ds.free_minutes,
            "fixed_minutes": ds.fixed_minutes,
            "planned_minutes": ds.planned_minutes,
            "leftover_minutes": ds.leftover_minutes,
            "free_blocks": [
                {"start": b.start.isoformat(), "end": b.end.isoformat(), "minutes": b.minutes}
                for b in ds.free_blocks
            ],
            "fixed_tasks": [],
            "assigned_tasks": [],
        }

        if ds.fixed_tasks:
            lines.append("  Fixed (nomove) tasks:")
            for t in ds.fixed_tasks:
                lines.append(f"    [{_pri(t.priority)}] {t.estimate_min}m  {t.name}")
                day_json["fixed_tasks"].append({
                    "name": t.name,
                    "priority": t.priority,
                    "estimate_min": t.estimate_min,
                })

        if ds.assigned_tasks:
            lines.append("  Rescheduled tasks:")
            for a in ds.assigned_tasks:
                delta_s = ""
                if a.delta_days is not None and a.delta_days != 0:
                    sign = "+" if a.delta_days > 0 else ""
                    delta_s = f" ({sign}{a.delta_days}d)"
                elif a.delta_days == 0:
                    delta_s = " (0d)"
                old_s = a.original_due.isoformat() if a.original_due else "?"
                lines.append(
                    f"    [{_pri(a.task.priority)}] {a.task.estimate_min}m  "
                    f"{a.task.name}  "
                    f"was: {old_s} -> {a.assigned_date.isoformat()}{delta_s}"
                )
                day_json["assigned_tasks"].append({
                    "name": a.task.name,
                    "priority": a.task.priority,
                    "estimate_min": a.task.estimate_min,
                    "original_due": old_s,
                    "new_due": a.assigned_date.isoformat(),
                    "delta_days": a.delta_days,
                })

        if not ds.fixed_tasks and not ds.assigned_tasks:
            lines.append("  (no tasks fit or no capacity)")

        json_days.append(day_json)

    if result.unscheduled:
        lines.append(
            f"\nNot scheduled within range: {len(result.unscheduled)} tasks "
            f"({result.unscheduled_total_minutes}m total)"
        )

    summary = "\n".join(lines)
    json_data = {
        "days": json_days,
        "unscheduled_count": len(result.unscheduled),
        "unscheduled_total_minutes": result.unscheduled_total_minutes,
        "unscheduled": [
            {"name": t.name, "priority": t.priority, "estimate_min": t.estimate_min}
            for t in result.unscheduled
        ],
    }
    return summary, json_data


def _format_changes(changes: list[ChangeRecord]) -> tuple[str, list[dict]]:
    """Format apply results as (summary_text, json_data)."""
    if not changes:
        return "No changes were needed (all tasks already on correct dates).", []

    lines = [f"Applied {len(changes)} due-date update(s) to RTM:", ""]
    json_list = []
    for c in changes:
        old_s = c.old_due.isoformat() if c.old_due else "?"
        sign = "+" if c.delta_days > 0 else ""
        lines.append(
            f"  {c.task_name}: {old_s} -> {c.new_due.isoformat()} ({sign}{c.delta_days}d)"
        )
        json_list.append({
            "name": c.task_name,
            "old_due": old_s,
            "new_due": c.new_due.isoformat(),
            "delta_days": c.delta_days,
        })

    summary = "\n".join(lines)
    return summary, json_list


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_tasks(
    filter: str = "all",
) -> str:
    """List incomplete tasks from Remember The Milk.

    Args:
        filter: Which tasks to show. One of:
            "all"     - all incomplete tasks (default)
            "today"   - tasks due today
            "week"    - tasks due within the next week
            "overdue" - tasks past their due date
    """
    rtm_filters = {
        "all": "status:incomplete",
        "today": "due:today AND status:incomplete",
        "week": 'dueBefore:"1 week" AND status:incomplete',
        "overdue": "dueBefore:today AND status:incomplete",
    }

    if filter not in rtm_filters:
        return json.dumps({
            "error": f"Unknown filter '{filter}'. Use: all, today, week, overdue",
        })

    try:
        tasks = fetch_tasks_all(rtm_filters[filter])
    except Exception as e:
        return json.dumps({"error": str(e)})

    if not tasks:
        summary = f"No {filter} tasks found."
        return json.dumps({"summary": summary, "tasks": [], "count": 0})

    lines = [f"{len(tasks)} {filter} task(s):", ""]
    for t in tasks:
        est_s = f"{t['estimate_min']}m" if t["estimate_min"] else "no est."
        lines.append(
            f"  [{_pri(t['priority'])}] {_due_str(t['due'])}  {t['name']}  ({est_s})"
        )

    summary = "\n".join(lines)
    return json.dumps({"summary": summary, "tasks": tasks, "count": len(tasks)})


@mcp.tool()
def plan(days: int = 5) -> str:
    """Preview how RTM tasks would be scheduled against calendar availability.

    Reads incomplete tasks (with estimates and due dates) from RTM and your
    calendar's freebusy feed, then fits tasks into available working slots.
    This is read-only -- no changes are written to RTM.

    Tasks tagged 'nomove' keep their current due date. Tasks without
    estimates are excluded. Only tasks with existing due dates are scheduled.

    Args:
        days: Number of working days to plan (default 5, i.e. one work week).
    """
    try:
        result = plan_tasks(days)
    except Exception as e:
        return json.dumps({"error": str(e)})

    if not result.days:
        return json.dumps({
            "summary": "No schedulable tasks found (need incomplete tasks with estimates and due dates).",
            "days": [],
        })

    summary, json_data = _format_plan(result)
    return json.dumps({"summary": summary, **json_data})


@mcp.tool()
def apply(days: int = 5, confirm: bool = False) -> str:
    """Apply the planned schedule by writing new due dates to RTM.

    WARNING: This modifies task due dates in Remember The Milk.
    You MUST set confirm=true to proceed. Always run 'plan' first to
    preview changes before applying.

    Args:
        days: Number of working days to schedule (default 5).
        confirm: Safety flag -- must be true to write changes.
    """
    if not confirm:
        return json.dumps({
            "error": "Refused: confirm must be true. Run 'plan' first to preview, "
                     "then call apply with confirm=true.",
        })

    try:
        changes = apply_plan(days)
    except Exception as e:
        return json.dumps({"error": str(e)})

    summary, json_data = _format_changes(changes)
    return json.dumps({"summary": summary, "changes": json_data, "count": len(json_data)})


@mcp.tool()
def capacity(days: int = 5) -> str:
    """Show calendar availability for upcoming working days.

    Reads the freebusy calendar feed and computes free working time per day,
    accounting for meetings (with buffer), work windows, and minimum focus
    block requirements. Does not look at RTM tasks.

    Args:
        days: Number of working days to show (default 5).
    """
    try:
        caps = compute_capacity(days)
    except Exception as e:
        return json.dumps({"error": str(e)})

    summary, json_days = _format_capacity(caps)
    return json.dumps({"summary": summary, "days": json_days})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

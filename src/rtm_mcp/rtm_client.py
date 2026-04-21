"""RTM API client.

Handles authentication, signing, and all RTM REST API calls.
Configuration is read from environment variables.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date

from .models import RTMTask

API_URL = "https://api.rememberthemilk.com/services/rest/"


def _get_config() -> tuple[str, str, str]:
    """Return (api_key, shared_secret, auth_token) from env."""
    api_key = os.environ.get("RTM_API_KEY", "")
    secret = os.environ.get("RTM_SHARED_SECRET", "")
    token = os.environ.get("RTM_AUTH_TOKEN", "").strip()

    if not token:
        token_file = os.environ.get("RTM_TOKEN_FILE", "")
        if token_file:
            try:
                with open(os.path.expanduser(token_file)) as f:
                    token = f.read().strip()
            except FileNotFoundError:
                pass

    if not api_key or not secret or not token:
        missing = []
        if not api_key:
            missing.append("RTM_API_KEY")
        if not secret:
            missing.append("RTM_SHARED_SECRET")
        if not token:
            missing.append("RTM_AUTH_TOKEN (or RTM_TOKEN_FILE)")
        raise RuntimeError(
            f"Missing RTM credentials: {', '.join(missing)}. "
            "Set them as environment variables."
        )

    return api_key, secret, token


def sign(params: dict, secret: str) -> str:
    """Sign request per RTM API spec (MD5 of secret + sorted key/value pairs)."""
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    return hashlib.md5((secret + sorted_params).encode()).hexdigest()


def api_call(method: str, **extra) -> dict:
    """Make a signed RTM API call and return the parsed JSON response."""
    api_key, secret, token = _get_config()
    params = {
        "api_key": api_key,
        "format": "json",
        "method": method,
        "auth_token": token,
        **extra,
    }
    params["api_sig"] = sign(params, secret)
    url = API_URL + "?" + urllib.parse.urlencode(params)
    resp = urllib.request.urlopen(url, timeout=30).read()
    return json.loads(resp)


def _check_response(data: dict) -> None:
    """Raise on RTM API error."""
    if data["rsp"]["stat"] != "ok":
        msg = data["rsp"].get("err", {}).get("msg", "Unknown RTM API error")
        raise RuntimeError(msg)


def timeline_create() -> str:
    """Create an RTM timeline (required for write operations)."""
    data = api_call("rtm.timelines.create")
    _check_response(data)
    return str(data["rsp"]["timeline"])


def task_set_due_date(timeline: str, task: RTMTask, new_due: date) -> dict:
    """Set a task's due date in RTM."""
    data = api_call(
        "rtm.tasks.setDueDate",
        timeline=timeline,
        list_id=task.list_id,
        taskseries_id=task.taskseries_id,
        task_id=task.task_id,
        due=new_due.isoformat(),
        has_due_time="0",
    )
    _check_response(data)
    return data


def _parse_estimate_to_min(s: str) -> int | None:
    """Parse RTM estimate strings into minutes.

    Handles: "30m", "1h", "1h30m", "1:30", "90", "90m",
    PT30M, PT1H, PT1H30M (ISO-8601 durations).
    """
    if not s:
        return None
    s = str(s).strip().lower()

    # ISO-8601 durations: PT30M, PT1H, PT1H30M
    m = re.fullmatch(r"pt(?:(\d+)h)?(?:(\d+)m)?", s)
    if m and (m.group(1) or m.group(2)):
        h = int(m.group(1) or 0)
        mi = int(m.group(2) or 0)
        return h * 60 + mi

    # 1:30
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    # 1h30m / 1h / 30m
    m = re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", s)
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)

    # digits alone => minutes
    if re.fullmatch(r"\d+", s):
        return int(s)

    # "2 hours", "30 mins" etc.
    m = re.fullmatch(r"(\d+)\s*(hour|hours|hr|hrs)", s)
    if m:
        return int(m.group(1)) * 60
    m = re.fullmatch(r"(\d+)\s*(min|mins|minute|minutes)", s)
    if m:
        return int(m.group(1))

    return None


def fetch_tasks(rtm_filter: str) -> list[RTMTask]:
    """Fetch tasks from RTM matching the given filter string.

    Only returns tasks that have time estimates. Tasks without estimates
    are excluded because they can't be scheduled.
    """
    data = api_call("rtm.tasks.getList", filter=rtm_filter)
    _check_response(data)

    tasks: list[RTMTask] = []
    lists_data = data["rsp"].get("tasks", {}).get("list", [])
    if not isinstance(lists_data, list):
        lists_data = [lists_data] if lists_data else []

    for lst in lists_data:
        list_id = str(lst.get("id", ""))
        taskseries = lst.get("taskseries", [])
        if not isinstance(taskseries, list):
            taskseries = [taskseries]
        for ts in taskseries:
            taskseries_id = str(ts.get("id", ""))
            name = ts.get("name", "")
            created_iso = str(ts.get("created", ""))

            tags_raw = (ts.get("tags") or {}).get("tag", [])
            if isinstance(tags_raw, str):
                tags = {tags_raw}
            elif isinstance(tags_raw, list):
                tags = {str(x) for x in tags_raw}
            else:
                tags = set()

            task_list = ts.get("task", [])
            if not isinstance(task_list, list):
                task_list = [task_list]

            for task in task_list:
                if task.get("completed"):
                    continue
                est = task.get("estimate", "")
                est_min = _parse_estimate_to_min(est)
                if est_min is None:
                    continue
                tasks.append(
                    RTMTask(
                        name=name,
                        priority=str(task.get("priority", "N")),
                        estimate_min=int(est_min),
                        due_iso=str(task.get("due", "")),
                        created_iso=created_iso,
                        tags=frozenset(tags),
                        list_id=list_id,
                        taskseries_id=taskseries_id,
                        task_id=str(task.get("id", "")),
                    )
                )

    return tasks


def fetch_tasks_all(rtm_filter: str) -> list[dict]:
    """Fetch tasks from RTM matching the filter, including those without estimates.

    Returns lightweight dicts suitable for display (not RTMTask objects).
    """
    data = api_call("rtm.tasks.getList", filter=rtm_filter)
    _check_response(data)

    tasks: list[dict] = []
    lists_data = data["rsp"].get("tasks", {}).get("list", [])
    if not isinstance(lists_data, list):
        lists_data = [lists_data] if lists_data else []

    for lst in lists_data:
        taskseries = lst.get("taskseries", [])
        if not isinstance(taskseries, list):
            taskseries = [taskseries]
        for ts in taskseries:
            task_list = ts.get("task", [])
            if not isinstance(task_list, list):
                task_list = [task_list]
            for task in task_list:
                if task.get("completed"):
                    continue
                est_raw = task.get("estimate", "")
                est_min = _parse_estimate_to_min(est_raw)
                tasks.append(
                    {
                        "name": ts.get("name", ""),
                        "priority": str(task.get("priority", "N")),
                        "due": str(task.get("due", "")),
                        "estimate": est_raw,
                        "estimate_min": est_min,
                    }
                )

    tasks.sort(key=lambda x: (x["due"] or "z", x["priority"]))
    return tasks

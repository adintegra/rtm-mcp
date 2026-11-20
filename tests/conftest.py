"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin configuration so tests don't depend on the developer's environment."""
    for var in (
        "RTM_API_KEY",
        "RTM_SHARED_SECRET",
        "RTM_AUTH_TOKEN",
        "RTM_TOKEN_FILE",
        "RTM_CALENDAR_ICS_URL",
        "RTM_MEETING_BUFFER_MIN",
        "RTM_FOCUS_BLOCK_MIN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RTM_TZ", "UTC")
    monkeypatch.setenv("RTM_WORK_WINDOWS", "09:00-12:00,13:00-17:00")

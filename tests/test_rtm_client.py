from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from rtm_mcp import rtm_client as rtm
from rtm_mcp.models import RTMTask


class TestSign:
    def test_md5_of_secret_and_sorted_params(self):
        expected = hashlib.md5(b"SECRETa1b2c3").hexdigest()
        assert rtm.sign({"c": "3", "a": "1", "b": "2"}, "SECRET") == expected


class TestParseEstimate:
    @pytest.mark.parametrize(
        ("raw", "minutes"),
        [
            ("30m", 30),
            ("1h", 60),
            ("1h30m", 90),
            ("1 h 30 m", 90),
            ("1:30", 90),
            ("90", 90),
            ("PT30M", 30),
            ("PT1H", 60),
            ("PT1H30M", 90),
            ("2 hours", 120),
            ("45 mins", 45),
            ("  1H  ", 60),
        ],
    )
    def test_valid(self, raw, minutes):
        assert rtm._parse_estimate_to_min(raw) == minutes

    @pytest.mark.parametrize("raw", ["", "soon", "PT", "abc123"])
    def test_invalid(self, raw):
        assert rtm._parse_estimate_to_min(raw) is None


class TestConfig:
    def test_missing_credentials(self):
        with pytest.raises(RuntimeError) as exc:
            rtm._get_config()
        msg = str(exc.value)
        assert "RTM_API_KEY" in msg
        assert "RTM_SHARED_SECRET" in msg
        assert "RTM_AUTH_TOKEN" in msg

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("RTM_API_KEY", "k")
        monkeypatch.setenv("RTM_SHARED_SECRET", "s")
        monkeypatch.setenv("RTM_AUTH_TOKEN", " t ")
        assert rtm._get_config() == ("k", "s", "t")

    def test_token_from_file(self, monkeypatch, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("filetoken\n")
        monkeypatch.setenv("RTM_API_KEY", "k")
        monkeypatch.setenv("RTM_SHARED_SECRET", "s")
        monkeypatch.setenv("RTM_TOKEN_FILE", str(token_file))
        assert rtm._get_config()[2] == "filetoken"

    def test_missing_token_file_reports_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RTM_API_KEY", "k")
        monkeypatch.setenv("RTM_SHARED_SECRET", "s")
        monkeypatch.setenv("RTM_TOKEN_FILE", str(tmp_path / "nope"))
        with pytest.raises(RuntimeError, match="RTM_AUTH_TOKEN"):
            rtm._get_config()


class TestCheckResponse:
    def test_ok(self):
        rtm._check_response({"rsp": {"stat": "ok"}})

    def test_error_message(self):
        with pytest.raises(RuntimeError, match="Invalid auth token"):
            rtm._check_response({"rsp": {"stat": "fail", "err": {"msg": "Invalid auth token"}}})

    def test_error_without_message(self):
        with pytest.raises(RuntimeError, match="Unknown RTM API error"):
            rtm._check_response({"rsp": {"stat": "fail"}})


def _response(lists):
    return {"rsp": {"stat": "ok", "tasks": {"list": lists}}}


class TestFetchTasks:
    def _patch(self, monkeypatch, payload):
        monkeypatch.setattr(rtm, "api_call", lambda method, **kw: payload)

    def test_parses_tasks(self, monkeypatch):
        self._patch(
            monkeypatch,
            _response(
                [
                    {
                        "id": "L1",
                        "taskseries": [
                            {
                                "id": "S1",
                                "name": "Write report",
                                "created": "2026-01-01T08:00:00Z",
                                "tags": {"tag": ["nomove", "work"]},
                                "task": [
                                    {
                                        "id": "T1",
                                        "priority": "1",
                                        "estimate": "PT1H30M",
                                        "due": "2026-03-02T00:00:00Z",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            ),
        )
        [task] = rtm.fetch_tasks("status:incomplete")
        assert task == RTMTask(
            name="Write report",
            priority="1",
            estimate_min=90,
            due_iso="2026-03-02T00:00:00Z",
            created_iso="2026-01-01T08:00:00Z",
            tags=frozenset({"nomove", "work"}),
            list_id="L1",
            taskseries_id="S1",
            task_id="T1",
        )

    def test_single_objects_instead_of_lists(self, monkeypatch):
        # RTM's JSON collapses single-element lists into bare objects.
        self._patch(
            monkeypatch,
            {
                "rsp": {
                    "stat": "ok",
                    "tasks": {
                        "list": {
                            "id": "L1",
                            "taskseries": {
                                "id": "S1",
                                "name": "Solo",
                                "tags": {"tag": "single"},
                                "task": {"id": "T1", "estimate": "30m"},
                            },
                        }
                    },
                }
            },
        )
        [task] = rtm.fetch_tasks("x")
        assert task.tags == frozenset({"single"})
        assert task.priority == "N"

    def test_skips_completed_and_unestimated(self, monkeypatch):
        self._patch(
            monkeypatch,
            _response(
                [
                    {
                        "id": "L1",
                        "taskseries": [
                            {
                                "id": "S1",
                                "name": "a",
                                "task": [
                                    {"id": "1", "estimate": "30m", "completed": "2026-01-01"},
                                    {"id": "2", "estimate": ""},
                                    {"id": "3", "estimate": "30m"},
                                ],
                            }
                        ],
                    }
                ]
            ),
        )
        assert [t.task_id for t in rtm.fetch_tasks("x")] == ["3"]

    def test_empty(self, monkeypatch):
        self._patch(monkeypatch, {"rsp": {"stat": "ok", "tasks": {}}})
        assert rtm.fetch_tasks("x") == []


class TestFetchTasksAll:
    def test_includes_unestimated_and_sorts(self, monkeypatch):
        payload = _response(
            [
                {
                    "id": "L1",
                    "taskseries": [
                        {
                            "id": "S1",
                            "name": "late",
                            "task": [{"due": "2026-03-05", "priority": "1"}],
                        },
                        {
                            "id": "S2",
                            "name": "early",
                            "task": [{"due": "2026-03-01", "estimate": "1h"}],
                        },
                        {"id": "S3", "name": "nodue", "task": [{"priority": "2"}]},
                    ],
                }
            ]
        )
        monkeypatch.setattr(rtm, "api_call", lambda method, **kw: payload)
        result = rtm.fetch_tasks_all("x")
        assert [t["name"] for t in result] == ["early", "late", "nodue"]
        assert result[0]["estimate_min"] == 60
        assert result[1]["estimate_min"] is None


class TestApiCall:
    def test_signs_request(self, monkeypatch):
        monkeypatch.setenv("RTM_API_KEY", "k")
        monkeypatch.setenv("RTM_SHARED_SECRET", "s")
        monkeypatch.setenv("RTM_AUTH_TOKEN", "t")
        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps({"rsp": {"stat": "ok"}}).encode()

        def fake_urlopen(url, timeout):
            captured["url"] = url
            return FakeResp()

        monkeypatch.setattr(rtm.urllib.request, "urlopen", fake_urlopen)
        assert rtm.api_call("rtm.test.echo", foo="bar") == {"rsp": {"stat": "ok"}}
        assert "api_sig=" in captured["url"]
        assert "method=rtm.test.echo" in captured["url"]
        assert "foo=bar" in captured["url"]


def test_task_set_due_date_sends_expected_params(monkeypatch):
    calls = []

    def fake_api_call(method, **kw):
        calls.append((method, kw))
        return {"rsp": {"stat": "ok"}}

    monkeypatch.setattr(rtm, "api_call", fake_api_call)
    task = RTMTask("n", "1", 30, "", "", frozenset(), "L", "S", "T")
    rtm.task_set_due_date("TL", task, date(2026, 3, 2))
    assert calls == [
        (
            "rtm.tasks.setDueDate",
            {
                "timeline": "TL",
                "list_id": "L",
                "taskseries_id": "S",
                "task_id": "T",
                "due": "2026-03-02",
                "has_due_time": "0",
            },
        )
    ]

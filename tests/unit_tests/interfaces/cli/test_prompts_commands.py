# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
import json
from types import TracebackType

import httpx
import pytest
from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app
from relay_teams.interfaces.cli import run_prompt_cli as prompt_cli

runner = CliRunner()


class _FakePromptStreamResponse:
    def __init__(
        self,
        lines: list[str],
        *,
        error_response: httpx.Response | None = None,
    ) -> None:
        self._lines = lines
        self._error_response = error_response

    async def __aenter__(self) -> _FakePromptStreamResponse:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, traceback)

    def raise_for_status(self) -> None:
        if self._error_response is not None:
            raise httpx.HTTPStatusError(
                "failed",
                request=self._error_response.request,
                response=self._error_response,
            )
        return None

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _FakePromptHttpClient:
    def __init__(
        self,
        lines: list[str],
        *,
        error_response: httpx.Response | None = None,
    ) -> None:
        self._lines = lines
        self._error_response = error_response
        self.streams: list[tuple[str, str, dict[str, str]]] = []

    async def __aenter__(self) -> _FakePromptHttpClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, traceback)

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> _FakePromptStreamResponse:
        self.streams.append((method, url, headers))
        return _FakePromptStreamResponse(
            self._lines,
            error_response=self._error_response,
        )


class _FakeAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body

    async def aclose(self) -> None:
        return None


def test_roles_prompt_builds_preview_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_autostart(base_url: str, autostart: bool) -> None:
        captured["base_url"] = base_url
        captured["autostart"] = autostart

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {
            "role_id": "Coordinator",
            "objective": "Draft release note",
            "tools": ["orch_dispatch_task"],
            "skills": ["time"],
            "runtime_system_prompt": "runtime",
            "provider_system_prompt": "provider",
            "user_prompt": "user",
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        [
            "roles",
            "prompt",
            "--role-id",
            "Coordinator",
            "--objective",
            "Draft release note",
            "--tool",
            "orch_dispatch_task",
            "--skill",
            "time",
            "--shared-state-json",
            '{"lang":"zh-CN"}',
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "base_url": cli_app.DEFAULT_BASE_URL,
        "autostart": True,
        "method": "POST",
        "path": "/api/prompts:preview",
        "payload": {
            "role_id": "Coordinator",
            "objective": "Draft release note",
            "shared_state": {"lang": "zh-CN"},
            "tools": ["orch_dispatch_task"],
            "skills": ["time"],
        },
    }
    assert '"provider_system_prompt": "provider"' in result.output


def test_roles_prompt_without_role_id_shows_available_roles(monkeypatch) -> None:
    captured: list[str] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, method, payload, timeout_seconds)
        captured.append(path)
        return [
            {"role_id": "Coordinator"},
            {"role_id": "writer_agent"},
        ]

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["roles", "prompt"])

    assert result.exit_code == 2
    assert captured == ["/api/roles"]
    assert "Missing required option: --role-id" in result.output
    assert "Coordinator" in result.output
    assert "Usage: relay-teams roles prompt --role-id <role_id>" in result.output


def test_roles_prompt_default_output_prints_full_prompt(monkeypatch) -> None:
    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, method, path, payload, timeout_seconds)
        return {
            "role_id": "Coordinator",
            "objective": "Draft release note",
            "tools": ["orch_dispatch_task"],
            "skills": ["time"],
            "runtime_system_prompt": "runtime line",
            "provider_system_prompt": "provider line",
            "user_prompt": "user line",
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        [
            "roles",
            "prompt",
            "--role-id",
            "Coordinator",
        ],
    )

    assert result.exit_code == 0
    assert "provider line" in result.output
    assert result.output.count("provider line") == 1
    assert "runtime line" not in result.output
    assert "user line" in result.output
    assert "role_id:" not in result.output
    assert "+-" not in result.output


def test_run_prompt_handle_stream_line_prints_text_and_stops(capsys) -> None:
    text_event = {
        "event_type": prompt_cli.RunEventType.TEXT_DELTA.value,
        "payload_json": json.dumps({"text": "hello"}),
    }
    completed_event = {"event_type": prompt_cli.RunEventType.RUN_COMPLETED.value}

    assert prompt_cli._handle_stream_line("", debug=False) is False
    assert prompt_cli._handle_stream_line("event: ping", debug=False) is False
    assert prompt_cli._handle_stream_line("data:", debug=False) is False
    assert (
        prompt_cli._handle_stream_line(
            "data: " + json.dumps(text_event),
            debug=False,
        )
        is False
    )
    assert (
        prompt_cli._handle_stream_line(
            "data: " + json.dumps(completed_event),
            debug=False,
        )
        is True
    )

    assert capsys.readouterr().out == "hello"


def test_run_prompt_handle_stream_line_supports_debug_and_errors(capsys) -> None:
    debug_event = {"event_type": "custom_event", "payload_json": "{}"}
    error_event = {"error": "boom"}

    assert (
        prompt_cli._handle_stream_line(
            "data: " + json.dumps(debug_event),
            debug=True,
        )
        is False
    )
    assert json.loads(capsys.readouterr().out) == debug_event
    with pytest.raises(RuntimeError, match="boom"):
        prompt_cli._handle_stream_line(
            "data: " + json.dumps(error_event),
            debug=False,
        )


@pytest.mark.asyncio
async def test_run_prompt_stream_events_async_reads_sse_lines(
    monkeypatch,
    capsys,
) -> None:
    fake_client = _FakePromptHttpClient(
        [
            "",
            "event: ping",
            "data:",
            "data: "
            + json.dumps(
                {
                    "event_type": prompt_cli.RunEventType.TEXT_DELTA.value,
                    "payload_json": json.dumps({"content": "chunk"}),
                }
            ),
            "data: "
            + json.dumps({"event_type": prompt_cli.RunEventType.RUN_COMPLETED.value}),
        ]
    )
    captured_kwargs: dict[str, object] = {}

    def fake_create_async_http_client(**kwargs: object) -> _FakePromptHttpClient:
        captured_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        prompt_cli,
        "create_async_http_client",
        fake_create_async_http_client,
    )

    await prompt_cli.stream_events_async(
        base_url="http://127.0.0.1:8000/",
        run_id="run-1",
        debug=False,
    )

    assert capsys.readouterr().out == "chunk"
    assert fake_client.streams == [
        (
            "GET",
            "http://127.0.0.1:8000/api/runs/run-1/events",
            {"Accept": "text/event-stream"},
        )
    ]
    assert captured_kwargs["timeout_seconds"] == 600.0


@pytest.mark.asyncio
async def test_run_prompt_stream_events_async_reports_http_errors(monkeypatch) -> None:
    response = httpx.Response(
        500,
        request=httpx.Request("GET", "http://127.0.0.1:8000/api/runs/run-1/events"),
        stream=_FakeAsyncByteStream(b"failed"),
    )

    def fake_create_async_http_client(**kwargs: object) -> _FakePromptHttpClient:
        _ = kwargs
        return _FakePromptHttpClient([], error_response=response)

    monkeypatch.setattr(
        prompt_cli,
        "create_async_http_client",
        fake_create_async_http_client,
    )

    with pytest.raises(
        RuntimeError,
        match="HTTP 500 while streaming run run-1: failed",
    ):
        await prompt_cli.stream_events_async(
            base_url="http://127.0.0.1:8000",
            run_id="run-1",
            debug=False,
        )

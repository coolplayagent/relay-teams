# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.tools.im_tools import im_send as im_send_module
from relay_teams.tools.runtime.context import ToolContext, ToolDeps


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        _ = description

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            return func

        return decorator


class _FakeImToolService:
    def __init__(self) -> None:
        self.sent_text: list[tuple[str, str, str | None]] = []
        self.sent_files: list[tuple[str, Path, str | None]] = []

    async def send_text(
        self, *, session_id: str, text: str, run_id: str | None = None
    ) -> dict[str, JsonValue]:
        self.sent_text.append((session_id, text, run_id))
        return {"kind": "text", "text": text}

    async def send_file(
        self, *, session_id: str, file_path: Path, run_id: str | None = None
    ) -> dict[str, JsonValue]:
        self.sent_files.append((session_id, file_path, run_id))
        return {"kind": "file", "file_path": str(file_path)}


class _FakeDeps:
    def __init__(self, *, service: _FakeImToolService, workspace: Path) -> None:
        self.im_tool_service = service
        self.session_id = "session-1"
        self.run_id = "run-1"
        self.workspace = _FakeWorkspace(workspace)


class _FakeWorkspace:
    def __init__(self, execution_root: Path) -> None:
        self.execution_root = execution_root

    def resolve_path(self, path: str, *, write: bool = False) -> Path:
        _ = write
        return (self.execution_root / path).resolve()


class _FakeContext:
    def __init__(self, deps: _FakeDeps) -> None:
        self.deps = deps


@pytest.mark.asyncio
async def test_im_send_register_executes_text_and_file_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    im_send_module.register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        fake_agent.tools["im_send"],
    )
    service = _FakeImToolService()
    ctx = cast(
        ToolContext, _FakeContext(_FakeDeps(service=service, workspace=tmp_path))
    )
    file_path = tmp_path / "report.txt"
    file_path.write_text("content", encoding="utf-8")

    async def _fake_execute_tool(
        ctx: ToolContext,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[dict[str, JsonValue]]],
    ) -> dict[str, JsonValue]:
        _ = (ctx, tool_name, args_summary)
        return await action()

    monkeypatch.setattr(im_send_module, "execute_tool", _fake_execute_tool)

    result = await tool(ctx, text="hello", file_path=str(file_path))

    assert result["status"] == "ok"
    assert service.sent_text == [("session-1", "hello", "run-1")]
    assert service.sent_files == [("session-1", file_path, "run-1")]

# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools.runtime.context import ToolContext, ToolDeps
from relay_teams.tools.runtime.models import ToolResultProjection
from relay_teams.tools.workspace_tools import register_edit
from relay_teams.tools.workspace_tools.edit import (
    _project_edit_result,
    apply_edit,
    edit_file_with_guard,
    replace_content,
)
from relay_teams.tools.workspace_tools.edit_state import (
    READ_STATE_PREFIX,
    assert_file_was_read,
    load_file_read_state,
    normalize_resolved_path,
    record_file_read,
    record_file_read_async,
)


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self,
        *,
        description: str,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        del description

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            return func

        return decorator


class _FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.scope_root = root

    def resolve_path(self, relative_path: str, *, write: bool = False) -> Path:
        _ = write
        return (self.scope_root / relative_path).resolve()


def test_replace_content_replaces_unique_exact_match() -> None:
    result = replace_content("alpha beta gamma", "beta", "delta")
    assert result == "alpha delta gamma"


def test_replace_content_replace_all_replaces_every_occurrence() -> None:
    result = replace_content("foo bar foo baz foo", "foo", "qux", replace_all=True)
    assert result == "qux bar qux baz qux"


def test_replace_content_supports_indentation_flexible_match() -> None:
    content = "def demo():\n    if True:\n        return 1\n"
    old = "if True:\n    return 1"
    new = "if False:\n    return 2"
    result = replace_content(content, old, new)
    assert "if False:" in result
    assert "return 2" in result


def test_replace_content_rejects_identical_old_and_new() -> None:
    with pytest.raises(ValueError, match="identical"):
        replace_content("alpha", "beta", "beta")


def test_replace_content_rejects_missing_match() -> None:
    with pytest.raises(ValueError, match="Could not find"):
        replace_content("alpha beta", "gamma", "delta")


def test_replace_content_rejects_ambiguous_match_without_replace_all() -> None:
    with pytest.raises(ValueError, match="multiple matches"):
        replace_content("foo foo", "foo", "bar")


def test_apply_edit_creates_new_file_when_old_string_is_empty(tmp_path: Path) -> None:
    file_path = tmp_path / "nested" / "new.txt"
    result = apply_edit(file_path=file_path, old_string="", new_string="hello")
    assert file_path.read_text(encoding="utf-8") == "hello"
    assert result["diff_summary"] == "  + 1: 1 line(s) added"


def test_apply_edit_preserves_lf_when_inputs_use_crlf(tmp_path: Path) -> None:
    file_path = tmp_path / "lf.txt"
    with file_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("alpha\nbeta\ngamma\n")

    apply_edit(
        file_path=file_path,
        old_string="alpha\r\nbeta\r\ngamma",
        new_string="alpha\r\nbeta-updated\r\ngamma",
    )

    with file_path.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    assert content == "alpha\nbeta-updated\ngamma\n"
    assert "\r\n" not in content


def test_apply_edit_preserves_crlf_when_inputs_use_lf(tmp_path: Path) -> None:
    file_path = tmp_path / "crlf.txt"
    with file_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("alpha\r\nbeta\r\ngamma\r\n")

    apply_edit(
        file_path=file_path,
        old_string="alpha\nbeta\ngamma",
        new_string="alpha\nbeta-updated\ngamma",
    )

    with file_path.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    assert content == "alpha\r\nbeta-updated\r\ngamma\r\n"


def test_edit_file_with_guard_requires_read_first(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")

    with pytest.raises(ValueError, match="You must read file"):
        edit_file_with_guard(
            shared_store=shared_store,
            session_id="session-1",
            conversation_id="conversation-1",
            file_path=file_path,
            old_string="content",
            new_string="updated",
        )


def test_edit_file_with_guard_succeeds_after_read(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    record_file_read(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )

    result = edit_file_with_guard(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        file_path=file_path,
        old_string="content",
        new_string="updated",
    )

    assert file_path.read_text(encoding="utf-8") == "updated"
    assert "Edit applied successfully." in result["output"]
    state = load_file_read_state(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )
    assert state is not None


def test_edit_file_with_guard_reuses_read_state_for_same_session(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    record_file_read(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )

    result = edit_file_with_guard(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        file_path=file_path,
        old_string="content",
        new_string="updated",
    )

    assert file_path.read_text(encoding="utf-8") == "updated"
    assert "Edit applied successfully." in result["output"]


def test_file_read_state_does_not_leak_between_conversations(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    record_file_read(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )

    assert (
        load_file_read_state(
            shared_store=shared_store,
            session_id="session-1",
            conversation_id="conversation-2",
            path=file_path,
        )
        is None
    )


def test_file_read_state_ignores_invalid_persisted_payload(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-1"),
            key=(
                f"{READ_STATE_PREFIX}conversation-1:"
                f"{normalize_resolved_path(file_path)}"
            ),
            value_json='{"path": "", "mtime_ns": -1, "size": -1}',
        )
    )

    assert (
        load_file_read_state(
            shared_store=shared_store,
            session_id="session-1",
            conversation_id="conversation-1",
            path=file_path,
        )
        is None
    )


@pytest.mark.asyncio
async def test_async_file_read_state_round_trips(tmp_path: Path) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")

    await record_file_read_async(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )

    state = load_file_read_state(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )
    assert state is not None
    assert state.path.endswith("file.txt")


@pytest.mark.asyncio
async def test_edit_tool_runs_registered_sync_action(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import edit as edit_module

    file_path = tmp_path / "demo.txt"
    file_path.write_text("before\n", encoding="utf-8")
    shared_store = SharedStateRepository(tmp_path / "state.db")
    record_file_read(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )
    fake_agent = _FakeAgent()
    register_edit(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, JsonValue]]],
        fake_agent.tools["edit"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=shared_store,
            session_id="session-1",
            conversation_id="conversation-1",
        )
    )

    async def _fake_execute_tool_call(
        ctx: ToolContext,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., ToolResultProjection | Awaitable[ToolResultProjection]],
        raw_args: dict[str, object],
        **kwargs: object,
    ) -> dict[str, JsonValue]:
        del ctx, tool_name, args_summary, kwargs
        parameter_names = set(inspect.signature(action).parameters)
        action_args = {
            key: value for key, value in raw_args.items() if key in parameter_names
        }
        maybe_projection = action(**action_args)
        if inspect.isawaitable(maybe_projection):
            projection = await maybe_projection
        else:
            projection = maybe_projection
        return cast(dict[str, JsonValue], projection.internal_data)

    monkeypatch.setattr(edit_module, "execute_tool_call", _fake_execute_tool_call)

    result = await tool(
        cast(ToolContext, cast(object, ctx)),
        path="demo.txt",
        old_string="before\n",
        new_string="after\n",
    )

    assert "Edit applied successfully." in cast(str, result["output"])
    assert file_path.read_text(encoding="utf-8") == "after\n"


def test_edit_file_with_guard_rejects_external_change_after_read(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    record_file_read(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )
    file_path.write_text("changed externally", encoding="utf-8")

    with pytest.raises(ValueError, match="modified since it was last read"):
        edit_file_with_guard(
            shared_store=shared_store,
            session_id="session-1",
            conversation_id="conversation-1",
            file_path=file_path,
            old_string="changed externally",
            new_string="updated",
        )


def test_edit_file_with_guard_allows_new_file_without_prior_read(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "state.db")
    file_path = tmp_path / "new.txt"

    edit_file_with_guard(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        file_path=file_path,
        old_string="",
        new_string="created",
    )

    assert file_path.read_text(encoding="utf-8") == "created"
    assert_file_was_read(
        shared_store=shared_store,
        session_id="session-1",
        conversation_id="conversation-1",
        path=file_path,
    )


def test_project_edit_result_keeps_only_output_visible() -> None:
    projected = _project_edit_result(
        {
            "path": "demo.txt",
            "output": "Edit applied successfully.",
            "diff": "@@ -1 +1 @@",
            "diff_summary": "  ~ 1: 1 line(s) changed",
        }
    )

    assert projected.visible_data == {"output": "Edit applied successfully."}
    assert projected.internal_data == {
        "path": "demo.txt",
        "output": "Edit applied successfully.",
        "diff": "@@ -1 +1 @@",
        "diff_summary": "  ~ 1: 1 line(s) changed",
    }


def test_apply_edit_rejects_existing_notebook_file(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.ipynb"
    file_path.write_text(
        '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Use notebook_edit"):
        apply_edit(
            file_path=file_path,
            old_string="[]",
            new_string="[{}]",
        )

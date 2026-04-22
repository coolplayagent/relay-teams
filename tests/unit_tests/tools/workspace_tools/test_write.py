from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.models import ToolResultProjection
from relay_teams.tools.workspace_tools import register_write, register_write_tmp


async def _invoke_tool_action(
    action: Callable[..., Awaitable[ToolResultProjection]],
    raw_args: dict[str, object] | None,
) -> ToolResultProjection:
    resolved_raw_args = {} if raw_args is None else raw_args
    tool_args = {
        name: resolved_raw_args[name]
        for name in inspect.signature(action).parameters
        if name in resolved_raw_args
    }
    return await action(**tool_args)


class TestAtomicWrite:
    def test_atomic_write_creates_file(self, tmp_path):
        from relay_teams.tools.workspace_tools.write import atomic_write

        test_file = tmp_path / "new.txt"

        atomic_write(test_file, "hello world")

        assert test_file.exists()
        assert test_file.read_text() == "hello world"

    def test_atomic_write_overwrites(self, tmp_path):
        from relay_teams.tools.workspace_tools.write import atomic_write

        test_file = tmp_path / "test.txt"
        test_file.write_text("old content")

        atomic_write(test_file, "new content")

        assert test_file.read_text() == "new content"

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        from relay_teams.tools.workspace_tools.write import atomic_write

        test_file = tmp_path / "subdir" / "nested" / "file.txt"

        atomic_write(test_file, "content")

        assert test_file.exists()
        assert test_file.read_text() == "content"

    def test_atomic_write_empty_content(self, tmp_path):
        from relay_teams.tools.workspace_tools.write import atomic_write

        test_file = tmp_path / "empty.txt"

        atomic_write(test_file, "")

        assert test_file.exists()
        assert test_file.read_text() == ""

    def test_atomic_write_special_chars(self, tmp_path):
        from relay_teams.tools.workspace_tools.write import atomic_write

        test_file = tmp_path / "special.txt"
        content = "line1\nline2\nline3 with 'quotes' and \"double quotes\""

        atomic_write(test_file, content)

        assert test_file.read_text() == content


class TestGenerateDiff:
    def test_generate_diff_no_change(self):
        from relay_teams.tools.workspace_tools.write import generate_diff

        old = "line1\nline2\nline3\n"
        new = "line1\nline2\nline3\n"

        diff = generate_diff("test.txt", old, new)

        assert diff == ""

    def test_generate_diff_modify(self):
        from relay_teams.tools.workspace_tools.write import generate_diff

        old = "line1\nline2\nline3\n"
        new = "line1\nmodified\nline3\n"

        diff = generate_diff("test.txt", old, new)

        assert "---" in diff
        assert "+++" in diff
        assert "modified" in diff

    def test_generate_diff_add(self):
        from relay_teams.tools.workspace_tools.write import generate_diff

        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"

        diff = generate_diff("test.txt", old, new)

        assert "+++" in diff
        assert "line3" in diff

    def test_generate_diff_delete(self):
        from relay_teams.tools.workspace_tools.write import generate_diff

        old = "line1\nline2\nline3\n"
        new = "line1\nline3\n"

        diff = generate_diff("test.txt", old, new)

        assert "---" in diff


class TestFormatDiffShort:
    def test_format_diff_no_changes(self):
        from relay_teams.tools.workspace_tools.write import format_diff_short

        old = "line1\nline2\n"
        new = "line1\nline2\n"

        result = format_diff_short(old, new)

        assert result == "No changes"

    def test_format_diff_modify(self):
        from relay_teams.tools.workspace_tools.write import format_diff_short

        old = "line1\nline2\nline3\n"
        new = "line1\nmodified\nline3\n"

        result = format_diff_short(old, new)

        assert "~" in result
        assert "changed" in result

    def test_format_diff_add(self):
        from relay_teams.tools.workspace_tools.write import format_diff_short

        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"

        result = format_diff_short(old, new)

        assert "+" in result
        assert "added" in result

    def test_format_diff_delete(self):
        from relay_teams.tools.workspace_tools.write import format_diff_short

        old = "line1\nline2\nline3\n"
        new = "line1\nline3\n"

        result = format_diff_short(old, new)

        assert "-" in result
        assert "deleted" in result


def test_project_write_result_keeps_only_output_visible() -> None:
    from relay_teams.tools.workspace_tools.write import _project_write_result

    projected = _project_write_result(
        output="Wrote file successfully.\n\nDiff:\nNo changes",
        diff_summary="No changes",
        path="demo.txt",
        created=True,
    )

    assert projected.visible_data == {
        "output": "Wrote file successfully.\n\nDiff:\nNo changes"
    }
    assert projected.internal_data == {
        "output": "Wrote file successfully.\n\nDiff:\nNo changes",
        "diff_summary": "No changes",
        "path": "demo.txt",
        "created": True,
    }


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        del description

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            return func

        return decorator


class _FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.execution_root = root
        self.tmp_root = root / "tmp"

    def resolve_path(self, relative_path: str, *, write: bool = False) -> Path:
        del write
        if relative_path == "tmp" or relative_path.startswith("tmp/"):
            suffix = relative_path.removeprefix("tmp").lstrip("/\\")
            return (self.tmp_root / suffix).resolve()
        return (self.execution_root / relative_path).resolve()

    def resolve_tmp_path(self, relative_path: str, *, write: bool = True) -> Path:
        del write
        requested_path = (self.tmp_root / relative_path).resolve()
        if (
            requested_path == self.tmp_root
            or self.tmp_root.resolve() not in requested_path.parents
        ):
            raise ValueError(
                f"Path is outside workspace tmp directory: {relative_path}"
            )
        return requested_path


@pytest.mark.asyncio
async def test_write_tool_supports_managed_tmp_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import write as write_module

    fake_agent = _FakeAgent()
    register_write(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
        )
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.internal_data)

    monkeypatch.setattr(write_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(ctx, path="tmp/reports/spec.md", content="hello tmp\n")

    assert result["path"] == "tmp/reports/spec.md"
    assert (tmp_path / "tmp" / "reports" / "spec.md").read_text(encoding="utf-8") == (
        "hello tmp\n"
    )


@pytest.mark.asyncio
async def test_write_tmp_tool_is_confined_to_workspace_tmp_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import write_tmp as write_tmp_module

    fake_agent = _FakeAgent()
    register_write_tmp(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write_tmp"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
        )
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.internal_data)

    monkeypatch.setattr(write_tmp_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(ctx, path="reports/spec.md", content="hello tmp only\n")

    assert result["path"] == "tmp/reports/spec.md"
    assert (tmp_path / "tmp" / "reports" / "spec.md").read_text(
        encoding="utf-8"
    ) == "hello tmp only\n"


@pytest.mark.asyncio
async def test_write_tmp_tool_uses_shared_tmp_path_resolver(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import write_tmp as write_tmp_module

    fake_agent = _FakeAgent()
    register_write_tmp(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write_tmp"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
        )
    )
    resolved_paths: list[str] = []

    def _fake_resolve_workspace_tmp_path(workspace, relative_path: str) -> Path:
        del workspace
        resolved_paths.append(relative_path)
        target = tmp_path / "tmp" / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.internal_data)

    monkeypatch.setattr(
        write_tmp_module,
        "resolve_workspace_tmp_path",
        _fake_resolve_workspace_tmp_path,
    )
    monkeypatch.setattr(write_tmp_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(ctx, path="tmp/reports/spec.md", content="resolved once\n")

    assert resolved_paths == ["reports/spec.md"]
    assert result["path"] == "tmp/reports/spec.md"
    assert (tmp_path / "tmp" / "reports" / "spec.md").read_text(
        encoding="utf-8"
    ) == "resolved once\n"


@pytest.mark.asyncio
async def test_write_tmp_tool_rejects_paths_outside_workspace_tmp_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import write_tmp as write_tmp_module

    fake_agent = _FakeAgent()
    register_write_tmp(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write_tmp"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
        )
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
        approval_request_factory=None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, approval_request, approval_request_factory
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.internal_data)

    monkeypatch.setattr(write_tmp_module, "execute_tool_call", _fake_execute_tool)

    with pytest.raises(ValueError, match="outside workspace tmp directory"):
        await tool(ctx, path="../outside.md", content="should fail\n")


@pytest.mark.asyncio
async def test_write_tool_rejects_existing_notebook_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import write as write_module

    fake_agent = _FakeAgent()
    register_write(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write"],
    )
    file_path = tmp_path / "demo.ipynb"
    file_path.write_text(
        '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}',
        encoding="utf-8",
    )
    ctx = SimpleNamespace(deps=SimpleNamespace(workspace=_FakeWorkspace(tmp_path)))

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        approval_request=None,
        raw_args: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request, kwargs
        parameter_names = set(inspect.signature(action).parameters)
        action_args = {
            key: value
            for key, value in dict(raw_args or {}).items()
            if key != "ctx" and not key.startswith("_") and key in parameter_names
        }
        return cast(dict[str, object], (await action(**action_args)).internal_data)

    monkeypatch.setattr(write_module, "execute_tool_call", _fake_execute_tool)

    with pytest.raises(ValueError, match="Use notebook_edit"):
        await tool(
            ctx,
            path="demo.ipynb",
            content='{"cells": [{}], "metadata": {}, "nbformat": 4}',
        )


@pytest.mark.asyncio
async def test_write_tool_rejects_bom_prefixed_existing_notebook_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import write as write_module

    fake_agent = _FakeAgent()
    register_write(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write"],
    )
    file_path = tmp_path / "demo.ipynb"
    file_path.write_text(
        '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}',
        encoding="utf-8-sig",
    )
    ctx = SimpleNamespace(deps=SimpleNamespace(workspace=_FakeWorkspace(tmp_path)))

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        approval_request=None,
        raw_args: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request, kwargs
        parameter_names = set(inspect.signature(action).parameters)
        action_args = {
            key: value
            for key, value in dict(raw_args or {}).items()
            if key != "ctx" and not key.startswith("_") and key in parameter_names
        }
        return cast(dict[str, object], (await action(**action_args)).internal_data)

    monkeypatch.setattr(write_module, "execute_tool_call", _fake_execute_tool)

    with pytest.raises(ValueError, match="Use notebook_edit"):
        await tool(
            ctx,
            path="demo.ipynb",
            content='{"cells": [{}], "metadata": {}, "nbformat": 4}',
        )


@pytest.mark.asyncio
async def test_write_tool_allows_repairing_invalid_notebook_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import write as write_module

    fake_agent = _FakeAgent()
    register_write(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write"],
    )
    file_path = tmp_path / "demo.ipynb"
    file_path.write_text("not notebook json", encoding="utf-8")
    ctx = SimpleNamespace(deps=SimpleNamespace(workspace=_FakeWorkspace(tmp_path)))

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        approval_request=None,
        raw_args: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request, kwargs
        parameter_names = set(inspect.signature(action).parameters)
        action_args = {
            key: value
            for key, value in dict(raw_args or {}).items()
            if key != "ctx" and not key.startswith("_") and key in parameter_names
        }
        return cast(dict[str, object], (await action(**action_args)).internal_data)

    monkeypatch.setattr(write_module, "execute_tool_call", _fake_execute_tool)

    content = '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}'
    result = await tool(ctx, path="demo.ipynb", content=content)

    assert result["created"] is False
    assert file_path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_write_tool_allows_repairing_notebook_with_non_object_cell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import write as write_module

    fake_agent = _FakeAgent()
    register_write(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["write"],
    )
    file_path = tmp_path / "demo.ipynb"
    file_path.write_text(
        '{"cells": [null], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}',
        encoding="utf-8",
    )
    ctx = SimpleNamespace(deps=SimpleNamespace(workspace=_FakeWorkspace(tmp_path)))

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        approval_request=None,
        raw_args: dict[str, object] | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request, kwargs
        parameter_names = set(inspect.signature(action).parameters)
        action_args = {
            key: value
            for key, value in dict(raw_args or {}).items()
            if key != "ctx" and not key.startswith("_") and key in parameter_names
        }
        return cast(dict[str, object], (await action(**action_args)).internal_data)

    monkeypatch.setattr(write_module, "execute_tool_call", _fake_execute_tool)

    content = '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}'
    result = await tool(ctx, path="demo.ipynb", content=content)

    assert result["created"] is False
    assert file_path.read_text(encoding="utf-8") == content

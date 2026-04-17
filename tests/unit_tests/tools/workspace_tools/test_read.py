from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools.runtime import ToolResultProjection
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.workspace_tools import register_read
from relay_teams.tools.workspace_tools.edit_state import load_file_read_state


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

    def resolve_read_path(self, relative_path: str) -> Path:
        return (self.scope_root / relative_path).resolve()


class TestIsBinaryFile:
    def test_binary_extension_zip(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"PK\x03\x04")

        assert is_binary_file(test_file, 4) is True

    def test_binary_extension_exe(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "test.exe"
        test_file.write_bytes(b"MZ")

        assert is_binary_file(test_file, 2) is True

    def test_text_file(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    print('world')")

        assert is_binary_file(test_file, test_file.stat().st_size) is False

    def test_null_byte(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello\x00world")

        assert is_binary_file(test_file, 11) is True

    def test_empty_file(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import is_binary_file

        test_file = tmp_path / "empty.txt"
        test_file.touch()

        assert is_binary_file(test_file, 0) is False


class TestReadFileContent:
    @pytest.mark.asyncio
    async def test_read_file_all_lines(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "test.py"
        test_file.write_text("line1\nline2\nline3\n")

        lines, total, truncated_lines, truncated_bytes = await read_file_content(
            test_file, offset=1, limit=10
        )

        assert lines == ["line1", "line2", "line3"]
        assert total == 3
        assert truncated_lines is False
        assert truncated_bytes is False

    @pytest.mark.asyncio
    async def test_read_with_offset(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "test.py"
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        lines, total, _, _ = await read_file_content(test_file, offset=3, limit=2)

        assert lines == ["line3", "line4"]
        assert total == 5

    @pytest.mark.asyncio
    async def test_read_line_limit(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "test.py"
        test_file.write_text("\n".join([f"line{i}" for i in range(20)]))

        lines, total, truncated, _ = await read_file_content(
            test_file, offset=1, limit=5
        )

        assert len(lines) == 5
        assert truncated is True

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "empty.txt"
        test_file.touch()

        lines, total, _, _ = await read_file_content(test_file)

        assert lines == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_read_file_rejects_non_positive_limit(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_file_content

        test_file = tmp_path / "test.py"
        test_file.write_text("line1\nline2\n", encoding="utf-8")

        with pytest.raises(ValueError, match="limit must be greater than 0"):
            await read_file_content(test_file, limit=0)


class TestReadDirectory:
    def test_read_directory(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_directory

        (tmp_path / "dir1").mkdir()
        (tmp_path / "file1.txt").touch()
        (tmp_path / "file2.py").touch()

        entries, total, truncated = read_directory(tmp_path, offset=1, limit=10)

        assert "dir1/" in entries
        assert "file1.txt" in entries
        assert "file2.py" in entries
        assert total == 3
        assert truncated is False

    def test_read_directory_with_offset(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_directory

        (tmp_path / "a.txt").touch()
        (tmp_path / "b.txt").touch()
        (tmp_path / "c.txt").touch()

        entries, total, truncated = read_directory(tmp_path, offset=2, limit=1)

        assert len(entries) == 1
        assert entries[0] == "b.txt"
        assert total == 3
        assert truncated is True

    def test_read_directory_sorted(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_directory

        (tmp_path / "z.txt").touch()
        (tmp_path / "a.txt").touch()
        (tmp_path / "m.txt").touch()

        entries, _, _ = read_directory(tmp_path, offset=1, limit=10)

        assert entries[0] == "a.txt"
        assert entries[1] == "m.txt"
        assert entries[2] == "z.txt"

    def test_read_directory_rejects_non_positive_offset(self, tmp_path):
        from relay_teams.tools.workspace_tools.read import read_directory

        (tmp_path / "a.txt").touch()

        with pytest.raises(ValueError, match="offset must be greater than 0"):
            read_directory(tmp_path, offset=0, limit=1)


def test_project_read_result_keeps_output_first_shape() -> None:
    from relay_teams.tools.workspace_tools.read import _project_read_result

    projected = _project_read_result(
        output="<content>\n1: hello\n</content>",
        truncated=True,
        next_offset=2,
    )

    assert projected.visible_data == {
        "output": "<content>\n1: hello\n</content>",
        "truncated": True,
        "next_offset": 2,
    }
    assert projected.internal_data == {
        "output": "<content>\n1: hello\n</content>",
        "truncated": True,
        "next_offset": 2,
    }


@pytest.mark.asyncio
async def test_read_tool_reads_notebook_cell_without_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import read as read_module

    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "id": "intro",
                "metadata": {},
                "source": "# Title\n",
            },
            {
                "cell_type": "code",
                "id": "calc",
                "metadata": {},
                "source": "print(1)\n",
                "execution_count": 1,
                "outputs": [
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": "1\n",
                    }
                ],
            },
        ],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    instruction_path = source_dir / "AGENTS.md"
    instruction_path.write_text("Notebook instructions.", encoding="utf-8")
    file_path = source_dir / "demo.ipynb"
    file_path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
    shared_store = SharedStateRepository(tmp_path / "state.db")
    fake_agent = _FakeAgent()
    register_read(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["read"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=shared_store,
            task_id="task-1",
        )
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[ToolResultProjection]],
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(dict[str, object], (await action()).internal_data)

    monkeypatch.setattr(read_module, "execute_tool", _fake_execute_tool)

    result = await tool(
        ctx,
        path="src/demo.ipynb",
        cell_id="cell-1",
        include_outputs=False,
    )

    output = cast(str, result["output"])
    assert result["truncated"] is False
    assert result["next_offset"] is None
    assert "<type>notebook</type>" in output
    assert "<instructions>" in output
    assert "Notebook instructions." in output
    assert '"cell_id": "calc"' in output
    assert '"source": "print(1)\\n"' in output
    assert '"outputs"' not in output
    from relay_teams.agents.execution.prompt_instruction_state import (
        is_prompt_instruction_loaded,
    )

    assert is_prompt_instruction_loaded(
        shared_store=shared_store,
        task_id="task-1",
        path=instruction_path,
    )
    assert (
        load_file_read_state(
            shared_store=shared_store,
            task_id="task-1",
            path=file_path,
        )
        is not None
    )


@pytest.mark.asyncio
async def test_resolve_read_instruction_sections_injects_nested_agents_once(
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools.read import resolve_read_instruction_sections

    workspace_root = tmp_path / "workspace"
    source_dir = workspace_root / "src" / "components"
    file_path = source_dir / "button.py"
    db_path = tmp_path / "state.db"
    workspace_root.mkdir()
    source_dir.mkdir(parents=True)
    (workspace_root / "AGENTS.md").write_text("Root instructions.", encoding="utf-8")
    (workspace_root / "src" / "AGENTS.md").write_text(
        "Nested instructions.",
        encoding="utf-8",
    )
    file_path.write_text("print('hello')\n", encoding="utf-8")
    shared_store = SharedStateRepository(db_path)
    deps = SimpleNamespace(
        shared_store=shared_store,
        task_id="task-1",
        workspace=SimpleNamespace(scope_root=workspace_root),
    )

    first = await resolve_read_instruction_sections(
        deps=cast(ToolDeps, deps),
        file_path=file_path,
    )
    second = await resolve_read_instruction_sections(
        deps=cast(ToolDeps, deps),
        file_path=file_path,
    )

    assert first == (
        f"Instructions from: {(workspace_root / 'src' / 'AGENTS.md').resolve()}\n"
        "Nested instructions.",
    )
    assert second == ()


@pytest.mark.asyncio
async def test_resolve_read_instruction_sections_skips_preloaded_paths(
    tmp_path: Path,
) -> None:
    from relay_teams.agents.execution.prompt_instruction_state import (
        record_prompt_instruction_loaded,
    )
    from relay_teams.tools.workspace_tools.read import resolve_read_instruction_sections

    workspace_root = tmp_path / "workspace"
    source_dir = workspace_root / "src"
    file_path = source_dir / "worker.py"
    db_path = tmp_path / "state.db"
    workspace_root.mkdir()
    source_dir.mkdir(parents=True)
    instruction_path = source_dir / "AGENTS.md"
    instruction_path.write_text("Nested instructions.", encoding="utf-8")
    file_path.write_text("print('hello')\n", encoding="utf-8")
    shared_store = SharedStateRepository(db_path)
    record_prompt_instruction_loaded(
        shared_store=shared_store,
        task_id="task-1",
        path=instruction_path,
    )
    deps = SimpleNamespace(
        shared_store=shared_store,
        task_id="task-1",
        workspace=SimpleNamespace(scope_root=workspace_root),
    )

    sections = await resolve_read_instruction_sections(
        deps=cast(ToolDeps, deps),
        file_path=file_path,
    )

    assert sections == ()


@pytest.mark.asyncio
async def test_read_tool_rejects_office_documents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import read as read_module

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    file_path = source_dir / "report.pdf"
    file_path.write_bytes(b"%PDF-1.7")
    fake_agent = _FakeAgent()
    register_read(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["read"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=SharedStateRepository(tmp_path / "state.db"),
            task_id="task-1",
        )
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[[], Awaitable[ToolResultProjection]],
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(dict[str, object], (await action()).internal_data)

    monkeypatch.setattr(read_module, "execute_tool", _fake_execute_tool)

    with pytest.raises(ValueError, match="Cannot read binary file: src/report.pdf"):
        await tool(ctx, path="src/report.pdf")

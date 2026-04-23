from __future__ import annotations

from collections.abc import Awaitable, Callable
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

import relay_teams.tools.workspace_tools as workspace_tools_module
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools.runtime.models import ToolResultProjection
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.workspace_tools import register_office_read_markdown
from relay_teams.tools.workspace_tools.edit_state import load_file_read_state


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


def test_register_office_read_markdown_only_registers_requested_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def _fake_register_single_tool(agent: object, tool_name: str) -> None:
        _ = agent
        captured.append(tool_name)

    monkeypatch.setattr(
        workspace_tools_module,
        "_register_single_tool",
        _fake_register_single_tool,
    )
    fake_agent = _FakeAgent()

    register_office_read_markdown(cast(Agent[ToolDeps, str], fake_agent))

    assert captured == ["office_read_markdown"]


class TestPaginateTextContent:
    def test_paginate_text_content_respects_offset_and_limit(self) -> None:
        from relay_teams.tools.workspace_tools.read_support import paginate_text_content

        lines, total, truncated_lines, truncated_bytes = paginate_text_content(
            "line1\nline2\nline3\nline4",
            offset=2,
            limit=2,
        )

        assert lines == ["line2", "line3"]
        assert total == 4
        assert truncated_lines is True
        assert truncated_bytes is False

    def test_paginate_text_content_respects_byte_limit(self) -> None:
        from relay_teams.tools.workspace_tools.read_support import paginate_text_content

        lines, total, truncated_lines, truncated_bytes = paginate_text_content(
            "alpha\nbeta\ngamma",
            max_bytes=8,
        )

        assert lines == ["alpha"]
        assert total == 2
        assert truncated_lines is False
        assert truncated_bytes is True

    def test_paginate_text_content_rejects_non_positive_limit(self) -> None:
        from relay_teams.tools.workspace_tools.read_support import paginate_text_content

        with pytest.raises(ValueError, match="limit must be greater than 0"):
            paginate_text_content("line1\nline2", limit=0)

    def test_paginate_text_content_rejects_non_positive_offset(self) -> None:
        from relay_teams.tools.workspace_tools.read_support import paginate_text_content

        with pytest.raises(ValueError, match="offset must be greater than 0"):
            paginate_text_content("line1\nline2", offset=0)


@pytest.mark.asyncio
async def test_office_read_markdown_tool_converts_supported_pdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import (
        OfficeConversionPage,
        OfficeConversionQuality,
    )
    from relay_teams.tools.workspace_tools import (
        office_read_markdown as office_read_markdown_module,
    )

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    instruction_path = source_dir / "AGENTS.md"
    instruction_path.write_text("PDF instructions.", encoding="utf-8")
    file_path = source_dir / "report.pdf"
    file_path.write_bytes(b"%PDF-1.7")
    shared_store = SharedStateRepository(tmp_path / "state.db")
    fake_agent = _FakeAgent()
    register_office_read_markdown(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["office_read_markdown"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=shared_store,
            task_id="task-1",
        )
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(
            dict[str, object],
            (await _invoke_tool_action(action, raw_args)).internal_data,
        )

    def _fake_convert(file_path: Path, **_: object) -> OfficeConversionPage:
        return OfficeConversionPage(
            lines=("# Report", "Converted PDF body"),
            total_lines=2,
            converter_name="markitdown",
            quality=OfficeConversionQuality(level="medium", preserves_tables=False),
            warnings=("PDF layout reconstruction may be approximate.",),
        )

    monkeypatch.setattr(
        office_read_markdown_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )
    monkeypatch.setattr(
        office_read_markdown_module,
        "paginate_office_document_markdown",
        _fake_convert,
    )

    result = await tool(ctx, path="src/report.pdf")

    output = cast(str, result["output"])
    assert result["truncated"] is False
    assert result["next_offset"] is None
    assert result["content_format"] == "markdown"
    assert result["line_numbers"] is False
    assert result["converter_name"] == "markitdown"
    assert result["conversion_quality"] == "medium"
    assert result["preserves_tables"] is False
    assert result["warnings"] == ["PDF layout reconstruction may be approximate."]
    assert "<type>file</type>" in output
    assert "<content_format>markdown</content_format>" in output
    assert "<line_numbers>false</line_numbers>" in output
    assert "<converter_name>markitdown</converter_name>" in output
    assert "<conversion_quality>medium</conversion_quality>" in output
    assert "<preserves_tables>false</preserves_tables>" in output
    assert "<warnings>" in output
    assert "<instructions>" in output
    assert "PDF instructions." in output
    assert "# Report" in output
    assert "Converted PDF body" in output
    assert "1: # Report" not in output
    assert "End of file - total 2 lines" in output
    assert (
        load_file_read_state(
            shared_store=shared_store,
            task_id="task-1",
            path=file_path,
        )
        is not None
    )
    from relay_teams.agents.execution.prompt_instruction_state import (
        is_prompt_instruction_loaded,
    )

    assert is_prompt_instruction_loaded(
        shared_store=shared_store,
        task_id="task-1",
        path=instruction_path,
    )


@pytest.mark.asyncio
async def test_office_read_markdown_preserves_markdown_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import (
        OfficeConversionPage,
        OfficeConversionQuality,
    )
    from relay_teams.tools.workspace_tools import (
        office_read_markdown as office_read_markdown_module,
    )

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    file_path = source_dir / "table.xlsx"
    file_path.write_bytes(b"placeholder")
    fake_agent = _FakeAgent()
    register_office_read_markdown(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["office_read_markdown"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=SharedStateRepository(tmp_path / "state.db"),
            task_id="task-1",
        )
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(
            dict[str, object],
            (await _invoke_tool_action(action, raw_args)).internal_data,
        )

    def _fake_convert(file_path: Path, **_: object) -> OfficeConversionPage:
        return OfficeConversionPage(
            lines=(
                "## Sheet1",
                "| Name | Score |",
                "| --- | --- |",
                "| Alice | 95 |",
                "| Bob | 88 |",
            ),
            total_lines=5,
            converter_name="markitdown",
            quality=OfficeConversionQuality(level="high", preserves_tables=True),
        )

    monkeypatch.setattr(
        office_read_markdown_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )
    monkeypatch.setattr(
        office_read_markdown_module,
        "paginate_office_document_markdown",
        _fake_convert,
    )

    result = await tool(ctx, path="src/table.xlsx")

    output = cast(str, result["output"])
    assert result["line_numbers"] is False
    assert result["conversion_quality"] == "high"
    assert result["preserves_tables"] is True
    assert result["warnings"] == []
    assert "| Name | Score |" in output
    assert "| --- | --- |" in output
    assert "| Alice | 95 |" in output
    assert "2: | Name | Score |" not in output


@pytest.mark.asyncio
async def test_office_read_markdown_allows_explicit_line_numbers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import (
        OfficeConversionPage,
        OfficeConversionQuality,
    )
    from relay_teams.tools.workspace_tools import (
        office_read_markdown as office_read_markdown_module,
    )

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    file_path = source_dir / "table.xlsx"
    file_path.write_bytes(b"placeholder")
    fake_agent = _FakeAgent()
    register_office_read_markdown(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["office_read_markdown"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=SharedStateRepository(tmp_path / "state.db"),
            task_id="task-1",
        )
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(
            dict[str, object],
            (await _invoke_tool_action(action, raw_args)).internal_data,
        )

    def _fake_convert(file_path: Path, **_: object) -> OfficeConversionPage:
        return OfficeConversionPage(
            lines=("## Sheet1", "| Name | Score |", "| --- | --- |", "| Alice | 95 |"),
            total_lines=4,
            converter_name="markitdown",
            quality=OfficeConversionQuality(level="high", preserves_tables=True),
        )

    monkeypatch.setattr(
        office_read_markdown_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )
    monkeypatch.setattr(
        office_read_markdown_module,
        "paginate_office_document_markdown",
        _fake_convert,
    )

    result = await tool(ctx, path="src/table.xlsx", line_numbers=True)

    output = cast(str, result["output"])
    assert result["line_numbers"] is True
    assert "<line_numbers>true</line_numbers>" in output
    assert "2: | Name | Score |" in output


@pytest.mark.asyncio
async def test_office_read_markdown_surfaces_office_ocr_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import OfficeConversionRequiresOcrError
    from relay_teams.tools.workspace_tools import (
        office_read_markdown as office_read_markdown_module,
    )

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    file_path = source_dir / "scanned.pdf"
    file_path.write_bytes(b"%PDF-1.7")
    fake_agent = _FakeAgent()
    register_office_read_markdown(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["office_read_markdown"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=SharedStateRepository(tmp_path / "state.db"),
            task_id="task-1",
        )
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(
            dict[str, object],
            (await _invoke_tool_action(action, raw_args)).internal_data,
        )

    def _raise_ocr_error(file_path: Path, **_: object) -> object:
        raise OfficeConversionRequiresOcrError(
            f"PDF requires OCR before it can be read as markdown: {file_path.name}"
        )

    monkeypatch.setattr(
        office_read_markdown_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )
    monkeypatch.setattr(
        office_read_markdown_module,
        "paginate_office_document_markdown",
        _raise_ocr_error,
    )

    with pytest.raises(OfficeConversionRequiresOcrError, match="requires OCR"):
        await tool(ctx, path="src/scanned.pdf")


@pytest.mark.asyncio
async def test_office_read_markdown_rejects_non_office_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import (
        office_read_markdown as office_read_markdown_module,
    )

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    file_path = source_dir / "notes.txt"
    file_path.write_text("plain text", encoding="utf-8")
    fake_agent = _FakeAgent()
    register_office_read_markdown(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["office_read_markdown"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=SharedStateRepository(tmp_path / "state.db"),
            task_id="task-1",
        )
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(
            dict[str, object],
            (await _invoke_tool_action(action, raw_args)).internal_data,
        )

    monkeypatch.setattr(
        office_read_markdown_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    with pytest.raises(
        ValueError,
        match="office_read_markdown only supports Office documents and PDFs",
    ):
        await tool(ctx, path="src/notes.txt")


@pytest.mark.asyncio
async def test_office_read_markdown_rejects_non_positive_limit_before_conversion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.workspace_tools import (
        office_read_markdown as office_read_markdown_module,
    )

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    file_path = source_dir / "report.pdf"
    file_path.write_bytes(b"%PDF-1.7")
    fake_agent = _FakeAgent()
    register_office_read_markdown(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["office_read_markdown"],
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            workspace=_FakeWorkspace(tmp_path),
            shared_store=SharedStateRepository(tmp_path / "state.db"),
            task_id="task-1",
        )
    )

    async def _fake_execute_tool_call(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        approval_request=None,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary, approval_request
        return cast(
            dict[str, object],
            (await _invoke_tool_action(action, raw_args)).internal_data,
        )

    def _unexpected_convert(file_path: Path) -> object:
        raise AssertionError(f"unexpected conversion for {file_path}")

    monkeypatch.setattr(
        office_read_markdown_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )
    monkeypatch.setattr(
        office_read_markdown_module,
        "paginate_office_document_markdown",
        _unexpected_convert,
    )

    with pytest.raises(ValueError, match="limit must be greater than 0"):
        await tool(ctx, path="src/report.pdf", limit=0)

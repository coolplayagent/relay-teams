# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.paths import path_exists, path_is_dir, path_is_file
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.office_tools import (
    OfficeConversionPage,
    SUPPORTED_OFFICE_EXTENSIONS,
    paginate_office_document_markdown,
)
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool_call
from relay_teams.tools.runtime.models import ToolResultProjection
from relay_teams.tools.workspace_tools.edit_state import record_file_read_async
from relay_teams.tools.workspace_tools.read_support import (
    DEFAULT_READ_LIMIT,
    MAX_BYTES_LABEL,
    MAX_BYTES,
    MAX_LINE_LENGTH,
    MAX_LINE_SUFFIX,
    _project_read_result,
    resolve_read_instruction_sections,
    validate_pagination_args,
)

DESCRIPTION = load_tool_description(__file__)


def _render_content_lines(
    *,
    lines: list[str],
    offset: int,
    include_line_numbers: bool,
) -> str:
    if include_line_numbers:
        return "\n".join(f"{offset + i}: {line}" for i, line in enumerate(lines))
    return "\n".join(lines)


def _append_office_content_metadata(
    *,
    output: list[str],
    converted: OfficeConversionPage,
    include_line_numbers: bool,
) -> None:
    output.append("<content_format>markdown</content_format>")
    output.append(f"<line_numbers>{str(include_line_numbers).lower()}</line_numbers>")
    output.append(f"<converter_name>{converted.converter_name}</converter_name>")
    output.append(f"<conversion_quality>{converted.quality.level}</conversion_quality>")
    output.append(
        f"<preserves_tables>{str(converted.quality.preserves_tables).lower()}</preserves_tables>"
    )
    if converted.warnings:
        output.append("<warnings>")
        output.append("\n".join(converted.warnings))
        output.append("</warnings>")


def _build_file_metadata(
    *,
    include_line_numbers: bool,
    converted: OfficeConversionPage,
) -> dict[str, JsonValue]:
    return {
        "line_numbers": include_line_numbers,
        "content_format": "markdown",
        "converter_name": converted.converter_name,
        "conversion_quality": converted.quality.level,
        "preserves_tables": converted.quality.preserves_tables,
        "warnings": list(converted.warnings),
    }


def _validate_office_path(*, file_path: Path, path: str) -> None:
    if file_path.suffix.lower() in SUPPORTED_OFFICE_EXTENSIONS:
        return
    supported_extensions = ", ".join(sorted(SUPPORTED_OFFICE_EXTENSIONS))
    raise ValueError(
        "office_read_markdown only supports Office documents and PDFs. "
        f"Got: {path}. Supported extensions: {supported_extensions}"
    )


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def office_read_markdown(
        ctx: ToolContext,
        path: str,
        offset: int = 1,
        limit: int = DEFAULT_READ_LIMIT,
        line_numbers: bool = False,
    ) -> dict[str, JsonValue]:
        """Convert an Office document or PDF to Markdown and page the result."""

        async def _action(
            path: str,
            offset: int = 1,
            limit: int = DEFAULT_READ_LIMIT,
            line_numbers: bool = False,
        ) -> ToolResultProjection:
            validate_pagination_args(offset=offset, limit=limit)
            file_path = ctx.deps.workspace.resolve_read_path(path)

            if not path_exists(file_path):
                raise ValueError(f"File not found: {path}")
            if path_is_dir(file_path):
                raise ValueError(
                    "office_read_markdown only supports files, not directories."
                )
            if not path_is_file(file_path):
                raise ValueError(f"Not a file: {path}")

            _validate_office_path(file_path=file_path, path=path)
            converted = await asyncio.to_thread(
                paginate_office_document_markdown,
                file_path,
                offset=offset,
                limit=limit,
                max_bytes=MAX_BYTES,
                max_line_length=MAX_LINE_LENGTH,
                max_line_suffix=MAX_LINE_SUFFIX,
            )
            lines = list(converted.lines)
            total_lines = converted.total_lines
            truncated_by_lines = converted.truncated_by_lines
            truncated_by_bytes = converted.truncated_by_bytes

            if offset > total_lines and not (offset == 1 and total_lines == 0):
                raise ValueError(
                    f"Offset {offset} is out of range for this file ({total_lines} lines)"
                )

            output = [f"<path>{file_path}</path>"]
            output.append("<type>file</type>")
            instruction_sections = await resolve_read_instruction_sections(
                deps=ctx.deps,
                file_path=file_path,
            )
            if instruction_sections:
                output.append("<instructions>")
                output.append("\n\n".join(instruction_sections))
                output.append("</instructions>")
            _append_office_content_metadata(
                output=output,
                converted=converted,
                include_line_numbers=line_numbers,
            )
            output.append("<content>")
            output.append(
                _render_content_lines(
                    lines=lines,
                    offset=offset,
                    include_line_numbers=line_numbers,
                )
            )

            last_read_line = offset + len(lines) - 1
            continuation_offset: int | None = last_read_line + 1

            if truncated_by_bytes:
                output.append(
                    f"\n\n(Output capped at {MAX_BYTES_LABEL}. "
                    f"Showing lines {offset}-{last_read_line}. "
                    f"Use offset={continuation_offset} to continue.)"
                )
            elif truncated_by_lines:
                output.append(
                    f"\n\n(Showing lines {offset}-{last_read_line} of {total_lines}. "
                    f"Use offset={continuation_offset} to continue.)"
                )
            else:
                continuation_offset = None
                output.append(f"\n\n(End of file - total {total_lines} lines)")

            output.append("</content>")
            await record_file_read_async(
                shared_store=ctx.deps.shared_store,
                session_id=ctx.deps.session_id,
                conversation_id=ctx.deps.conversation_id,
                path=file_path,
            )

            return _project_read_result(
                output="\n".join(output),
                truncated=truncated_by_lines or truncated_by_bytes,
                next_offset=continuation_offset,
                metadata=_build_file_metadata(
                    include_line_numbers=line_numbers,
                    converted=converted,
                ),
            )

        return await execute_tool_call(
            ctx,
            tool_name="office_read_markdown",
            args_summary={
                "path": path,
                "offset": offset,
                "limit": limit,
                "line_numbers": line_numbers,
            },
            action=_action,
            raw_args=locals(),
        )

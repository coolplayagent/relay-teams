# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue

from relay_teams.agents.execution.prompt_instruction_state import (
    filter_unloaded_prompt_instruction_paths,
    record_prompt_instruction_paths_loaded,
)
from relay_teams.agents.execution.prompt_instructions import PromptInstructionResolver
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.models import ToolResultProjection

DEFAULT_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_LINE_SUFFIX = "... (line truncated)"
MAX_BYTES = 50 * 1024
MAX_BYTES_LABEL = "50 KB"


def validate_pagination_args(*, offset: int, limit: int) -> None:
    if offset <= 0:
        raise ValueError("offset must be greater than 0")
    if limit <= 0:
        raise ValueError("limit must be greater than 0")


def paginate_text_content(
    content: str,
    offset: int = 1,
    limit: int = DEFAULT_READ_LIMIT,
    max_bytes: int = MAX_BYTES,
) -> tuple[list[str], int, bool, bool]:
    """Paginate in-memory text using the same limits as file reads."""
    validate_pagination_args(offset=offset, limit=limit)
    lines: list[str] = []
    total_lines = 0
    bytes_count = 0
    truncated_by_lines = False
    truncated_by_bytes = False
    start_offset = offset - 1

    for raw_line in content.splitlines():
        total_lines += 1

        if total_lines <= start_offset:
            continue

        if len(lines) >= limit:
            truncated_by_lines = True
            continue

        line = raw_line
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX

        line_size = len(line.encode("utf-8"))
        if bytes_count + line_size > max_bytes:
            truncated_by_bytes = True
            break

        lines.append(line)
        bytes_count += line_size

    return lines, total_lines, truncated_by_lines, truncated_by_bytes


def _project_read_result(
    *,
    output: str,
    truncated: bool,
    next_offset: int | None,
    metadata: dict[str, JsonValue] | None = None,
) -> ToolResultProjection:
    visible_data: dict[str, JsonValue] = {
        "output": output,
        "truncated": truncated,
        "next_offset": next_offset,
    }
    if metadata:
        visible_data.update(metadata)
    return ToolResultProjection(
        visible_data=visible_data,
        internal_data=dict(visible_data),
    )


async def resolve_read_instruction_sections(
    *,
    deps: ToolDeps,
    file_path: Path,
) -> tuple[str, ...]:
    resolver = PromptInstructionResolver()
    candidate_paths = resolver.resolve_dynamic_paths(
        file_path=file_path,
        workspace_root=deps.workspace.scope_root,
    )
    unresolved_paths = filter_unloaded_prompt_instruction_paths(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        paths=candidate_paths,
    )
    if not unresolved_paths:
        return ()
    loaded = await resolver.load_paths(unresolved_paths)
    record_prompt_instruction_paths_loaded(
        shared_store=deps.shared_store,
        task_id=deps.task_id,
        paths=loaded.local_paths,
    )
    return loaded.sections

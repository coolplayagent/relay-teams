# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue
from pydantic_ai import Agent

from relay_teams.agents.execution.prompt_instruction_state import (
    filter_unloaded_prompt_instruction_paths,
    record_prompt_instruction_paths_loaded,
)
from relay_teams.agents.execution.prompt_instructions import PromptInstructionResolver
from relay_teams.paths import (
    iter_dir_paths,
    open_binary_file,
    open_text_file,
    path_exists,
    path_is_dir,
    path_is_file,
    path_stat,
)
from relay_teams.tools._description_loader import load_tool_description
from relay_teams.tools.runtime import (
    ToolContext,
    ToolDeps,
    ToolResultProjection,
    execute_tool_call,
)
from relay_teams.tools.workspace_tools.edit_state import record_file_read

DEFAULT_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_LINE_SUFFIX = "... (line truncated)"
MAX_BYTES = 50 * 1024
MAX_BYTES_LABEL = "50 KB"
DESCRIPTION = load_tool_description(__file__)

BINARY_EXTENSIONS = {
    ".zip",
    ".tar",
    ".gz",
    ".exe",
    ".dll",
    ".so",
    ".class",
    ".jar",
    ".war",
    ".7z",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".bin",
    ".dat",
    ".obj",
    ".o",
    ".a",
    ".lib",
    ".wasm",
    ".pyc",
    ".pyo",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".pdf",
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
}


def is_binary_file(file_path: Path, file_size: int = 0) -> bool:
    """Detect whether the target file should be treated as binary."""
    ext = file_path.suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True

    if file_size == 0:
        return False

    try:
        with open_binary_file(file_path) as handle:
            sample = handle.read(4096)

        if not sample:
            return False

        if b"\x00" in sample:
            return True

        non_printable = sum(1 for b in sample if b < 9 or (b > 13 and b < 32))
        if non_printable / len(sample) > 0.3:
            return True

    except OSError:
        pass

    return False


async def read_file_content(
    file_path: Path,
    offset: int = 1,
    limit: int = DEFAULT_READ_LIMIT,
    max_bytes: int = MAX_BYTES,
) -> tuple[list[str], int, bool, bool]:
    """Read file content with line and byte limits."""
    lines: list[str] = []
    total_lines = 0
    bytes_count = 0
    truncated_by_lines = False
    truncated_by_bytes = False
    start_offset = offset - 1

    with open_text_file(file_path) as handle:
        for line in handle:
            total_lines += 1

            if total_lines <= start_offset:
                continue

            if len(lines) >= limit:
                truncated_by_lines = True
                continue

            if len(line) > MAX_LINE_LENGTH:
                line = line[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX

            line_size = len(line.encode("utf-8"))
            if bytes_count + line_size > max_bytes:
                truncated_by_bytes = True
                break

            lines.append(line.rstrip("\n"))
            bytes_count += line_size

    return lines, total_lines, truncated_by_lines, truncated_by_bytes


def read_directory(
    dir_path: Path,
    offset: int = 1,
    limit: int = DEFAULT_READ_LIMIT,
) -> tuple[list[str], int, bool]:
    """Read directory entries with offset and limit pagination."""
    entries = []

    for entry in iter_dir_paths(dir_path):
        name = entry.name
        if path_is_dir(entry):
            name += "/"
        entries.append(name)

    entries.sort()

    start = offset - 1
    sliced = entries[start : start + limit]
    truncated = start + len(sliced) < len(entries)

    return sliced, len(entries), truncated


def _project_read_result(
    *,
    output: str,
    truncated: bool,
    next_offset: int | None,
) -> ToolResultProjection:
    visible_data: dict[str, JsonValue] = {
        "output": output,
        "truncated": truncated,
        "next_offset": next_offset,
    }
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


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool(description=DESCRIPTION)
    async def read(
        ctx: ToolContext,
        path: str,
        offset: int = 1,
        limit: int = DEFAULT_READ_LIMIT,
    ) -> dict[str, JsonValue]:
        """Read a file or directory content.

        Args:
            ctx: Tool context.
            path: Path to the file or directory, relative to the workspace root.
            offset: Line offset for files, or entry offset for directories (1-based).
            limit: Maximum number of lines or entries to return.
        """

        async def _action(
            path: str,
            offset: int = 1,
            limit: int = DEFAULT_READ_LIMIT,
        ) -> ToolResultProjection:
            file_path = ctx.deps.workspace.resolve_read_path(path)

            if not path_exists(file_path):
                raise ValueError(f"File not found: {path}")

            if path_is_dir(file_path):
                entries, total, truncated = read_directory(file_path, offset, limit)

                output = [f"<path>{file_path}</path>"]
                output.append("<type>directory</type>")
                output.append("<entries>")
                output.append("\n".join(entries))

                next_offset: int | None = None
                if truncated:
                    next_offset = offset + len(entries)
                    output.append(
                        f"\n(Showing {len(entries)} of {total} entries. "
                        f"Use offset={next_offset} to continue.)"
                    )
                else:
                    output.append(f"\n({total} entries)")
                output.append("</entries>")

                return _project_read_result(
                    output="\n".join(output),
                    truncated=truncated,
                    next_offset=next_offset,
                )

            if not path_is_file(file_path):
                raise ValueError(f"Not a file: {path}")

            if is_binary_file(file_path, path_stat(file_path).st_size):
                raise ValueError(f"Cannot read binary file: {path}")

            (
                lines,
                total_lines,
                truncated_by_lines,
                truncated_by_bytes,
            ) = await read_file_content(file_path, offset, limit)

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
            output.append("<content>")

            numbered_lines = [f"{offset + i}: {line}" for i, line in enumerate(lines)]
            output.append("\n".join(numbered_lines))

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
            record_file_read(
                shared_store=ctx.deps.shared_store,
                task_id=ctx.deps.task_id,
                path=file_path,
            )

            return _project_read_result(
                output="\n".join(output),
                truncated=truncated_by_lines or truncated_by_bytes,
                next_offset=continuation_offset,
            )

        return await execute_tool_call(
            ctx,
            tool_name="read",
            args_summary={"path": path, "offset": offset, "limit": limit},
            action=_action,
            raw_args=locals(),
        )

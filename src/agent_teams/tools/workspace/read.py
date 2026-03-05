from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent

from agent_teams.core.types import JsonObject
from agent_teams.tools.runtime import ToolContext, ToolDeps
from agent_teams.tools.tool_helpers import execute_tool
from agent_teams.tools.file_utils import resolve_workspace_path

DEFAULT_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_LINE_SUFFIX = "... (line truncated)"
MAX_BYTES = 50 * 1024
MAX_BYTES_LABEL = "50 KB"

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
    """检测文件是否为二进制"""
    ext = file_path.suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True

    if file_size == 0:
        return False

    try:
        with open(file_path, "rb") as f:
            sample = f.read(4096)

        if not sample:
            return False

        if b"\x00" in sample:
            return True

        non_printable = sum(1 for b in sample if b < 9 or (b > 13 and b < 32))
        if non_printable / len(sample) > 0.3:
            return True

    except Exception:
        pass

    return False


async def read_file_content(
    file_path: Path,
    offset: int = 1,
    limit: int = DEFAULT_READ_LIMIT,
    max_bytes: int = MAX_BYTES,
) -> tuple[list[str], int, bool, bool]:
    """流式读取文件内容"""
    lines: list[str] = []
    total_lines = 0
    bytes_count = 0
    truncated_by_lines = False
    truncated_by_bytes = False
    start_offset = offset - 1

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
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
    """读取目录内容"""
    entries = []

    for entry in dir_path.iterdir():
        name = entry.name
        if entry.is_dir():
            name += "/"
        entries.append(name)

    entries.sort()

    start = offset - 1
    sliced = entries[start : start + limit]
    truncated = start + len(sliced) < len(entries)

    return sliced, len(entries), truncated


def register(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    async def read(
        ctx: ToolContext,
        path: str,
        offset: int = 1,
        limit: int = DEFAULT_READ_LIMIT,
    ) -> JsonObject:
        async def _action() -> str:
            file_path = resolve_workspace_path(ctx.deps.workspace_root, path)

            if not file_path.exists():
                raise ValueError(f"File not found: {path}")

            if file_path.is_dir():
                entries, total, truncated = read_directory(file_path, offset, limit)

                output = [f"<path>{file_path}</path>"]
                output.append("<type>directory</type>")
                output.append("<entries>")
                output.append("\n".join(entries))

                if truncated:
                    offset_info = offset + len(entries)
                    output.append(
                        f"\n(Showing {len(entries)} of {total} entries. "
                        f"Use offset={offset_info} to continue.)"
                    )
                else:
                    output.append(f"\n({total} entries)")
                output.append("</entries>")

                return "\n".join(output)

            if not file_path.is_file():
                raise ValueError(f"Not a file: {path}")

            if is_binary_file(file_path, file_path.stat().st_size):
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
            output.append("<content>")

            numbered_lines = [f"{offset + i}: {line}" for i, line in enumerate(lines)]
            output.append("\n".join(numbered_lines))

            last_read_line = offset + len(lines) - 1
            next_offset = last_read_line + 1

            if truncated_by_bytes:
                output.append(
                    f"\n\n(Output capped at {MAX_BYTES_LABEL}. "
                    f"Showing lines {offset}-{last_read_line}. "
                    f"Use offset={next_offset} to continue.)"
                )
            elif truncated_by_lines:
                output.append(
                    f"\n\n(Showing lines {offset}-{last_read_line} of {total_lines}. "
                    f"Use offset={next_offset} to continue.)"
                )
            else:
                output.append(f"\n\n(End of file - total {total_lines} lines)")

            output.append("</content>")

            return "\n".join(output)

        return await execute_tool(
            ctx,
            tool_name="read",
            args_summary={"path": path, "offset": offset, "limit": limit},
            action=_action,
        )

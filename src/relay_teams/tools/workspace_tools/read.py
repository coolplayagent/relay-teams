# -*- coding: utf-8 -*-
from __future__ import annotations

import mimetypes
from pathlib import Path

from PIL import (
    Image,
    UnidentifiedImageError,
)
from pydantic import JsonValue
from pydantic_ai import Agent
from pydantic_ai.messages import ToolReturn

from relay_teams.media import (
    MediaModality,
    infer_media_modality,
)
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
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.execution import execute_tool_call
from relay_teams.tools.runtime.models import ToolResultProjection
from relay_teams.tools.workspace_tools.edit_state import record_file_read
from relay_teams.tools.workspace_tools.notebook import (
    read_notebook_for_tool,
)
from relay_teams.tools.workspace_tools.read_support import (
    DEFAULT_READ_LIMIT,
    MAX_BYTES,
    MAX_BYTES_LABEL,
    MAX_LINE_LENGTH,
    MAX_LINE_SUFFIX,
    _project_read_result,
    resolve_read_instruction_sections,
    validate_pagination_args,
)

DESCRIPTION = load_tool_description(__file__)
_READ_TOOL_IMAGE_SOURCE = "read_tool"
_READ_TOOL_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_READ_TOOL_MAX_IMAGE_BYTES_LABEL = "10 MB"

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
    validate_pagination_args(offset=offset, limit=limit)
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
    validate_pagination_args(offset=offset, limit=limit)
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


def _inject_instruction_sections(
    output: str,
    instruction_sections: tuple[str, ...],
) -> str:
    if not instruction_sections:
        return output
    lines = output.splitlines()
    instructions = [
        "<instructions>",
        "\n\n".join(instruction_sections),
        "</instructions>",
    ]
    if len(lines) >= 2 and lines[1] == "<type>notebook</type>":
        return "\n".join([*lines[:2], *instructions, *lines[2:]])
    return "\n".join([*instructions, output])


def _detect_image_mime_type(file_path: Path) -> str | None:
    try:
        with open_binary_file(file_path) as handle:
            with Image.open(handle) as image:
                detected_format = image.format
                image.verify()
    except (OSError, UnidentifiedImageError):
        return None
    detected_mime_type = Image.MIME.get(detected_format or "")
    guessed_mime_type, _ = mimetypes.guess_type(file_path.name, strict=False)
    mime_type = (
        detected_mime_type
        if isinstance(detected_mime_type, str) and detected_mime_type.strip()
        else guessed_mime_type
    )
    if not isinstance(mime_type, str) or not mime_type.strip():
        return None
    try:
        modality = infer_media_modality(mime_type, file_path.name)
    except ValueError:
        return None
    if modality != MediaModality.IMAGE:
        return None
    return mime_type


# noinspection PyTypeHints
def _read_image_capability_error(path: str, support: bool | None) -> str:
    if support is True:
        return ""
    if support is False:
        return (
            "The current model does not have image input enabled for read(). "
            "Enable image input in provider settings if this model supports vision, "
            f"or switch to a vision-capable model, then retry: {path}"
        )
    return (
        "Image input support for the current model is unknown, so read() cannot "
        "attach this image yet. Enable image input in provider settings if this "
        f"model supports vision, or switch to a vision-capable model, then retry: {path}"
    )


# noinspection PyTypeHints
def _image_support(ctx: ToolContext) -> bool | None:
    return ctx.deps.model_capabilities.input.image


def _project_image_read_result(
    *,
    ctx: ToolContext,
    file_path: Path,
    path: str,
) -> ToolResultProjection:
    media_asset_service = ctx.deps.media_asset_service
    if media_asset_service is None:
        raise ValueError("Cannot read image file without media asset support.")
    mime_type = _detect_image_mime_type(file_path)
    if mime_type is None:
        raise ValueError(f"Cannot read binary file: {path}")
    support = _image_support(ctx)
    if support is not True:
        raise ValueError(_read_image_capability_error(path, support))
    image_size_bytes = path_stat(file_path).st_size
    if image_size_bytes > _READ_TOOL_MAX_IMAGE_BYTES:
        raise ValueError(
            "Image file is too large for read(). "
            f"Maximum supported size is {_READ_TOOL_MAX_IMAGE_BYTES_LABEL}: {path}"
        )

    with open_binary_file(file_path) as handle:
        data = handle.read()

    record = media_asset_service.store_bytes(
        session_id=ctx.deps.session_id,
        workspace_id=ctx.deps.workspace_id,
        modality=MediaModality.IMAGE,
        mime_type=mime_type,
        data=data,
        name=file_path.name,
        source=_READ_TOOL_IMAGE_SOURCE,
    )
    media_content_part = media_asset_service.to_content_part(record)
    media_part = media_content_part.model_dump(mode="json")
    record_file_read(
        shared_store=ctx.deps.shared_store,
        task_id=ctx.deps.task_id,
        path=file_path,
    )
    output = "\n".join(
        [
            f"<path>{file_path}</path>",
            "<type>image</type>",
            "<content>",
            f"[image: {file_path.name}]",
            "</content>",
        ]
    )
    return _project_read_result(
        output=output,
        truncated=False,
        next_offset=None,
        metadata={
            "path": str(file_path),
            "type": "image",
            "mime_type": mime_type,
            "content": [media_part],
        },
    ).model_copy(
        update={
            "internal_data": {
                "output": output,
                "truncated": False,
                "next_offset": None,
                "path": str(file_path),
                "type": "image",
                "mime_type": mime_type,
                "content": [media_part],
            },
            "tool_content_parts": (media_content_part,),
        }
    )


def register(agent: Agent[ToolDeps, str]) -> None:
    # noinspection PyTypeHints
    @agent.tool(description=DESCRIPTION)
    async def read(
        ctx: ToolContext,
        path: str,
        offset: int = 1,
        limit: int = DEFAULT_READ_LIMIT,
        cell_id: str | None = None,
        include_outputs: bool = True,
    ) -> ToolReturn | dict[str, JsonValue]:
        """Read a file or directory content.

        Args:
            ctx: Tool context.
            path: Path to the file or directory, relative to the workspace root.
            offset: Line offset for files, or entry offset for directories (1-based).
            limit: Maximum number of lines or entries to return.
            cell_id: Notebook cell id or cell-N fallback index for .ipynb files.
            include_outputs: Whether notebook code cell outputs are included.
        """

        async def _action(
            path: str,
            offset: int = 1,
            limit: int = DEFAULT_READ_LIMIT,
            cell_id: str | None = None,
            include_outputs: bool = True,
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

            if file_path.suffix.lower() == ".ipynb":
                instruction_sections = await resolve_read_instruction_sections(
                    deps=ctx.deps,
                    file_path=file_path,
                )
                output, _cells, truncated, _parsed = read_notebook_for_tool(
                    file_path=file_path,
                    cell_id=cell_id,
                    include_outputs=include_outputs,
                )
                output = _inject_instruction_sections(output, instruction_sections)
                record_file_read(
                    shared_store=ctx.deps.shared_store,
                    task_id=ctx.deps.task_id,
                    path=file_path,
                )
                return _project_read_result(
                    output=output,
                    truncated=truncated,
                    next_offset=None,
                )

            if cell_id is not None:
                raise ValueError("cell_id only applies to Jupyter notebooks (.ipynb).")
            if not include_outputs:
                raise ValueError(
                    "include_outputs only applies to Jupyter notebooks (.ipynb)."
                )

            file_size = path_stat(file_path).st_size
            if is_binary_file(file_path, file_size):
                image_mime_type = _detect_image_mime_type(file_path)
                if image_mime_type is not None:
                    return _project_image_read_result(
                        ctx=ctx,
                        file_path=file_path,
                        path=path,
                    )
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
            args_summary={
                "path": path,
                "offset": offset,
                "limit": limit,
                "cell_id": cell_id,
                "include_outputs": include_outputs,
            },
            action=_action,
            raw_args=locals(),
            allow_tool_return=True,
        )

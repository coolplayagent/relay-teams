# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import JsonValue

from relay_teams.paths import open_text_file, path_exists, path_is_dir
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools.workspace_tools.edit_state import (
    assert_file_unchanged_since_read,
    record_file_read,
)
from relay_teams.tools.workspace_tools.write import (
    atomic_write,
    format_diff_summary,
    generate_diff,
)

MAX_OUTPUT_TEXT_CHARS = 4_000
MAX_NOTEBOOK_OUTPUT_CHARS = 80_000
MAX_RAW_NOTEBOOK_PREVIEW_CHARS = 20_000
EditMode = Literal["replace", "insert", "delete"]
CellType = Literal["code", "markdown"]


def parse_cell_index(cell_id: str) -> int | None:
    if not cell_id.startswith("cell-"):
        return None
    raw_index = cell_id.removeprefix("cell-")
    if not raw_index.isdigit():
        return None
    return int(raw_index)


def read_notebook_text(file_path: Path) -> tuple[str, str]:
    with open_text_file(file_path, encoding="utf-8-sig", newline="") as handle:
        content = handle.read()
    newline = "\r\n" if "\r\n" in content else "\n"
    return content, newline


def load_notebook(file_path: Path) -> tuple[dict[str, Any], str, str]:
    if file_path.suffix.lower() != ".ipynb":
        raise ValueError("File must be a Jupyter notebook (.ipynb).")
    if not path_exists(file_path):
        raise ValueError(f"Notebook file not found: {file_path}")
    if path_is_dir(file_path):
        raise ValueError(f"Path is a directory: {file_path}")

    content, newline = read_notebook_text(file_path)
    try:
        notebook = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(_format_json_decode_error(content, exc)) from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise ValueError("Notebook JSON must contain a cells array.")
    return notebook, content, newline


def _format_json_decode_error(content: str, exc: json.JSONDecodeError) -> str:
    if not content.strip():
        return "Notebook file is empty; expected Jupyter notebook JSON."
    preview = content[:80].replace("\r", "\\r").replace("\n", "\\n")
    return (
        f"Notebook is not valid JSON: {exc.msg} "
        f"at line {exc.lineno} column {exc.colno} (char {exc.pos}); "
        f"first characters: {preview!r}"
    )


def notebook_language(notebook: dict[str, Any]) -> str:
    metadata = notebook.get("metadata")
    if isinstance(metadata, dict):
        language_info = metadata.get("language_info")
        if isinstance(language_info, dict) and isinstance(
            language_info.get("name"),
            str,
        ):
            return language_info["name"]
    return "python"


def normalize_source(source: object) -> str:
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    if isinstance(source, str):
        return source
    return ""


def _truncate_text(text: str, max_chars: int = MAX_OUTPUT_TEXT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (output truncated)"


def _output_text(value: object) -> str:
    if isinstance(value, list):
        return "".join(str(part) for part in value)
    if isinstance(value, str):
        return value
    return ""


def summarize_output(output: object) -> dict[str, JsonValue]:
    if not isinstance(output, dict):
        return {"output_type": "unknown", "text": str(output)}

    output_type = output.get("output_type")
    summary: dict[str, JsonValue] = {
        "output_type": output_type if isinstance(output_type, str) else "unknown"
    }
    if output_type == "stream":
        summary["text"] = _truncate_text(_output_text(output.get("text")))
        return summary

    if output_type in {"execute_result", "display_data"}:
        data = output.get("data")
        if isinstance(data, dict):
            summary["text"] = _truncate_text(_output_text(data.get("text/plain")))
            media_types = sorted(
                key
                for key in data
                if isinstance(key, str) and key not in {"text/plain"}
            )
            if media_types:
                summary["media_types"] = cast(JsonValue, media_types)
        return summary

    if output_type == "error":
        traceback = output.get("traceback")
        trace_text = (
            "\n".join(str(line) for line in traceback)
            if isinstance(traceback, list)
            else ""
        )
        ename = output.get("ename")
        evalue = output.get("evalue")
        header = ": ".join(str(part) for part in (ename, evalue) if part)
        summary["text"] = _truncate_text(
            "\n".join(part for part in (header, trace_text) if part)
        )
        return summary

    if "text" in output:
        summary["text"] = _truncate_text(_output_text(output.get("text")))
    return summary


def project_notebook_cell(
    cell: object,
    *,
    index: int,
    language: str,
    include_outputs: bool,
) -> dict[str, JsonValue]:
    if not isinstance(cell, dict):
        return {
            "cell_id": f"cell-{index}",
            "index": index,
            "cell_type": "unknown",
            "source": str(cell),
        }

    cell_type = cell.get("cell_type")
    cell_id = cell.get("id")
    projected: dict[str, JsonValue] = {
        "cell_id": cell_id if isinstance(cell_id, str) else f"cell-{index}",
        "index": index,
        "cell_type": cell_type if isinstance(cell_type, str) else "unknown",
        "source": normalize_source(cell.get("source")),
    }
    if cell_type == "code":
        projected["language"] = language
        execution_count = cell.get("execution_count")
        if isinstance(execution_count, int) or execution_count is None:
            projected["execution_count"] = execution_count
        outputs = cell.get("outputs")
        if include_outputs and isinstance(outputs, list) and outputs:
            projected["outputs"] = [summarize_output(output) for output in outputs]
        elif include_outputs:
            projected["outputs"] = []
    return projected


def project_notebook(
    notebook: dict[str, Any],
    *,
    cell_id: str | None = None,
    include_outputs: bool = True,
) -> list[dict[str, JsonValue]]:
    cells = cast(list[object], notebook["cells"])
    language = notebook_language(notebook)
    if cell_id:
        index = resolve_cell_index(notebook, cell_id)
        return [
            project_notebook_cell(
                cells[index],
                index=index,
                language=language,
                include_outputs=include_outputs,
            )
        ]
    return [
        project_notebook_cell(
            cell,
            index=index,
            language=language,
            include_outputs=include_outputs,
        )
        for index, cell in enumerate(cells)
    ]


def format_notebook_output(
    *,
    file_path: Path,
    cells: list[dict[str, JsonValue]],
    truncated: bool = False,
) -> str:
    output = [f"<path>{file_path}</path>", "<type>notebook</type>", "<cells>"]
    content = json.dumps(cells, ensure_ascii=False, indent=2)
    if len(content) > MAX_NOTEBOOK_OUTPUT_CHARS:
        truncated = True
        content = (
            content[:MAX_NOTEBOOK_OUTPUT_CHARS] + "\n... (notebook output truncated)"
        )
    output.append(content)
    output.append("</cells>")
    if truncated:
        output.append(
            "Notebook output was truncated. "
            "Use read with cell_id to inspect a single cell."
        )
    return "\n".join(output)


def format_invalid_notebook_output(
    *,
    file_path: Path,
    warning: str,
    content: str,
) -> tuple[str, bool]:
    truncated = len(content) > MAX_RAW_NOTEBOOK_PREVIEW_CHARS
    preview = content[:MAX_RAW_NOTEBOOK_PREVIEW_CHARS]
    if truncated:
        preview += "\n... (raw notebook preview truncated)"
    output = [
        f"<path>{file_path}</path>",
        "<type>notebook</type>",
        "<warning>",
        "Notebook native parsing failed; showing raw text preview instead.",
        warning,
        "</warning>",
        "<raw_content>",
        preview,
        "</raw_content>",
    ]
    return "\n".join(output), truncated


def read_notebook_for_tool(
    *,
    file_path: Path,
    cell_id: str | None = None,
    include_outputs: bool = True,
) -> tuple[str, list[dict[str, JsonValue]], bool, bool]:
    try:
        notebook, _original_content, _newline = load_notebook(file_path)
    except ValueError as exc:
        try:
            raw_content, _newline = read_notebook_text(file_path)
        except OSError as read_exc:
            raise exc from read_exc
        output, truncated = format_invalid_notebook_output(
            file_path=file_path,
            warning=str(exc),
            content=raw_content,
        )
        return output, [], truncated, False

    cells = project_notebook(
        notebook,
        cell_id=cell_id,
        include_outputs=include_outputs,
    )
    output = format_notebook_output(file_path=file_path, cells=cells)
    return output, cells, "... (notebook output truncated)" in output, True


def resolve_cell_index(notebook: dict[str, Any], cell_id: str) -> int:
    cells = cast(list[object], notebook["cells"])
    for index, cell in enumerate(cells):
        if isinstance(cell, dict) and cell.get("id") == cell_id:
            return index

    parsed_index = parse_cell_index(cell_id)
    if parsed_index is not None and 0 <= parsed_index < len(cells):
        return parsed_index
    raise ValueError(f'Cell with ID "{cell_id}" not found in notebook.')


def _should_write_cell_ids(notebook: dict[str, Any]) -> bool:
    nbformat = notebook.get("nbformat")
    minor = notebook.get("nbformat_minor")
    return (
        isinstance(nbformat, int)
        and isinstance(minor, int)
        and (nbformat > 4 or (nbformat == 4 and minor >= 5))
    )


def make_cell(*, cell_type: CellType, source: str, include_id: bool) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        "source": source,
    }
    if include_id:
        cell["id"] = uuid.uuid4().hex
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def apply_notebook_edit(
    notebook: dict[str, Any],
    *,
    cell_id: str | None,
    new_source: str,
    cell_type: CellType | None,
    edit_mode: EditMode,
) -> dict[str, JsonValue]:
    cells = cast(list[Any], notebook["cells"])
    if edit_mode not in {"replace", "insert", "delete"}:
        raise ValueError("edit_mode must be replace, insert, or delete.")
    if edit_mode != "insert" and not cell_id:
        raise ValueError("cell_id is required unless edit_mode is insert.")
    if cell_type is not None and cell_type not in {"code", "markdown"}:
        raise ValueError("cell_type must be code or markdown.")

    target_index = 0
    if cell_id:
        target_index = resolve_cell_index(notebook, cell_id)
        if edit_mode == "insert":
            target_index += 1

    if edit_mode == "insert":
        new_cell = make_cell(
            cell_type=cell_type or "code",
            source=new_source,
            include_id=_should_write_cell_ids(notebook),
        )
        cells.insert(target_index, new_cell)
        new_cell_id = new_cell.get("id")
        return {
            "edit_mode": edit_mode,
            "cell_index": target_index,
            "cell_id": (
                new_cell_id if isinstance(new_cell_id, str) else f"cell-{target_index}"
            ),
            "cell_type": cast(str, new_cell["cell_type"]),
        }

    target_cell = cells[target_index]
    if not isinstance(target_cell, dict):
        raise ValueError(f"Cell at index {target_index} is not a JSON object.")

    resolved_cell_id = target_cell.get("id")
    result_cell_id = (
        resolved_cell_id
        if isinstance(resolved_cell_id, str)
        else f"cell-{target_index}"
    )
    original_cell_type = target_cell.get("cell_type")
    if cell_type:
        result_cell_type = cell_type
    elif original_cell_type in {"code", "markdown"}:
        result_cell_type = cast(str, original_cell_type)
    else:
        result_cell_type = "code"

    if edit_mode == "delete":
        del cells[target_index]
        return {
            "edit_mode": edit_mode,
            "cell_index": target_index,
            "cell_id": result_cell_id,
            "cell_type": result_cell_type,
        }

    target_cell["source"] = new_source
    if cell_type:
        target_cell["cell_type"] = cell_type
    if target_cell.get("cell_type") == "code":
        target_cell["execution_count"] = None
        target_cell["outputs"] = []
    else:
        target_cell.pop("execution_count", None)
        target_cell.pop("outputs", None)
    return {
        "edit_mode": edit_mode,
        "cell_index": target_index,
        "cell_id": result_cell_id,
        "cell_type": cast(str, target_cell.get("cell_type", result_cell_type)),
    }


def dump_notebook(
    notebook: dict[str, Any],
    *,
    newline: str,
    trailing_newline: bool,
) -> str:
    content = json.dumps(notebook, ensure_ascii=False, indent=1)
    if newline != "\n":
        content = content.replace("\n", newline)
    if trailing_newline and not content.endswith(newline):
        content += newline
    return content


def notebook_edit_file_with_guard(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    file_path: Path,
    cell_id: str | None,
    new_source: str,
    cell_type: CellType | None = None,
    edit_mode: EditMode = "replace",
) -> dict[str, JsonValue]:
    assert_file_unchanged_since_read(
        shared_store=shared_store,
        task_id=task_id,
        path=file_path,
    )
    notebook, original_content, newline = load_notebook(file_path)
    metadata = apply_notebook_edit(
        notebook,
        cell_id=cell_id,
        new_source=new_source,
        cell_type=cell_type,
        edit_mode=edit_mode,
    )
    updated_content = dump_notebook(
        notebook,
        newline=newline,
        trailing_newline=original_content.endswith(("\n", "\r\n")),
    )
    atomic_write(file_path, updated_content, encoding="utf-8", newline="")
    record_file_read(shared_store=shared_store, task_id=task_id, path=file_path)
    diff_summary = format_diff_summary(original_content, updated_content)
    diff = generate_diff(str(file_path), original_content, updated_content)
    output = (
        f"Notebook edit applied successfully: {edit_mode} cell "
        f"{metadata['cell_id']} at index {metadata['cell_index']}.\n\n"
        f"Diff:\n{diff_summary}"
    )
    return {
        "path": str(file_path),
        "output": output,
        "diff": diff,
        "diff_summary": diff_summary,
        "edit_mode": edit_mode,
        "cell_id": metadata["cell_id"],
        "cell_index": metadata["cell_index"],
        "cell_type": metadata["cell_type"],
    }

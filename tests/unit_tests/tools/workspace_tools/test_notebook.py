# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.tools.workspace_tools.edit_state import record_file_read
from relay_teams.tools.workspace_tools.notebook import (
    apply_notebook_edit,
    format_invalid_notebook_output,
    load_notebook,
    notebook_edit_file_with_guard,
    project_notebook,
)


def _notebook() -> dict[str, object]:
    return {
        "cells": [
            {
                "cell_type": "markdown",
                "id": "intro",
                "metadata": {"keep": True},
                "source": ["# Title\n", "Body"],
            },
            {
                "cell_type": "code",
                "id": "calc",
                "metadata": {"tags": ["demo"]},
                "source": "print(1)\n",
                "execution_count": 7,
                "outputs": [
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": ["1\n"],
                    }
                ],
            },
        ],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _write_notebook(path: Path, notebook: dict[str, object] | None = None) -> None:
    path.write_text(
        json.dumps(_notebook() if notebook is None else notebook, indent=1),
        encoding="utf-8",
    )


def test_project_notebook_projects_cells_and_outputs(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.ipynb"
    _write_notebook(file_path)

    notebook, _content, _newline = load_notebook(file_path)
    cells = project_notebook(notebook)

    assert cells[0]["cell_id"] == "intro"
    assert cells[0]["cell_type"] == "markdown"
    assert cells[0]["source"] == "# Title\nBody"
    assert cells[1]["cell_id"] == "calc"
    assert cells[1]["language"] == "python"
    assert cells[1]["execution_count"] == 7
    assert cells[1]["outputs"] == [{"output_type": "stream", "text": "1\n"}]


def test_project_notebook_accepts_utf8_bom(tmp_path: Path) -> None:
    file_path = tmp_path / "bom.ipynb"
    file_path.write_text(
        json.dumps(_notebook(), indent=1),
        encoding="utf-8-sig",
    )

    notebook, content, _newline = load_notebook(file_path)
    cells = project_notebook(notebook)

    assert not content.startswith("\ufeff")
    assert cells[0]["cell_id"] == "intro"


def test_project_notebook_accepts_cell_n_fallback(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.ipynb"
    _write_notebook(file_path)

    notebook, _content, _newline = load_notebook(file_path)
    cells = project_notebook(notebook, cell_id="cell-1", include_outputs=False)

    assert len(cells) == 1
    assert cells[0]["cell_id"] == "calc"
    assert "outputs" not in cells[0]


def test_apply_notebook_edit_replaces_code_cell_and_clears_outputs() -> None:
    notebook = _notebook()

    result = apply_notebook_edit(
        notebook,
        cell_id="calc",
        new_source="print(2)\n",
        cell_type=None,
        edit_mode="replace",
    )

    cells = cast(list[Any], notebook["cells"])
    cell = cells[1]
    assert isinstance(cell, dict)
    assert result["cell_id"] == "calc"
    assert cell["source"] == "print(2)\n"
    assert cell["execution_count"] is None
    assert cell["outputs"] == []
    assert cell["metadata"] == {"tags": ["demo"]}


def test_apply_notebook_edit_converts_code_cell_to_markdown_cleanly() -> None:
    notebook = _notebook()

    result = apply_notebook_edit(
        notebook,
        cell_id="calc",
        new_source="Notes only\n",
        cell_type="markdown",
        edit_mode="replace",
    )

    cells = cast(list[Any], notebook["cells"])
    cell = cells[1]
    assert isinstance(cell, dict)
    assert result["cell_type"] == "markdown"
    assert cell["cell_type"] == "markdown"
    assert cell["source"] == "Notes only\n"
    assert "execution_count" not in cell
    assert "outputs" not in cell


def test_apply_notebook_edit_inserts_cell_after_id_with_generated_id() -> None:
    notebook = _notebook()

    result = apply_notebook_edit(
        notebook,
        cell_id="intro",
        new_source="Some notes",
        cell_type="markdown",
        edit_mode="insert",
    )

    cells = notebook["cells"]
    assert isinstance(cells, list)
    inserted = cells[1]
    assert isinstance(inserted, dict)
    assert result["cell_index"] == 1
    assert isinstance(inserted["id"], str)
    assert inserted["cell_type"] == "markdown"
    assert inserted["source"] == "Some notes"


def test_apply_notebook_edit_deletes_cell() -> None:
    notebook = _notebook()

    result = apply_notebook_edit(
        notebook,
        cell_id="cell-0",
        new_source="",
        cell_type=None,
        edit_mode="delete",
    )

    cells = notebook["cells"]
    assert isinstance(cells, list)
    assert result["cell_id"] == "intro"
    assert len(cells) == 1
    assert cells[0]["id"] == "calc"


def test_notebook_edit_file_requires_prior_read(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.ipynb"
    _write_notebook(file_path)
    shared_store = SharedStateRepository(tmp_path / "state.db")

    with pytest.raises(ValueError, match="read file before editing"):
        notebook_edit_file_with_guard(
            shared_store=shared_store,
            task_id="task-1",
            file_path=file_path,
            cell_id="calc",
            new_source="print(2)\n",
        )


def test_notebook_edit_file_rejects_external_change_after_read(tmp_path: Path) -> None:
    file_path = tmp_path / "demo.ipynb"
    _write_notebook(file_path)
    shared_store = SharedStateRepository(tmp_path / "state.db")
    record_file_read(shared_store=shared_store, task_id="task-1", path=file_path)
    _write_notebook(file_path, {**_notebook(), "metadata": {"changed": True}})

    with pytest.raises(ValueError, match="modified since it was last read"):
        notebook_edit_file_with_guard(
            shared_store=shared_store,
            task_id="task-1",
            file_path=file_path,
            cell_id="calc",
            new_source="print(2)\n",
        )


def test_notebook_edit_file_writes_cell_and_preserves_notebook_metadata(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "demo.ipynb"
    _write_notebook(file_path)
    shared_store = SharedStateRepository(tmp_path / "state.db")
    record_file_read(shared_store=shared_store, task_id="task-1", path=file_path)

    result = notebook_edit_file_with_guard(
        shared_store=shared_store,
        task_id="task-1",
        file_path=file_path,
        cell_id="calc",
        new_source="print(2)\n",
    )

    updated = json.loads(file_path.read_text(encoding="utf-8"))
    assert "Notebook edit applied successfully" in cast(str, result["output"])
    assert updated["metadata"] == {"language_info": {"name": "python"}}
    assert updated["cells"][1]["source"] == "print(2)\n"
    assert updated["cells"][1]["outputs"] == []


def test_load_notebook_empty_file_reports_empty_notebook(tmp_path: Path) -> None:
    file_path = tmp_path / "empty.ipynb"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Notebook file is empty"):
        load_notebook(file_path)


def test_format_invalid_notebook_output_includes_raw_preview(tmp_path: Path) -> None:
    file_path = tmp_path / "bad.ipynb"
    raw = "not notebook json"

    output, truncated = format_invalid_notebook_output(
        file_path=file_path,
        warning="Notebook is not valid JSON: Expecting value",
        content=raw,
    )

    assert truncated is False
    assert "Notebook native parsing failed" in output
    assert "Notebook is not valid JSON" in output
    assert raw in output

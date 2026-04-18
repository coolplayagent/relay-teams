from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeMarkItDownResult:
    def __init__(
        self,
        *,
        markdown: str | None,
        text_content: str | None,
    ) -> None:
        self.markdown = markdown
        self.text_content = text_content


class _FakeMarkItDown:
    def __init__(self, result: _FakeMarkItDownResult) -> None:
        self._result = result

    def convert(self, source: str | Path) -> _FakeMarkItDownResult:
        assert source
        return self._result


def test_convert_with_markitdown_uses_text_content_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools.markitdown_backend import (
        convert_with_markitdown,
    )

    fake_module = SimpleNamespace(
        MarkItDown=lambda: _FakeMarkItDown(
            _FakeMarkItDownResult(markdown="", text_content="fallback text")
        )
    )

    def _import_module(name: str) -> object:
        del name
        return fake_module

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.markitdown_backend.importlib.import_module",
        _import_module,
    )

    result = convert_with_markitdown(tmp_path / "report.docx")

    assert result.markdown == "fallback text"
    assert result.converter_name == "markitdown"
    assert result.source_extension == ".docx"


def test_convert_with_markitdown_reports_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import OfficeConversionError
    from relay_teams.tools.office_tools.markitdown_backend import (
        convert_with_markitdown,
    )

    def _raise_import_error(name: str) -> object:
        del name
        raise ImportError("missing markitdown")

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.markitdown_backend.importlib.import_module",
        _raise_import_error,
    )

    with pytest.raises(OfficeConversionError, match="dependencies are unavailable"):
        convert_with_markitdown(tmp_path / "report.pdf")


def test_convert_office_document_rejects_unsupported_extension(tmp_path: Path) -> None:
    from relay_teams.tools.office_tools import (
        OfficeConversionError,
        convert_office_document,
    )

    with pytest.raises(OfficeConversionError, match="Unsupported Office document"):
        convert_office_document(tmp_path / "legacy.doc")


def test_convert_office_document_trims_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import (
        OfficeConversionQuality,
        OfficeConversionResult,
    )
    from relay_teams.tools.office_tools.conversion import convert_office_document

    def _fake_convert(file_path: Path) -> OfficeConversionResult:
        return OfficeConversionResult(
            markdown="  # Title\nBody\n  ",
            converter_name="markitdown",
            source_extension=file_path.suffix.lower(),
            quality=OfficeConversionQuality(level="low", preserves_tables=False),
        )

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.conversion.convert_with_markitdown",
        _fake_convert,
    )

    result = convert_office_document(tmp_path / "report.pptx")

    assert result.markdown == "# Title\nBody"
    assert result.quality.level == "medium"
    assert result.quality.preserves_tables is False


def test_convert_office_document_marks_xlsx_as_high_fidelity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import OfficeConversionResult
    from relay_teams.tools.office_tools.conversion import convert_office_document

    def _fake_convert(file_path: Path) -> OfficeConversionResult:
        return OfficeConversionResult(
            markdown=("## Sheet1\n| Name | Score |\n| --- | --- |\n| Alice | 95 |"),
            converter_name="markitdown",
            source_extension=file_path.suffix.lower(),
        )

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.conversion.convert_with_markitdown",
        _fake_convert,
    )

    result = convert_office_document(tmp_path / "report.xlsx")

    assert result.quality.level == "high"
    assert result.quality.preserves_tables is True
    assert result.warnings == ()


def test_convert_office_document_raises_ocr_for_blank_pdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import (
        OfficeConversionRequiresOcrError,
        OfficeConversionResult,
    )
    from relay_teams.tools.office_tools.conversion import convert_office_document

    def _fake_convert(file_path: Path) -> OfficeConversionResult:
        return OfficeConversionResult(
            markdown="   ",
            converter_name="markitdown",
            source_extension=file_path.suffix.lower(),
        )

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.conversion.convert_with_markitdown",
        _fake_convert,
    )

    with pytest.raises(OfficeConversionRequiresOcrError, match="requires OCR"):
        convert_office_document(tmp_path / "scanned.pdf")


def test_convert_office_document_adds_pdf_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import OfficeConversionResult
    from relay_teams.tools.office_tools.conversion import convert_office_document

    def _fake_convert(file_path: Path) -> OfficeConversionResult:
        return OfficeConversionResult(
            markdown="Quarterly report\nRevenue increased 12%",
            converter_name="markitdown",
            source_extension=file_path.suffix.lower(),
        )

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.conversion.convert_with_markitdown",
        _fake_convert,
    )

    result = convert_office_document(tmp_path / "report.pdf")

    assert result.quality.level == "medium"
    assert result.quality.preserves_tables is False
    assert result.warnings == ("PDF layout reconstruction may be approximate.",)


def test_convert_office_document_raises_error_for_blank_docx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import (
        OfficeConversionError,
        OfficeConversionResult,
    )
    from relay_teams.tools.office_tools.conversion import convert_office_document

    def _fake_convert(file_path: Path) -> OfficeConversionResult:
        return OfficeConversionResult(
            markdown="   ",
            converter_name="markitdown",
            source_extension=file_path.suffix.lower(),
        )

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.conversion.convert_with_markitdown",
        _fake_convert,
    )

    with pytest.raises(OfficeConversionError, match="produced no readable markdown"):
        convert_office_document(tmp_path / "empty.docx")


def test_paginate_office_document_markdown_streams_without_full_markdown_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import paginate_office_document_markdown

    consumed: list[str] = []

    def _fake_stream(file_path: Path, *, on_line) -> None:
        assert file_path == tmp_path / "report.xlsx"
        for line in (
            "## Sheet1",
            "| Name | Score |",
            "| --- | --- |",
            "| Alice | 95 |",
            "| Bob | 88 |",
        ):
            consumed.append(line)
            on_line(line)

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.conversion.stream_markdown_with_markitdown",
        _fake_stream,
    )

    result = paginate_office_document_markdown(
        tmp_path / "report.xlsx",
        offset=2,
        limit=2,
        max_bytes=50 * 1024,
        max_line_length=2000,
        max_line_suffix="... (line truncated)",
    )

    assert consumed == [
        "## Sheet1",
        "| Name | Score |",
        "| --- | --- |",
        "| Alice | 95 |",
        "| Bob | 88 |",
    ]
    assert result.lines == ("| Name | Score |", "| --- | --- |")
    assert result.total_lines == 5
    assert result.truncated_by_lines is True
    assert result.truncated_by_bytes is False
    assert result.converter_name == "markitdown"
    assert result.quality.level == "high"
    assert result.quality.preserves_tables is True
    assert result.warnings == ()


def test_paginate_office_document_markdown_raises_ocr_for_empty_pdf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.tools.office_tools import (
        OfficeConversionRequiresOcrError,
        paginate_office_document_markdown,
    )

    def _fake_stream(file_path: Path, *, on_line) -> None:
        del file_path, on_line

    monkeypatch.setattr(
        "relay_teams.tools.office_tools.conversion.stream_markdown_with_markitdown",
        _fake_stream,
    )

    with pytest.raises(OfficeConversionRequiresOcrError, match="requires OCR"):
        paginate_office_document_markdown(
            tmp_path / "scanned.pdf",
            offset=1,
            limit=10,
            max_bytes=50 * 1024,
            max_line_length=2000,
            max_line_suffix="... (line truncated)",
        )

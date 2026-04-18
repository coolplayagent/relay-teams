from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re

from relay_teams.tools.office_tools.markitdown_backend import (
    MARKITDOWN_CONVERTER_NAME,
    convert_with_markitdown,
    stream_markdown_with_markitdown,
)
from relay_teams.tools.office_tools.models import (
    OfficeConversionError,
    OfficeConversionPage,
    OfficeConversionQuality,
    OfficeConversionRequiresOcrError,
    OfficeConversionResult,
)

SUPPORTED_OFFICE_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
    }
)

_TABLE_ROW_PATTERN = re.compile(r"^\|.*\|$")
_TABLE_SEPARATOR_PATTERN = re.compile(r"^\|\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|$")


def convert_office_document(file_path: Path) -> OfficeConversionResult:
    source_extension = file_path.suffix.lower()
    _validate_supported_extension(file_path=file_path)
    result = convert_with_markitdown(file_path)
    normalized_markdown = result.markdown.strip()
    if normalized_markdown:
        quality = _classify_conversion_quality(
            source_extension=source_extension,
            markdown=normalized_markdown,
        )
        warnings = _build_conversion_warnings(
            source_extension=source_extension,
            markdown=normalized_markdown,
        )
        return result.model_copy(
            update={
                "markdown": normalized_markdown,
                "quality": quality,
                "warnings": (*result.warnings, *warnings),
            }
        )
    if source_extension == ".pdf":
        raise OfficeConversionRequiresOcrError(
            f"PDF requires OCR before it can be read as markdown: {file_path.name}"
        )
    raise OfficeConversionError(
        f"Document conversion produced no readable markdown: {file_path.name}"
    )


def paginate_office_document_markdown(
    file_path: Path,
    *,
    offset: int,
    limit: int,
    max_bytes: int,
    max_line_length: int,
    max_line_suffix: str,
) -> OfficeConversionPage:
    if offset <= 0:
        raise ValueError("offset must be greater than 0")
    if limit <= 0:
        raise ValueError("limit must be greater than 0")
    _validate_supported_extension(file_path=file_path)

    streamed_page = _paginate_streamed_markdown(
        file_path=file_path,
        offset=offset,
        limit=limit,
        max_bytes=max_bytes,
        max_line_length=max_line_length,
        max_line_suffix=max_line_suffix,
        streamer=_stream_markitdown_markdown,
    )
    return streamed_page


def _paginate_streamed_markdown(
    *,
    file_path: Path,
    offset: int,
    limit: int,
    max_bytes: int,
    max_line_length: int,
    max_line_suffix: str,
    streamer: Callable[..., str],
) -> OfficeConversionPage:
    start_offset = offset - 1
    lines: list[str] = []
    total_lines = 0
    bytes_count = 0
    truncated_by_lines = False
    truncated_by_bytes = False
    contains_markdown_table = False
    previous_nonempty_line: str | None = None

    def _consume_line(raw_line: str) -> None:
        nonlocal bytes_count
        nonlocal contains_markdown_table
        nonlocal previous_nonempty_line
        nonlocal total_lines
        nonlocal truncated_by_bytes
        nonlocal truncated_by_lines

        total_lines += 1
        stripped_line = raw_line.strip()
        if (
            previous_nonempty_line is not None
            and _TABLE_ROW_PATTERN.match(previous_nonempty_line)
            and _TABLE_SEPARATOR_PATTERN.match(stripped_line)
        ):
            contains_markdown_table = True
        if stripped_line:
            previous_nonempty_line = stripped_line

        if total_lines <= start_offset:
            return
        if len(lines) >= limit:
            truncated_by_lines = True
            return

        line = raw_line
        if len(line) > max_line_length:
            line = line[:max_line_length] + max_line_suffix

        line_size = len(line.encode("utf-8"))
        if bytes_count + line_size > max_bytes:
            truncated_by_bytes = True
            return

        lines.append(line)
        bytes_count += line_size

    converter_name = streamer(file_path, on_line=_consume_line)
    if total_lines == 0:
        _raise_no_markdown_error(
            file_path=file_path,
            source_extension=file_path.suffix.lower(),
        )

    quality = _classify_conversion_quality_from_table_flag(
        source_extension=file_path.suffix.lower(),
        contains_markdown_table=contains_markdown_table,
    )
    warnings = _build_conversion_warnings_from_table_flag(
        source_extension=file_path.suffix.lower(),
        contains_markdown_table=contains_markdown_table,
    )
    return OfficeConversionPage(
        lines=tuple(lines),
        total_lines=total_lines,
        truncated_by_lines=truncated_by_lines,
        truncated_by_bytes=truncated_by_bytes,
        converter_name=converter_name,
        quality=quality,
        warnings=warnings,
    )


def _stream_markitdown_markdown(
    file_path: Path,
    *,
    on_line: Callable[[str], None],
) -> str:
    stream_markdown_with_markitdown(file_path, on_line=on_line)
    return MARKITDOWN_CONVERTER_NAME


def _classify_conversion_quality(
    *,
    source_extension: str,
    markdown: str,
) -> OfficeConversionQuality:
    contains_markdown_table = _contains_markdown_table(markdown)
    return _classify_conversion_quality_from_table_flag(
        source_extension=source_extension,
        contains_markdown_table=contains_markdown_table,
    )


def _classify_conversion_quality_from_table_flag(
    *,
    source_extension: str,
    contains_markdown_table: bool,
) -> OfficeConversionQuality:

    if source_extension == ".xlsx":
        return OfficeConversionQuality(level="high", preserves_tables=True)
    if source_extension == ".pdf":
        level = "low" if contains_markdown_table else "medium"
        return OfficeConversionQuality(
            level=level,
            preserves_tables=contains_markdown_table,
        )
    return OfficeConversionQuality(
        level="medium",
        preserves_tables=contains_markdown_table,
    )


def _build_conversion_warnings(
    *,
    source_extension: str,
    markdown: str,
) -> tuple[str, ...]:
    return _build_conversion_warnings_from_table_flag(
        source_extension=source_extension,
        contains_markdown_table=_contains_markdown_table(markdown),
    )


def _build_conversion_warnings_from_table_flag(
    *,
    source_extension: str,
    contains_markdown_table: bool,
) -> tuple[str, ...]:
    if source_extension != ".pdf":
        return ()

    warnings = ["PDF layout reconstruction may be approximate."]
    if contains_markdown_table:
        warnings.append(
            "PDF table formatting is heuristic and should be verified against the source."
        )
    return tuple(warnings)


def _contains_markdown_table(markdown: str) -> bool:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    for index in range(len(lines) - 1):
        if _TABLE_ROW_PATTERN.match(lines[index]) and _TABLE_SEPARATOR_PATTERN.match(
            lines[index + 1]
        ):
            return True
    return False


def _validate_supported_extension(*, file_path: Path) -> None:
    if file_path.suffix.lower() in SUPPORTED_OFFICE_EXTENSIONS:
        return
    raise OfficeConversionError(f"Unsupported Office document type: {file_path.name}")


def _raise_no_markdown_error(*, file_path: Path, source_extension: str) -> None:
    if source_extension == ".pdf":
        raise OfficeConversionRequiresOcrError(
            f"PDF requires OCR before it can be read as markdown: {file_path.name}"
        )
    raise OfficeConversionError(
        f"Document conversion produced no readable markdown: {file_path.name}"
    )

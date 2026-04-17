from __future__ import annotations

from pathlib import Path
import re

from relay_teams.tools.office_tools.markitdown_backend import convert_with_markitdown
from relay_teams.tools.office_tools.models import (
    OfficeConversionError,
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
    if source_extension not in SUPPORTED_OFFICE_EXTENSIONS:
        raise OfficeConversionError(
            f"Unsupported Office document type: {file_path.name}"
        )
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


def _classify_conversion_quality(
    *,
    source_extension: str,
    markdown: str,
) -> OfficeConversionQuality:
    contains_markdown_table = _contains_markdown_table(markdown)

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
    if source_extension != ".pdf":
        return ()

    warnings = ["PDF layout reconstruction may be approximate."]
    if _contains_markdown_table(markdown):
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

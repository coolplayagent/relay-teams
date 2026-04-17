from __future__ import annotations

import importlib
from pathlib import Path
from typing import Protocol, cast

from relay_teams.tools.office_tools.models import (
    OfficeConversionError,
    OfficeConversionResult,
)

_MARKITDOWN_MODULE = "markitdown"
_CONVERTER_NAME = "markitdown"


class _MarkItDownResult(Protocol):
    markdown: str | None
    text_content: str | None


class _MarkItDownConverter(Protocol):
    def convert(self, source: str | Path) -> _MarkItDownResult: ...


def convert_with_markitdown(file_path: Path) -> OfficeConversionResult:
    converter = _load_markitdown_converter()
    try:
        result = converter.convert(file_path)
    except Exception as exc:  # pragma: no cover - external library behavior
        raise OfficeConversionError(
            f"Failed to convert document to markdown: {file_path.name}"
        ) from exc
    markdown = _extract_markdown(result)
    return OfficeConversionResult(
        markdown=markdown,
        converter_name=_CONVERTER_NAME,
        source_extension=file_path.suffix.lower(),
    )


def _load_markitdown_converter() -> _MarkItDownConverter:
    try:
        module = importlib.import_module(_MARKITDOWN_MODULE)
    except ImportError as exc:
        raise OfficeConversionError(
            "Document conversion dependencies are unavailable. "
            "Install relay-teams with markitdown office extras."
        ) from exc
    converter_cls = getattr(module, "MarkItDown", None)
    if not callable(converter_cls):
        raise OfficeConversionError(
            "Document conversion backend is unavailable: markitdown.MarkItDown"
        )
    return cast(_MarkItDownConverter, converter_cls())


def _extract_markdown(result: _MarkItDownResult) -> str:
    markdown = str(result.markdown or "").strip()
    if markdown:
        return markdown
    return str(result.text_content or "").strip()

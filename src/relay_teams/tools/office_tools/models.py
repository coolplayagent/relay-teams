from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OfficeConversionQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: Literal["high", "medium", "low"] = Field()
    preserves_tables: bool = False


def _default_conversion_quality() -> OfficeConversionQuality:
    return OfficeConversionQuality(level="medium", preserves_tables=False)


class OfficeConversionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    markdown: str = Field()
    converter_name: str = Field(min_length=1)
    source_extension: str = Field(min_length=1)
    quality: OfficeConversionQuality = Field(
        default_factory=_default_conversion_quality
    )
    ocr_required: bool = False
    ocr_reason: str | None = None
    warnings: tuple[str, ...] = ()


class OfficeConversionPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lines: tuple[str, ...] = ()
    total_lines: int = Field(ge=0)
    truncated_by_lines: bool = False
    truncated_by_bytes: bool = False
    converter_name: str = Field(min_length=1)
    quality: OfficeConversionQuality = Field(
        default_factory=_default_conversion_quality
    )
    warnings: tuple[str, ...] = ()


class OfficeConversionError(ValueError):
    """Raised when an Office or PDF document cannot be converted to Markdown."""


class OfficeConversionRequiresOcrError(OfficeConversionError):
    """Raised when a PDF appears to require OCR before text extraction."""

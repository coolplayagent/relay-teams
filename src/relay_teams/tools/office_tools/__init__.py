from __future__ import annotations

from relay_teams.tools.office_tools.conversion import (
    SUPPORTED_OFFICE_EXTENSIONS,
    convert_office_document,
    paginate_office_document_markdown,
)
from relay_teams.tools.office_tools.models import (
    OfficeConversionError,
    OfficeConversionPage,
    OfficeConversionQuality,
    OfficeConversionRequiresOcrError,
    OfficeConversionResult,
)

__all__ = [
    "OfficeConversionError",
    "OfficeConversionPage",
    "OfficeConversionQuality",
    "OfficeConversionRequiresOcrError",
    "OfficeConversionResult",
    "SUPPORTED_OFFICE_EXTENSIONS",
    "convert_office_document",
    "paginate_office_document_markdown",
]

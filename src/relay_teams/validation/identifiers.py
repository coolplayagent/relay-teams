from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BeforeValidator

NONE_LIKE_IDENTIFIER_TEXT = frozenset({"none", "null"})


def is_none_like_identifier_text(value: str) -> bool:
    return value.casefold() in NONE_LIKE_IDENTIFIER_TEXT


def _normalize_required_identifier(value: object) -> object:
    if value is None:
        raise ValueError("Identifier is required")
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized or is_none_like_identifier_text(normalized):
            raise ValueError("Identifier cannot be blank, 'None', or 'null'")
        return normalized
    return value


def _normalize_optional_identifier(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized or is_none_like_identifier_text(normalized):
            raise ValueError("Identifier cannot be blank, 'None', or 'null'")
        return normalized
    return value


RequiredIdentifierStr = Annotated[str, BeforeValidator(_normalize_required_identifier)]
OptionalIdentifierStr = Annotated[
    str | None, BeforeValidator(_normalize_optional_identifier)
]


def normalize_persisted_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or is_none_like_identifier_text(normalized):
        return None
    return normalized


def parse_persisted_datetime_or_none(value: object) -> datetime | None:
    normalized = normalize_persisted_text(value)
    if normalized is None:
        return None
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def require_persisted_identifier(value: object, *, field_name: str) -> str:
    normalized = normalize_persisted_text(value)
    if normalized is None:
        raise ValueError(f"Invalid persisted {field_name}")
    return normalized

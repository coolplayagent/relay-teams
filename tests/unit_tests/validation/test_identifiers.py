from __future__ import annotations

from pydantic import BaseModel, ValidationError
import pytest

from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)


class _RequiredIdentifierModel(BaseModel):
    value: RequiredIdentifierStr


class _OptionalIdentifierModel(BaseModel):
    value: OptionalIdentifierStr = None


@pytest.mark.parametrize("raw_value", ["", "   ", "None", "none", "Null", " null "])
def test_required_identifier_rejects_blank_and_none_like_strings(
    raw_value: str,
) -> None:
    with pytest.raises(ValidationError):
        _RequiredIdentifierModel(value=raw_value)


def test_required_identifier_strips_whitespace() -> None:
    model = _RequiredIdentifierModel(value="  session-1  ")

    assert model.value == "session-1"


@pytest.mark.parametrize("raw_value", ["", "   ", "None", "NULL"])
def test_optional_identifier_rejects_explicit_blank_and_none_like_strings(
    raw_value: str,
) -> None:
    with pytest.raises(ValidationError):
        _OptionalIdentifierModel(value=raw_value)


def test_optional_identifier_allows_real_none() -> None:
    model = _OptionalIdentifierModel(value=None)

    assert model.value is None


def test_persisted_helpers_normalize_none_like_values() -> None:
    assert normalize_persisted_text(" None ") is None
    assert normalize_persisted_text(" value ") == "value"
    assert require_persisted_identifier(" run-1 ", field_name="run_id") == "run-1"
    with pytest.raises(ValueError, match="Invalid persisted run_id"):
        require_persisted_identifier("null", field_name="run_id")


def test_parse_persisted_datetime_or_none_rejects_invalid_isoformat() -> None:
    assert parse_persisted_datetime_or_none("None") is None
    assert parse_persisted_datetime_or_none("not-a-datetime") is None
    assert parse_persisted_datetime_or_none("2026-03-30T06:52:47+00:00") is not None

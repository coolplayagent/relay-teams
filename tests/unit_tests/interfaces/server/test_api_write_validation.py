from __future__ import annotations

import pytest

from relay_teams.interfaces.server.api_write_validation import (
    normalize_optional_text_field,
    reject_empty_mapping_patch,
    require_cascade_delete,
    require_force_delete,
    require_non_empty_patch,
)


class _FakePatch:
    model_fields_set: set[str]

    def __init__(self, fields: set[str]) -> None:
        self.model_fields_set = fields


def test_require_non_empty_patch_rejects_empty_fields() -> None:
    with pytest.raises(ValueError, match="update must include at least one field"):
        require_non_empty_patch(_FakePatch(set()))


def test_normalize_optional_text_field_supports_empty_to_none() -> None:
    assert (
        normalize_optional_text_field(
            "   ",
            field_name="display_name",
            empty_to_none=True,
        )
        is None
    )


def test_reject_empty_mapping_patch_rejects_empty_dict() -> None:
    with pytest.raises(ValueError, match="config patch must not be empty"):
        reject_empty_mapping_patch({}, message="config patch must not be empty")


def test_require_force_delete_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="force required"):
        require_force_delete(False, message="force required")


def test_require_cascade_delete_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="cascade required"):
        require_cascade_delete(False, message="cascade required")

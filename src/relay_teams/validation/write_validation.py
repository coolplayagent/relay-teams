from __future__ import annotations

from typing import Optional, Protocol, Set


class _HasModelFieldsSet(Protocol):
    @property
    def model_fields_set(self) -> Set[str]:
        raise NotImplementedError


def require_non_empty_patch(
    model: _HasModelFieldsSet,
    *,
    message: str = "update must include at least one field",
) -> _HasModelFieldsSet:
    if not model.model_fields_set:
        raise ValueError(message)
    return model


def normalize_optional_text_field(
    value: object,
    *,
    field_name: str,
    empty_to_none: bool = False,
) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if normalized:
        return normalized
    if empty_to_none:
        return None
    raise ValueError(f"{field_name} must not be empty")


def normalize_optional_string(
    value: Optional[str],
    *,
    field_name: str,
    empty_to_none: bool = False,
) -> Optional[str]:
    normalized = normalize_optional_text_field(
        value,
        field_name=field_name,
        empty_to_none=empty_to_none,
    )
    if normalized is None or isinstance(normalized, str):
        return normalized
    raise TypeError(f"{field_name} must be a string or None")


def reject_empty_mapping_patch(value: object, *, message: str) -> object:
    if isinstance(value, dict) and not value:
        raise ValueError(message)
    return value


def require_force_delete(force: bool, *, message: str) -> None:
    if not force:
        raise RuntimeError(message)


def require_cascade_delete(cascade: bool, *, message: str) -> None:
    if not cascade:
        raise RuntimeError(message)

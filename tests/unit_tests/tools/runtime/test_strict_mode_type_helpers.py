# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from relay_teams.tools.runtime.execution import (
    _enum_type_from_annotation,
    _resolve_pydantic_model_type,
)


class _SampleModel(BaseModel):
    name: str
    value: int


class _SampleEnum(Enum):
    ALPHA = "alpha"
    BETA = "beta"


class TestResolvePydanticModelType:
    def test_direct_basemodel_subclass(self) -> None:
        result = _resolve_pydantic_model_type(_SampleModel)
        assert result is _SampleModel

    def test_optional_basemodel(self) -> None:
        result = _resolve_pydantic_model_type(Optional[_SampleModel])  # type: ignore[arg-type]
        assert result is _SampleModel

    def test_non_model_returns_none(self) -> None:
        result = _resolve_pydantic_model_type(str)
        assert result is None


class TestEnumTypeFromAnnotation:
    def test_direct_enum_subclass(self) -> None:
        result = _enum_type_from_annotation(_SampleEnum)
        assert result is _SampleEnum

    def test_optional_enum(self) -> None:
        result = _enum_type_from_annotation(Optional[_SampleEnum])  # type: ignore[arg-type]
        assert result is _SampleEnum

    def test_non_enum_returns_none(self) -> None:
        result = _enum_type_from_annotation(str)
        assert result is None

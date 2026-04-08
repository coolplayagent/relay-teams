# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SecretCoordinate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1)


class SecretIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    storage: Literal["keyring", "file"]
    value: str | None = None

    def coordinate(self) -> SecretCoordinate:
        return SecretCoordinate(
            namespace=self.namespace,
            owner_id=self.owner_id,
            field_name=self.field_name,
        )


class SecretIndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    entries: tuple[SecretIndexEntry, ...] = ()

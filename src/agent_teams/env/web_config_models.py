# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class WebProvider(str, Enum):
    EXA = "exa"


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: WebProvider = WebProvider.EXA
    api_key: str | None = None

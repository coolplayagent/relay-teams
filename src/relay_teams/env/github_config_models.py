# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class GitHubConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = None

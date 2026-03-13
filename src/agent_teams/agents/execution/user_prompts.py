# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserPromptBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str = Field(min_length=1)


def build_user_prompt(data: UserPromptBuildInput) -> str:
    return data.objective

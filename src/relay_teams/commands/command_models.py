# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class CommandScope(str, Enum):
    APP = "app"
    PROJECT = "project"


class CommandFrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    argument_hint: str = ""
    allowed_modes: list[str] = Field(default_factory=lambda: ["normal"])


class CommandEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    argument_hint: str = ""
    allowed_modes: list[str] = Field(default_factory=lambda: ["normal"])
    body: str = ""
    scope: CommandScope
    path: Path


class CommandSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    scope: CommandScope
    argument_hint: str = ""


class ResolveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_name: str = Field(min_length=1)
    scope: CommandScope
    raw_text: str = Field(min_length=1)
    expanded_prompt: str = Field(min_length=1)
    args: str = ""
    prompt_length: int = 0

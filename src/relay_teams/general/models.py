# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class GeneralConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shell_safety_policy_enabled: bool = True


class GeneralConfigUpdate(GeneralConfig):
    model_config = ConfigDict(extra="forbid")

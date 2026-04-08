# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class UiLanguage(str, Enum):
    EN_US = "en-US"
    ZH_CN = "zh-CN"


class UiLanguageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: UiLanguage = UiLanguage.ZH_CN

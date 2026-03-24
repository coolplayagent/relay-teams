# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from agent_teams.sessions.runs.run_models import RunThinkingConfig
from agent_teams.sessions.session_models import SessionMode

FEISHU_PLATFORM = "feishu"
FEISHU_METADATA_PLATFORM_KEY = "feishu_platform"
FEISHU_METADATA_TENANT_KEY = "feishu_tenant_key"
FEISHU_METADATA_CHAT_ID_KEY = "feishu_chat_id"
FEISHU_METADATA_CHAT_TYPE_KEY = "feishu_chat_type"
FEISHU_METADATA_TRIGGER_ID_KEY = "feishu_trigger_id"


class FeishuMessageFormat(str, Enum):
    TEXT = "text"
    CARD = "card"


class FeishuEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str = Field(min_length=1)
    app_secret: str = Field(min_length=1)
    app_name: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None


class FeishuNotificationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_key: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    chat_type: str = Field(min_length=1)


class FeishuTriggerSecretConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_secret: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None


class FeishuTriggerSecretStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_secret_configured: bool = False
    verification_token_configured: bool = False
    encrypt_key_configured: bool = False


class FeishuTriggerSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["feishu"] = FEISHU_PLATFORM
    trigger_rule: Literal["mention_only", "all_messages"] = "mention_only"
    app_id: str = Field(min_length=1)
    app_name: str = Field(min_length=1)


class FeishuTriggerTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(default="default", min_length=1)
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: str | None = None
    orchestration_preset_id: str | None = None
    yolo: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)

    @model_validator(mode="after")
    def _validate_mode_settings(self) -> FeishuTriggerTargetConfig:
        if (
            self.session_mode == SessionMode.ORCHESTRATION
            and not str(self.orchestration_preset_id or "").strip()
        ):
            raise ValueError(
                "orchestration_preset_id is required in orchestration mode"
            )
        return self


class FeishuTriggerRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger_id: str = Field(min_length=1)
    trigger_name: str = Field(min_length=1)
    source: FeishuTriggerSourceConfig
    target: FeishuTriggerTargetConfig
    environment: FeishuEnvironment

    @property
    def signature(self) -> tuple[str, str, str | None, str | None]:
        return (
            self.environment.app_id,
            self.environment.app_secret,
            self.environment.verification_token,
            self.environment.encrypt_key,
        )


class FeishuNormalizedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    tenant_key: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    chat_type: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    message_type: str = Field(min_length=1)
    sender_type: str | None = None
    sender_open_id: str | None = None
    raw_text: str = ""
    trigger_text: str = ""
    mentioned: bool = False
    mention_names: tuple[str, ...] = ()
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)


class TriggerProcessingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1)
    trigger_id: str | None = None
    trigger_name: str | None = None
    event_id: str | None = None
    duplicate: bool = False
    session_id: str | None = None
    run_id: str | None = None
    ignored: bool = False
    reason: str | None = None

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_teams.media import ContentPart
from agent_teams.media import ContentPartsAdapter
from agent_teams.media import content_parts_from_text
from agent_teams.media import content_parts_to_text
from agent_teams.sessions.runs.enums import (
    ExecutionMode,
    InjectionSource,
    RunEventType,
)
from agent_teams.sessions.session_models import SessionMode


class RunKind(str, Enum):
    CONVERSATION = "conversation"
    GENERATE_IMAGE = "generate_image"
    GENERATE_AUDIO = "generate_audio"
    GENERATE_VIDEO = "generate_video"


class RunThinkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    effort: Literal["minimal", "low", "medium", "high"] | None = None


class RunTopologySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_mode: SessionMode
    main_agent_role_id: str = Field(min_length=1)
    normal_root_role_id: str = Field(min_length=1)
    coordinator_role_id: str = Field(min_length=1)
    orchestration_preset_id: str | None = None
    orchestration_prompt: str = ""
    allowed_role_ids: tuple[str, ...] = ()


class RuntimePromptConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_provider: str | None = None
    source_kind: str | None = None
    feishu_chat_type: str | None = None
    im_force_direct_send: bool = False


class ImageGenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["image"] = "image"
    count: int = Field(default=1, ge=1, le=8)
    size: str | None = None
    seed: int | None = None


class AudioGenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["audio"] = "audio"
    count: int = Field(default=1, ge=1, le=8)
    voice: str | None = None
    format: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    seed: int | None = None


class VideoGenerationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["video"] = "video"
    count: int = Field(default=1, ge=1, le=4)
    resolution: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    seed: int | None = None


MediaGenerationConfig = (
    ImageGenerationConfig | AudioGenerationConfig | VideoGenerationConfig
)


class IntentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    input: tuple[ContentPart, ...] = Field(default_factory=tuple)
    run_kind: RunKind = RunKind.CONVERSATION
    generation_config: MediaGenerationConfig | None = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = False
    reuse_root_instance: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    target_role_id: str | None = None
    session_mode: SessionMode = SessionMode.NORMAL
    topology: RunTopologySnapshot | None = None
    conversation_context: RuntimePromptConversationContext | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_intent(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "input" in payload:
            return payload
        legacy_intent = payload.pop("intent", None)
        if isinstance(legacy_intent, str):
            payload["input"] = content_parts_from_text(legacy_intent)
        return payload

    @property
    def intent(self) -> str:
        return content_parts_to_text(self.input)

    @intent.setter
    def intent(self, value: str) -> None:
        self.input = content_parts_from_text(value)


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    root_task_id: str
    status: Literal["completed", "failed"]
    output: tuple[ContentPart, ...] = Field(default_factory=tuple)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_output(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        raw_output = payload.get("output")
        if isinstance(raw_output, str):
            payload["output"] = content_parts_from_text(raw_output)
            return payload
        if isinstance(raw_output, tuple):
            return payload
        if isinstance(raw_output, list):
            payload["output"] = ContentPartsAdapter.validate_python(tuple(raw_output))
        return payload

    @property
    def output_text(self) -> str:
        return content_parts_to_text(self.output)


class InjectionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    recipient_instance_id: str = Field(min_length=1)
    source: InjectionSource
    content: str = Field(min_length=1)
    sender_instance_id: str | None = None
    sender_role_id: str | None = None
    priority: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str | None = None
    instance_id: str | None = None
    role_id: str | None = None
    event_type: RunEventType
    payload_json: str = Field(default="{}")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    event_id: int | None = None

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay_teams.media import ContentPart
from relay_teams.media import ContentPartsAdapter
from relay_teams.media import UserPromptContent
from relay_teams.media import content_parts_from_text
from relay_teams.media import content_parts_to_text
from relay_teams.media import user_prompt_content_to_text
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.enums import (
    ExecutionMode,
    InjectionSource,
    RunEventType,
)
from relay_teams.sessions.session_models import SessionMode
from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_identifier_tuple,
)


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
    main_agent_role_id: RequiredIdentifierStr
    normal_root_role_id: RequiredIdentifierStr
    coordinator_role_id: RequiredIdentifierStr
    orchestration_preset_id: OptionalIdentifierStr = None
    orchestration_prompt: str = ""
    allowed_role_ids: tuple[str, ...] = ()


class RuntimePromptConversationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_provider: str | None = None
    source_kind: str | None = None
    feishu_chat_type: str | None = None
    im_force_direct_send: bool = False
    im_reply_to_message_id: str | None = None


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


MediaGenerationConfig: TypeAlias = (
    ImageGenerationConfig | AudioGenerationConfig | VideoGenerationConfig
)


class IntentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: RequiredIdentifierStr
    input: tuple[ContentPart, ...] = Field(default_factory=tuple)
    display_input: tuple[ContentPart, ...] = Field(default_factory=tuple)
    run_kind: RunKind = RunKind.CONVERSATION
    generation_config: MediaGenerationConfig | None = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = False
    reuse_root_instance: bool = True
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    target_role_id: OptionalIdentifierStr = None
    skills: Optional[tuple[str, ...]] = None
    session_mode: SessionMode = SessionMode.NORMAL
    topology: RunTopologySnapshot | None = None
    conversation_context: RuntimePromptConversationContext | None = None

    @field_validator("skills", mode="before")
    @classmethod
    def _normalize_skills(cls, value: object) -> Optional[tuple[str, ...]]:
        return normalize_identifier_tuple(value, field_name="skills")

    @property
    def intent(self) -> str:
        return content_parts_to_text(self.input)

    @intent.setter
    def intent(self, value: str) -> None:
        self.input = content_parts_from_text(value)
        self.display_input = ()

    @property
    def display_intent(self) -> str:
        return content_parts_to_text(self.display_input or self.input)


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: RequiredIdentifierStr
    root_task_id: RequiredIdentifierStr
    status: Literal["completed", "failed"]
    completion_reason: RunCompletionReason = RunCompletionReason.ASSISTANT_RESPONSE
    error_code: str | None = None
    error_message: str | None = None
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

    run_id: RequiredIdentifierStr
    recipient_instance_id: RequiredIdentifierStr
    source: InjectionSource
    # noinspection PyTypeHints
    content: UserPromptContent
    sender_instance_id: OptionalIdentifierStr = None
    sender_role_id: OptionalIdentifierStr = None
    priority: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="after")
    def _validate_content(self) -> "InjectionMessage":
        if not user_prompt_content_to_text(self.content):
            raise ValueError("Injection content must not be empty")
        return self


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: RequiredIdentifierStr
    run_id: RequiredIdentifierStr
    trace_id: RequiredIdentifierStr
    task_id: OptionalIdentifierStr = None
    instance_id: OptionalIdentifierStr = None
    role_id: OptionalIdentifierStr = None
    event_type: RunEventType
    payload_json: str = Field(default="{}")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    event_id: int | None = None

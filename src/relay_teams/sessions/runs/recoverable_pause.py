# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.providers.llm_retry import LlmRetryErrorInfo
from relay_teams.providers.provider_contracts import LLMRequest


class RecoverableRunPausePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    error_code: str = ""
    error_message: str = Field(min_length=1)
    retries_used: int = Field(default=0, ge=0)
    total_attempts: int = Field(default=1, ge=1)

    @classmethod
    def from_request(
        cls,
        *,
        request: LLMRequest,
        error: LlmRetryErrorInfo,
        retries_used: int,
        total_attempts: int,
        error_message: str | None = None,
    ) -> RecoverableRunPausePayload:
        resolved_error_message = str(error_message or error.message).strip()
        return cls(
            run_id=request.run_id,
            trace_id=request.trace_id,
            task_id=request.task_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            error_code=str(error.error_code or "").strip(),
            error_message=resolved_error_message
            or "Run paused due to a recoverable error.",
            retries_used=max(0, retries_used),
            total_attempts=max(1, total_attempts),
        )


class RecoverableRunPauseError(RuntimeError):
    def __init__(self, payload: RecoverableRunPausePayload) -> None:
        self.payload = payload
        super().__init__(payload.error_message)

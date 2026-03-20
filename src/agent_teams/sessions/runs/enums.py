from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    AI = "ai"
    MANUAL = "manual"


class ApprovalMode(str, Enum):
    STANDARD = "standard"
    YOLO = "yolo"


class InjectionSource(str, Enum):
    SYSTEM = "system"
    USER = "user"
    SUBAGENT = "subagent"


class RunEventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    LLM_RETRY_SCHEDULED = "llm_retry_scheduled"
    LLM_RETRY_EXHAUSTED = "llm_retry_exhausted"
    MODEL_STEP_STARTED = "model_step_started"
    MODEL_STEP_FINISHED = "model_step_finished"
    TEXT_DELTA = "text_delta"
    THINKING_STARTED = "thinking_started"
    THINKING_DELTA = "thinking_delta"
    THINKING_FINISHED = "thinking_finished"
    TOOL_CALL = "tool_call"
    TOOL_INPUT_VALIDATION_FAILED = "tool_input_validation_failed"
    TOOL_RESULT = "tool_result"
    INJECTION_ENQUEUED = "injection_enqueued"
    INJECTION_APPLIED = "injection_applied"
    TOOL_APPROVAL_REQUESTED = "tool_approval_requested"
    TOOL_APPROVAL_RESOLVED = "tool_approval_resolved"
    NOTIFICATION_REQUESTED = "notification_requested"
    SUBAGENT_STOPPED = "subagent_stopped"
    SUBAGENT_RESUMED = "subagent_resumed"
    RUN_STOPPED = "run_stopped"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    AWAITING_MANUAL_ACTION = "awaiting_manual_action"
    TOKEN_USAGE = "token_usage"

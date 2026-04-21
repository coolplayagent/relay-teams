from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    AI = "ai"
    MANUAL = "manual"


class InjectionSource(str, Enum):
    SYSTEM = "system"
    USER = "user"
    SUBAGENT = "subagent"


class RunEventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    BACKGROUND_TASK_STARTED = "background_task_started"
    BACKGROUND_TASK_UPDATED = "background_task_updated"
    BACKGROUND_TASK_COMPLETED = "background_task_completed"
    BACKGROUND_TASK_STOPPED = "background_task_stopped"
    MONITOR_CREATED = "monitor_created"
    MONITOR_TRIGGERED = "monitor_triggered"
    MONITOR_STOPPED = "monitor_stopped"
    LLM_RETRY_SCHEDULED = "llm_retry_scheduled"
    LLM_RETRY_EXHAUSTED = "llm_retry_exhausted"
    LLM_FALLBACK_ACTIVATED = "llm_fallback_activated"
    LLM_FALLBACK_EXHAUSTED = "llm_fallback_exhausted"
    MODEL_STEP_STARTED = "model_step_started"
    MODEL_STEP_FINISHED = "model_step_finished"
    TEXT_DELTA = "text_delta"
    OUTPUT_DELTA = "output_delta"
    GENERATION_PROGRESS = "generation_progress"
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
    USER_QUESTION_REQUESTED = "user_question_requested"
    USER_QUESTION_ANSWERED = "user_question_answered"
    NOTIFICATION_REQUESTED = "notification_requested"
    SUBAGENT_STOPPED = "subagent_stopped"
    SUBAGENT_RESUMED = "subagent_resumed"
    RUN_STOPPED = "run_stopped"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    AWAITING_MANUAL_ACTION = "awaiting_manual_action"
    TOKEN_USAGE = "token_usage"
    HOOK_MATCHED = "hook_matched"
    HOOK_STARTED = "hook_started"
    HOOK_COMPLETED = "hook_completed"
    HOOK_FAILED = "hook_failed"
    HOOK_DECISION_APPLIED = "hook_decision_applied"
    HOOK_DEFERRED = "hook_deferred"

# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.sessions.runs.background_tasks import BackgroundTaskService
    from relay_teams.sessions.runs.background_tasks.manager import (
        BackgroundTaskManager,
    )
    from relay_teams.sessions.runs.background_tasks.models import (
        BackgroundTaskRecord,
        BackgroundTaskStatus,
    )
    from relay_teams.sessions.runs.background_tasks.repository import (
        BackgroundTaskRepository,
    )
    from relay_teams.sessions.runs.assistant_errors import (
        AssistantRunError,
        AssistantRunErrorPayload,
        RunCompletionReason,
    )
    from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
    from relay_teams.sessions.runs.run_control_manager import RunControlManager
    from relay_teams.sessions.runs.recoverable_pause import (
        RecoverableRunPauseError,
        RecoverableRunPausePayload,
    )
    from relay_teams.sessions.runs.enums import (
        ExecutionMode,
        InjectionSource,
        RunEventType,
    )
    from relay_teams.sessions.runs.event_log import EventLog
    from relay_teams.sessions.runs.event_stream import RunEventHub
    from relay_teams.sessions.runs.ids import TraceId, new_trace_id
    from relay_teams.sessions.runs.injection_queue import RunInjectionManager
    from relay_teams.sessions.runs.run_manager import RunManager
    from relay_teams.sessions.runs.run_models import (
        InjectionMessage,
        IntentInput,
        RuntimePromptConversationContext,
        RunEvent,
        RunResult,
    )
    from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
    from relay_teams.sessions.runs.run_runtime_repo import (
        RunRuntimePhase,
        RunRuntimeRecord,
        RunRuntimeRepository,
        RunRuntimeStatus,
    )
    from relay_teams.sessions.runs.user_question_manager import UserQuestionManager
    from relay_teams.sessions.runs.user_question_models import (
        NONE_OF_THE_ABOVE_OPTION_LABEL,
        PendingUserQuestionState,
        UserQuestionAnswer,
        UserQuestionAnswerSubmission,
        UserQuestionOption,
        UserQuestionPrompt,
        UserQuestionSelection,
        UserQuestionRequestRecord,
        UserQuestionRequestStatus,
    )
    from relay_teams.sessions.runs.user_question_repository import (
        UserQuestionRepository,
    )
    from relay_teams.sessions.runs.run_state_models import (
        RunSnapshotRecord,
        RunStatePhase,
        RunStateRecord,
        RunStateStatus,
        apply_run_event_to_state,
    )
    from relay_teams.sessions.runs.run_state_repo import RunStateRepository
    from relay_teams.sessions.runs.runtime_config import (
        RuntimeConfig,
        RuntimePaths,
        load_runtime_config,
    )

__all__ = [
    "ActiveSessionRunRegistry",
    "BackgroundTaskRecord",
    "BackgroundTaskService",
    "BackgroundTaskStatus",
    "BackgroundTaskManager",
    "BackgroundTaskRepository",
    "AssistantRunError",
    "AssistantRunErrorPayload",
    "ExecutionMode",
    "EventLog",
    "InjectionMessage",
    "InjectionSource",
    "IntentInput",
    "NONE_OF_THE_ABOVE_OPTION_LABEL",
    "RunControlManager",
    "RunCompletionReason",
    "RunEvent",
    "RunEventHub",
    "RunEventType",
    "RunInjectionManager",
    "RunIntentRepository",
    "RunManager",
    "RecoverableRunPauseError",
    "RecoverableRunPausePayload",
    "RunResult",
    "RuntimePromptConversationContext",
    "RunRuntimePhase",
    "RunRuntimeRecord",
    "RunRuntimeRepository",
    "RunRuntimeStatus",
    "PendingUserQuestionState",
    "RunSnapshotRecord",
    "RunStatePhase",
    "RunStateRecord",
    "RunStateRepository",
    "RunStateStatus",
    "RuntimeConfig",
    "RuntimePaths",
    "TraceId",
    "UserQuestionAnswer",
    "UserQuestionAnswerSubmission",
    "UserQuestionManager",
    "UserQuestionOption",
    "UserQuestionPrompt",
    "UserQuestionSelection",
    "UserQuestionRepository",
    "UserQuestionRequestRecord",
    "UserQuestionRequestStatus",
    "apply_run_event_to_state",
    "load_runtime_config",
    "new_trace_id",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ActiveSessionRunRegistry": (
        "relay_teams.sessions.runs.active_run_registry",
        "ActiveSessionRunRegistry",
    ),
    "BackgroundTaskService": (
        "relay_teams.sessions.runs.background_tasks",
        "BackgroundTaskService",
    ),
    "BackgroundTaskManager": (
        "relay_teams.sessions.runs.background_tasks.manager",
        "BackgroundTaskManager",
    ),
    "BackgroundTaskRecord": (
        "relay_teams.sessions.runs.background_tasks.models",
        "BackgroundTaskRecord",
    ),
    "BackgroundTaskRepository": (
        "relay_teams.sessions.runs.background_tasks.repository",
        "BackgroundTaskRepository",
    ),
    "BackgroundTaskStatus": (
        "relay_teams.sessions.runs.background_tasks.models",
        "BackgroundTaskStatus",
    ),
    "AssistantRunError": (
        "relay_teams.sessions.runs.assistant_errors",
        "AssistantRunError",
    ),
    "AssistantRunErrorPayload": (
        "relay_teams.sessions.runs.assistant_errors",
        "AssistantRunErrorPayload",
    ),
    "ExecutionMode": ("relay_teams.sessions.runs.enums", "ExecutionMode"),
    "EventLog": ("relay_teams.sessions.runs.event_log", "EventLog"),
    "InjectionMessage": ("relay_teams.sessions.runs.run_models", "InjectionMessage"),
    "InjectionSource": ("relay_teams.sessions.runs.enums", "InjectionSource"),
    "IntentInput": ("relay_teams.sessions.runs.run_models", "IntentInput"),
    "RuntimePromptConversationContext": (
        "relay_teams.sessions.runs.run_models",
        "RuntimePromptConversationContext",
    ),
    "RunControlManager": (
        "relay_teams.sessions.runs.run_control_manager",
        "RunControlManager",
    ),
    "RunCompletionReason": (
        "relay_teams.sessions.runs.assistant_errors",
        "RunCompletionReason",
    ),
    "RecoverableRunPauseError": (
        "relay_teams.sessions.runs.recoverable_pause",
        "RecoverableRunPauseError",
    ),
    "RecoverableRunPausePayload": (
        "relay_teams.sessions.runs.recoverable_pause",
        "RecoverableRunPausePayload",
    ),
    "RunEvent": ("relay_teams.sessions.runs.run_models", "RunEvent"),
    "RunEventHub": ("relay_teams.sessions.runs.event_stream", "RunEventHub"),
    "RunEventType": ("relay_teams.sessions.runs.enums", "RunEventType"),
    "RunInjectionManager": (
        "relay_teams.sessions.runs.injection_queue",
        "RunInjectionManager",
    ),
    "RunIntentRepository": (
        "relay_teams.sessions.runs.run_intent_repo",
        "RunIntentRepository",
    ),
    "RunManager": ("relay_teams.sessions.runs.run_manager", "RunManager"),
    "RunResult": ("relay_teams.sessions.runs.run_models", "RunResult"),
    "RunRuntimePhase": (
        "relay_teams.sessions.runs.run_runtime_repo",
        "RunRuntimePhase",
    ),
    "RunRuntimeRecord": (
        "relay_teams.sessions.runs.run_runtime_repo",
        "RunRuntimeRecord",
    ),
    "RunRuntimeRepository": (
        "relay_teams.sessions.runs.run_runtime_repo",
        "RunRuntimeRepository",
    ),
    "RunRuntimeStatus": (
        "relay_teams.sessions.runs.run_runtime_repo",
        "RunRuntimeStatus",
    ),
    "PendingUserQuestionState": (
        "relay_teams.sessions.runs.user_question_models",
        "PendingUserQuestionState",
    ),
    "NONE_OF_THE_ABOVE_OPTION_LABEL": (
        "relay_teams.sessions.runs.user_question_models",
        "NONE_OF_THE_ABOVE_OPTION_LABEL",
    ),
    "RunSnapshotRecord": (
        "relay_teams.sessions.runs.run_state_models",
        "RunSnapshotRecord",
    ),
    "RunStatePhase": (
        "relay_teams.sessions.runs.run_state_models",
        "RunStatePhase",
    ),
    "RunStateRecord": (
        "relay_teams.sessions.runs.run_state_models",
        "RunStateRecord",
    ),
    "RunStateRepository": (
        "relay_teams.sessions.runs.run_state_repo",
        "RunStateRepository",
    ),
    "RunStateStatus": (
        "relay_teams.sessions.runs.run_state_models",
        "RunStateStatus",
    ),
    "RuntimeConfig": ("relay_teams.sessions.runs.runtime_config", "RuntimeConfig"),
    "RuntimePaths": ("relay_teams.sessions.runs.runtime_config", "RuntimePaths"),
    "TraceId": ("relay_teams.sessions.runs.ids", "TraceId"),
    "UserQuestionAnswer": (
        "relay_teams.sessions.runs.user_question_models",
        "UserQuestionAnswer",
    ),
    "UserQuestionAnswerSubmission": (
        "relay_teams.sessions.runs.user_question_models",
        "UserQuestionAnswerSubmission",
    ),
    "UserQuestionManager": (
        "relay_teams.sessions.runs.user_question_manager",
        "UserQuestionManager",
    ),
    "UserQuestionOption": (
        "relay_teams.sessions.runs.user_question_models",
        "UserQuestionOption",
    ),
    "UserQuestionPrompt": (
        "relay_teams.sessions.runs.user_question_models",
        "UserQuestionPrompt",
    ),
    "UserQuestionSelection": (
        "relay_teams.sessions.runs.user_question_models",
        "UserQuestionSelection",
    ),
    "UserQuestionRepository": (
        "relay_teams.sessions.runs.user_question_repository",
        "UserQuestionRepository",
    ),
    "UserQuestionRequestRecord": (
        "relay_teams.sessions.runs.user_question_models",
        "UserQuestionRequestRecord",
    ),
    "UserQuestionRequestStatus": (
        "relay_teams.sessions.runs.user_question_models",
        "UserQuestionRequestStatus",
    ),
    "apply_run_event_to_state": (
        "relay_teams.sessions.runs.run_state_models",
        "apply_run_event_to_state",
    ),
    "load_runtime_config": (
        "relay_teams.sessions.runs.runtime_config",
        "load_runtime_config",
    ),
    "new_trace_id": ("relay_teams.sessions.runs.ids", "new_trace_id"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)

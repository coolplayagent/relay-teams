# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
    from agent_teams.sessions.runs.run_control_manager import RunControlManager
    from agent_teams.sessions.runs.enums import (
        ExecutionMode,
        InjectionSource,
        RunEventType,
    )
    from agent_teams.sessions.runs.event_log import EventLog
    from agent_teams.sessions.runs.event_stream import RunEventHub
    from agent_teams.sessions.runs.ids import TraceId, new_trace_id
    from agent_teams.sessions.runs.injection_queue import RunInjectionManager
    from agent_teams.sessions.runs.run_manager import RunManager
    from agent_teams.sessions.runs.run_models import (
        InjectionMessage,
        IntentInput,
        RunEvent,
        RunResult,
    )
    from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
    from agent_teams.sessions.runs.run_runtime_repo import (
        RunRuntimePhase,
        RunRuntimeRecord,
        RunRuntimeRepository,
        RunRuntimeStatus,
    )
    from agent_teams.sessions.runs.run_state_models import (
        RunSnapshotRecord,
        RunStatePhase,
        RunStateRecord,
        RunStateStatus,
        apply_run_event_to_state,
    )
    from agent_teams.sessions.runs.run_state_repo import RunStateRepository
    from agent_teams.sessions.runs.runtime_config import (
        RuntimeConfig,
        RuntimePaths,
        load_runtime_config,
    )

__all__ = [
    "ActiveSessionRunRegistry",
    "ExecutionMode",
    "EventLog",
    "InjectionMessage",
    "InjectionSource",
    "IntentInput",
    "RunControlManager",
    "RunEvent",
    "RunEventHub",
    "RunEventType",
    "RunInjectionManager",
    "RunIntentRepository",
    "RunManager",
    "RunResult",
    "RunRuntimePhase",
    "RunRuntimeRecord",
    "RunRuntimeRepository",
    "RunRuntimeStatus",
    "RunSnapshotRecord",
    "RunStatePhase",
    "RunStateRecord",
    "RunStateRepository",
    "RunStateStatus",
    "RuntimeConfig",
    "RuntimePaths",
    "TraceId",
    "apply_run_event_to_state",
    "load_runtime_config",
    "new_trace_id",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ActiveSessionRunRegistry": (
        "agent_teams.sessions.runs.active_run_registry",
        "ActiveSessionRunRegistry",
    ),
    "ExecutionMode": ("agent_teams.sessions.runs.enums", "ExecutionMode"),
    "EventLog": ("agent_teams.sessions.runs.event_log", "EventLog"),
    "InjectionMessage": ("agent_teams.sessions.runs.run_models", "InjectionMessage"),
    "InjectionSource": ("agent_teams.sessions.runs.enums", "InjectionSource"),
    "IntentInput": ("agent_teams.sessions.runs.run_models", "IntentInput"),
    "RunControlManager": (
        "agent_teams.sessions.runs.run_control_manager",
        "RunControlManager",
    ),
    "RunEvent": ("agent_teams.sessions.runs.run_models", "RunEvent"),
    "RunEventHub": ("agent_teams.sessions.runs.event_stream", "RunEventHub"),
    "RunEventType": ("agent_teams.sessions.runs.enums", "RunEventType"),
    "RunInjectionManager": (
        "agent_teams.sessions.runs.injection_queue",
        "RunInjectionManager",
    ),
    "RunIntentRepository": (
        "agent_teams.sessions.runs.run_intent_repo",
        "RunIntentRepository",
    ),
    "RunManager": ("agent_teams.sessions.runs.run_manager", "RunManager"),
    "RunResult": ("agent_teams.sessions.runs.run_models", "RunResult"),
    "RunRuntimePhase": (
        "agent_teams.sessions.runs.run_runtime_repo",
        "RunRuntimePhase",
    ),
    "RunRuntimeRecord": (
        "agent_teams.sessions.runs.run_runtime_repo",
        "RunRuntimeRecord",
    ),
    "RunRuntimeRepository": (
        "agent_teams.sessions.runs.run_runtime_repo",
        "RunRuntimeRepository",
    ),
    "RunRuntimeStatus": (
        "agent_teams.sessions.runs.run_runtime_repo",
        "RunRuntimeStatus",
    ),
    "RunSnapshotRecord": (
        "agent_teams.sessions.runs.run_state_models",
        "RunSnapshotRecord",
    ),
    "RunStatePhase": (
        "agent_teams.sessions.runs.run_state_models",
        "RunStatePhase",
    ),
    "RunStateRecord": (
        "agent_teams.sessions.runs.run_state_models",
        "RunStateRecord",
    ),
    "RunStateRepository": (
        "agent_teams.sessions.runs.run_state_repo",
        "RunStateRepository",
    ),
    "RunStateStatus": (
        "agent_teams.sessions.runs.run_state_models",
        "RunStateStatus",
    ),
    "RuntimeConfig": ("agent_teams.sessions.runs.runtime_config", "RuntimeConfig"),
    "RuntimePaths": ("agent_teams.sessions.runs.runtime_config", "RuntimePaths"),
    "TraceId": ("agent_teams.sessions.runs.ids", "TraceId"),
    "apply_run_event_to_state": (
        "agent_teams.sessions.runs.run_state_models",
        "apply_run_event_to_state",
    ),
    "load_runtime_config": (
        "agent_teams.sessions.runs.runtime_config",
        "load_runtime_config",
    ),
    "new_trace_id": ("agent_teams.sessions.runs.ids", "new_trace_id"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)

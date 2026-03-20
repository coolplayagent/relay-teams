# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import platform

from agent_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from agent_teams.persistence.shared_state_repo import SharedStateRepository

PROMPT_INSTRUCTION_STATE_PREFIX = "prompt_instruction:"


def normalize_instruction_path(path: Path) -> str:
    resolved = str(path.expanduser().resolve())
    if platform.system() == "Windows":
        return resolved.lower()
    return resolved


def record_prompt_instruction_loaded(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    path: Path,
) -> None:
    resolved_path = path.expanduser().resolve()
    shared_store.manage_state(
        StateMutation(
            scope=_task_scope(task_id),
            key=_state_key(resolved_path),
            value_json='"loaded"',
        )
    )


def record_prompt_instruction_paths_loaded(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    paths: tuple[Path, ...],
) -> None:
    for path in paths:
        record_prompt_instruction_loaded(
            shared_store=shared_store,
            task_id=task_id,
            path=path,
        )


def is_prompt_instruction_loaded(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    path: Path,
) -> bool:
    return shared_store.get_state(_task_scope(task_id), _state_key(path)) is not None


def filter_unloaded_prompt_instruction_paths(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    paths: tuple[Path, ...],
) -> tuple[Path, ...]:
    return tuple(
        path
        for path in paths
        if not is_prompt_instruction_loaded(
            shared_store=shared_store,
            task_id=task_id,
            path=path,
        )
    )


def _task_scope(task_id: str) -> ScopeRef:
    return ScopeRef(scope_type=ScopeType.TASK, scope_id=task_id)


def _state_key(path: Path) -> str:
    return PROMPT_INSTRUCTION_STATE_PREFIX + normalize_instruction_path(path)

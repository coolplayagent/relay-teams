# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from agent_teams.persistence.shared_state_repo import SharedStateRepository

READ_STATE_PREFIX = "workspace_read:"


class FileReadState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    mtime_ns: int = Field(ge=0)
    size: int = Field(ge=0)


def normalize_resolved_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def fingerprint_file(path: Path) -> FileReadState:
    stat = path.stat()
    return FileReadState(
        path=normalize_resolved_path(path),
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
    )


def record_file_read(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    path: Path,
) -> FileReadState:
    state = fingerprint_file(path)
    shared_store.manage_state(
        StateMutation(
            scope=_task_scope(task_id),
            key=_state_key(path),
            value_json=state.model_dump_json(),
        )
    )
    return state


def load_file_read_state(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    path: Path,
) -> FileReadState | None:
    raw = shared_store.get_state(_task_scope(task_id), _state_key(path))
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return FileReadState.model_validate(payload)
    except Exception:
        return None


def assert_file_was_read(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    path: Path,
) -> FileReadState:
    state = load_file_read_state(shared_store=shared_store, task_id=task_id, path=path)
    if state is None:
        raise ValueError("You must read file before editing it.")
    return state


def assert_file_unchanged_since_read(
    *,
    shared_store: SharedStateRepository,
    task_id: str,
    path: Path,
) -> FileReadState:
    state = assert_file_was_read(shared_store=shared_store, task_id=task_id, path=path)
    current = fingerprint_file(path)
    if current.mtime_ns != state.mtime_ns or current.size != state.size:
        raise ValueError("File has been modified since it was last read.")
    return state


def _task_scope(task_id: str) -> ScopeRef:
    return ScopeRef(scope_type=ScopeType.TASK, scope_id=task_id)


def _state_key(path: Path) -> str:
    return READ_STATE_PREFIX + normalize_resolved_path(path)

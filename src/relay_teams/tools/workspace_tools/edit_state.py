# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository

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
    session_id: str,
    conversation_id: str,
    path: Path,
) -> FileReadState:
    state = fingerprint_file(path)
    shared_store.manage_state(
        StateMutation(
            scope=_session_scope(session_id),
            key=_state_key(conversation_id=conversation_id, path=path),
            value_json=state.model_dump_json(),
        )
    )
    return state


async def record_file_read_async(
    *,
    shared_store: SharedStateRepository,
    session_id: str,
    conversation_id: str,
    path: Path,
) -> FileReadState:
    state = fingerprint_file(path)
    await shared_store.manage_state_async(
        StateMutation(
            scope=_session_scope(session_id),
            key=_state_key(conversation_id=conversation_id, path=path),
            value_json=state.model_dump_json(),
        )
    )
    return state


def load_file_read_state(
    *,
    shared_store: SharedStateRepository,
    session_id: str,
    conversation_id: str,
    path: Path,
) -> FileReadState | None:
    raw = shared_store.get_state(
        _session_scope(session_id),
        _state_key(conversation_id=conversation_id, path=path),
    )
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
    except ValidationError:
        return None


def assert_file_was_read(
    *,
    shared_store: SharedStateRepository,
    session_id: str,
    conversation_id: str,
    path: Path,
) -> FileReadState:
    state = load_file_read_state(
        shared_store=shared_store,
        session_id=session_id,
        conversation_id=conversation_id,
        path=path,
    )
    if state is None:
        raise ValueError("You must read file before editing it.")
    return state


def assert_file_unchanged_since_read(
    *,
    shared_store: SharedStateRepository,
    session_id: str,
    conversation_id: str,
    path: Path,
) -> FileReadState:
    state = assert_file_was_read(
        shared_store=shared_store,
        session_id=session_id,
        conversation_id=conversation_id,
        path=path,
    )
    current = fingerprint_file(path)
    if current.mtime_ns != state.mtime_ns or current.size != state.size:
        raise ValueError("File has been modified since it was last read.")
    return state


def _session_scope(session_id: str) -> ScopeRef:
    return ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_id)


def _state_key(*, conversation_id: str, path: Path) -> str:
    return READ_STATE_PREFIX + conversation_id + ":" + normalize_resolved_path(path)

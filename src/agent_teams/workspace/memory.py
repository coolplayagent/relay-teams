# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent_teams.state.scope_models import ScopeRef, ScopeType, StateMutation
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.workspace.ids import (
    build_instance_role_scope_id,
    build_instance_session_scope_id,
)
from agent_teams.workspace.models import (
    StateScope,
    WorkspaceBinding,
    WorkspaceProfile,
    WorkspaceRef,
)


class WorkspaceMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    store: SharedStateRepository
    ref: WorkspaceRef
    profile: WorkspaceProfile

    def workspace_scope(self) -> ScopeRef:
        return ScopeRef(scope_type=ScopeType.WORKSPACE, scope_id=self.ref.workspace_id)

    def session_scope(self) -> ScopeRef:
        return ScopeRef(
            scope_type=ScopeType.SESSION,
            scope_id=self._session_scope_id(),
        )

    def role_scope(self) -> ScopeRef:
        return ScopeRef(
            scope_type=ScopeType.ROLE,
            scope_id=self._role_scope_id(),
        )

    def conversation_scope(self) -> ScopeRef:
        return ScopeRef(
            scope_type=ScopeType.CONVERSATION,
            scope_id=self.ref.conversation_id,
        )

    def prompt_snapshot(self) -> tuple[tuple[str, str], ...]:
        readable_scopes = tuple(
            self._resolve_scope(scope) for scope in self.profile.readable_scopes
        )
        return self.store.snapshot_many(readable_scopes)

    def write_role_memory(self, *, key: str, value_json: str) -> None:
        self._assert_writable(StateScope.ROLE)
        self.store.manage_state(
            StateMutation(
                scope=self.role_scope(),
                key=key,
                value_json=value_json,
            )
        )

    def write_conversation_memory(self, *, key: str, value_json: str) -> None:
        self._assert_writable(StateScope.CONVERSATION)
        self.store.manage_state(
            StateMutation(
                scope=self.conversation_scope(),
                key=key,
                value_json=value_json,
            )
        )

    def _assert_writable(self, scope: StateScope) -> None:
        if scope not in self.profile.writable_scopes:
            raise ValueError(f"Workspace scope is not writable: {scope.value}")

    def _resolve_scope(self, scope: StateScope) -> ScopeRef:
        if scope == StateScope.WORKSPACE:
            return self.workspace_scope()
        if scope == StateScope.SESSION:
            return self.session_scope()
        if scope == StateScope.ROLE:
            return self.role_scope()
        if scope == StateScope.CONVERSATION:
            return self.conversation_scope()
        raise ValueError(f"Unsupported workspace snapshot scope: {scope.value}")

    def _session_scope_id(self) -> str:
        if (
            self.profile.binding == WorkspaceBinding.INSTANCE
            and self.ref.instance_id is not None
        ):
            return build_instance_session_scope_id(
                self.ref.session_id,
                self.ref.instance_id,
            )
        return self.ref.session_id

    def _role_scope_id(self) -> str:
        if (
            self.profile.binding == WorkspaceBinding.INSTANCE
            and self.ref.instance_id is not None
        ):
            return build_instance_role_scope_id(
                self.ref.session_id,
                self.ref.role_id,
                self.ref.instance_id,
            )
        return f"{self.ref.session_id}:{self.ref.role_id}"

# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock, patch

from relay_teams.roles.role_contracts import (
    RoleContract,
    RoleContractInvariant,
    RoleContractInvariantType,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.tools.runtime.execution import _apply_role_contract_check


def _make_role(
    *,
    role_id: str = "test_role",
    tools: tuple[str, ...] = (),
    contract: RoleContract | None = None,
) -> RoleDefinition:
    return RoleDefinition(
        role_id=role_id,
        name="Test Role",
        description="A test role",
        version="1",
        tools=tools,
        system_prompt="You are a test role.",
        contract=contract if contract is not None else RoleContract(),
    )


def _make_ctx(
    role: RoleDefinition,
    *,
    runtime_role_resolver: MagicMock | None = None,
) -> MagicMock:
    ctx = MagicMock()
    deps = MagicMock()
    deps.role_id = role.role_id
    deps.role_registry = MagicMock()
    deps.role_registry.get = MagicMock(return_value=role)
    deps.runtime_role_resolver = runtime_role_resolver
    ctx.deps = deps
    return ctx


class TestRoleContractInterception:
    """Test cases for _apply_role_contract_check."""

    def test_tool_not_denied_passes_through(self) -> None:
        """AC-4: Tool not in denied set passes through without error."""
        contract = RoleContract(
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                    tools=("shell", "write"),
                ),
            )
        )
        role = _make_role(contract=contract)
        ctx = _make_ctx(role)
        result = _apply_role_contract_check(ctx=ctx, tool_name="read")
        assert result is None

    def test_tool_in_must_not_have_tools_denied(self) -> None:
        """AC-1: Tool in must_not_have_tools gets tool_policy_denied error."""
        contract = RoleContract(
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                    tools=("shell", "write"),
                ),
            )
        )
        role = _make_role(contract=contract)
        ctx = _make_ctx(role)
        result = _apply_role_contract_check(ctx=ctx, tool_name="shell")
        assert result is not None
        assert result.type == "tool_policy_denied"
        assert result.retryable is False
        assert "shell" in result.message
        assert "test_role" in result.message

    def test_role_with_empty_contract_no_interception(self) -> None:
        """No contract invariants means no interception."""
        role = _make_role(contract=RoleContract())
        ctx = _make_ctx(role)
        result = _apply_role_contract_check(ctx=ctx, tool_name="shell")
        assert result is None

    def test_role_with_must_have_only_no_interception(self) -> None:
        """AC-4: MUST_HAVE_TOOLS invariant does not trigger denial."""
        contract = RoleContract(
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_HAVE_TOOLS,
                    tools=("read", "glob"),
                ),
            )
        )
        role = _make_role(contract=contract)
        ctx = _make_ctx(role)
        result = _apply_role_contract_check(ctx=ctx, tool_name="shell")
        assert result is None

    @patch("relay_teams.tools.runtime.execution.log_event")
    def test_error_envelope_meta_fields(self, mock_log: MagicMock) -> None:
        """AC-2/AC-5: Error includes proper type and message."""
        contract = RoleContract(
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                    tools=("shell",),
                ),
            )
        )
        role = _make_role(contract=contract)
        ctx = _make_ctx(role)
        result = _apply_role_contract_check(ctx=ctx, tool_name="shell")
        assert result is not None
        assert result.type == "tool_policy_denied"
        assert result.retryable is False
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert (
            call_args[1].get("event") == "tool.role_contract.denied"
            or call_args[0][2] == "tool.role_contract.denied"
        )

    @patch("relay_teams.tools.runtime.execution.log_event")
    def test_runtime_role_resolver_used(self, mock_log: MagicMock) -> None:
        """AC-5: runtime_role_resolver takes precedence."""
        contract = RoleContract(
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                    tools=("shell",),
                ),
            )
        )
        dynamic_role = _make_role(role_id="dynamic_role", contract=contract)
        resolver = MagicMock()
        resolver.get_effective_role = MagicMock(return_value=dynamic_role)
        static_role = _make_role(role_id="dynamic_role", contract=RoleContract())
        ctx = _make_ctx(static_role, runtime_role_resolver=resolver)
        result = _apply_role_contract_check(ctx=ctx, tool_name="shell")
        assert result is not None
        assert result.type == "tool_policy_denied"

    def test_multiple_must_not_have_invariants_union(self) -> None:
        """Multiple MUST_NOT_HAVE_TOOLS invariants combine."""
        contract = RoleContract(
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                    tools=("shell",),
                ),
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                    tools=("write",),
                ),
            )
        )
        role = _make_role(contract=contract)
        ctx = _make_ctx(role)
        assert _apply_role_contract_check(ctx=ctx, tool_name="shell") is not None
        assert _apply_role_contract_check(ctx=ctx, tool_name="write") is not None
        assert _apply_role_contract_check(ctx=ctx, tool_name="read") is None

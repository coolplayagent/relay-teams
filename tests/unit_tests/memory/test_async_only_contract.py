# -*- coding: utf-8 -*-
from __future__ import annotations

import relay_teams.roles.memory_injection as memory_injection
from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.evolution_history import RoleEvolutionHistoryService
from relay_teams.roles.memory_service import RoleMemoryService


def _public_callables(cls: type[object]) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_") and callable(value)
    }


def test_memory_runtime_has_no_public_sync_async_method_pairs() -> None:
    for cls in (
        MemoryBankRepository,
        MemoryBankService,
        MemoryEventHandler,
        RoleMemoryService,
        RoleEvolutionHistoryService,
    ):
        methods = _public_callables(cls)
        sync_async_pairs = sorted(
            method for method in methods if f"{method}_async" in methods
        )
        assert sync_async_pairs == []


def test_removed_sync_memory_facades_do_not_return() -> None:
    forbidden_methods: dict[type[object], tuple[str, ...]] = {
        MemoryBankRepository: (
            "create_entry",
            "get_by_id",
            "update_entry",
            "delete_entry",
            "query_entries",
            "expire_entries",
            "apply_confidence_decay",
            "count_entries",
            "expire_oldest",
        ),
        MemoryBankService: (
            "create_entry",
            "get_entry",
            "list_entries",
            "update_entry",
            "delete_entry",
            "consolidate",
            "forget_expired",
            "search",
            "enforce_capacity",
        ),
        MemoryEventHandler: (
            "on_task_completed",
            "on_run_completed",
            "on_session_completed",
            "get_injectable_memory_text",
        ),
        RoleEvolutionHistoryService: (
            "record_event",
            "get_timeline",
            "get_current_state",
        ),
        RoleMemoryService: (
            "build_injected_memory",
            "get_reflection_record",
            "update_reflection_memory",
            "delete_reflection_memory",
            "build_reflection_preview",
            "get_performance_metrics",
            "record_task_result",
        ),
    }

    for cls, method_names in forbidden_methods.items():
        methods = _public_callables(cls)
        returned_methods = sorted(name for name in method_names if name in methods)
        assert returned_methods == []


def test_memory_injection_old_sync_helpers_do_not_return() -> None:
    exported_names = vars(memory_injection)
    forbidden_helpers = (
        "build_role_with_memory",
        "_build_project_memory_section",
        "_build_role_evolution_section",
        "_find_latest_maturity_level",
        "_count_applied_adjustments",
    )

    returned_helpers = sorted(
        name for name in forbidden_helpers if name in exported_names
    )
    assert returned_helpers == []

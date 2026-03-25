from __future__ import annotations

from pathlib import Path

from agent_teams.automation import (
    AutomationFeishuBinding,
    AutomationFeishuBindingService,
)
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.triggers import (
    TriggerCreateInput,
    TriggerRepository,
    TriggerService,
    TriggerSourceType,
)


class _FakeRuntimeConfigLookup:
    def get_runtime_config_by_trigger_id(self, trigger_id: str) -> object | None:
        _ = trigger_id
        return object()

    def is_feishu_trigger(self, trigger) -> bool:
        return (
            trigger.source_type == TriggerSourceType.IM
            and str(trigger.source_config.get("provider", "")).strip() == "feishu"
        )


def _build_service(
    tmp_path: Path,
) -> tuple[AutomationFeishuBindingService, TriggerService, SessionRepository, ExternalSessionBindingRepository]:
    db_path = tmp_path / "automation-feishu.db"
    trigger_service = TriggerService(trigger_repo=TriggerRepository(db_path))
    session_repo = SessionRepository(db_path)
    binding_repo = ExternalSessionBindingRepository(db_path)
    service = AutomationFeishuBindingService(
        external_session_binding_repo=binding_repo,
        session_repo=session_repo,
        trigger_lookup=trigger_service,
        runtime_config_lookup=_FakeRuntimeConfigLookup(),
    )
    return service, trigger_service, session_repo, binding_repo


def test_list_candidates_returns_existing_feishu_chat_bindings(tmp_path: Path) -> None:
    service, trigger_service, session_repo, binding_repo = _build_service(tmp_path)
    trigger = trigger_service.create_trigger(
        TriggerCreateInput(
            name="feishu_main",
            source_type=TriggerSourceType.IM,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
        )
    )
    session = session_repo.create(
        session_id="session-im-1",
        workspace_id="default",
        metadata={
            "title": "feishu_main - Release Updates",
            "source_label": "Release Updates",
            "title_source": "auto",
            "feishu_chat_type": "group",
        },
    )
    binding_repo.upsert_binding(
        platform="feishu",
        trigger_id=trigger.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_123",
        session_id=session.session_id,
    )

    candidates = service.list_candidates()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.trigger_id == trigger.trigger_id
    assert candidate.chat_id == "oc_123"
    assert candidate.source_label == "Release Updates"
    assert candidate.session_title == "Release Updates"


def test_list_candidates_prefers_manual_session_title(tmp_path: Path) -> None:
    service, trigger_service, session_repo, binding_repo = _build_service(tmp_path)
    trigger = trigger_service.create_trigger(
        TriggerCreateInput(
            name="feishu_main",
            source_type=TriggerSourceType.IM,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
        )
    )
    session = session_repo.create(
        session_id="session-im-1",
        workspace_id="default",
        metadata={
            "title": "值班告警群",
            "title_source": "manual",
            "source_label": "Release Updates",
            "feishu_chat_type": "group",
        },
    )
    binding_repo.upsert_binding(
        platform="feishu",
        trigger_id=trigger.trigger_id,
        tenant_key="tenant-1",
        external_chat_id="oc_123",
        session_id=session.session_id,
    )

    candidates = service.list_candidates()

    assert len(candidates) == 1
    assert candidates[0].session_title == "值班告警群"


def test_validate_binding_rejects_unknown_chat_binding(tmp_path: Path) -> None:
    service, trigger_service, _session_repo, _binding_repo = _build_service(tmp_path)
    trigger = trigger_service.create_trigger(
        TriggerCreateInput(
            name="feishu_main",
            source_type=TriggerSourceType.IM,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
        )
    )

    try:
        service.validate_binding(
            AutomationFeishuBinding(
                trigger_id=trigger.trigger_id,
                tenant_key="tenant-1",
                chat_id="oc_missing",
                chat_type="group",
                source_label="Missing Chat",
            )
        )
    except ValueError as exc:
        assert "existing Feishu chat binding" in str(exc)
    else:
        raise AssertionError("Expected validate_binding to reject unknown chat")

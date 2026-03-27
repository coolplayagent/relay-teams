from __future__ import annotations

from pathlib import Path

from agent_teams.automation import (
    AutomationFeishuBinding,
    AutomationFeishuBindingService,
)
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.session_repository import SessionRepository


class _FakeAccount:
    def __init__(self, account_id: str, display_name: str) -> None:
        self.account_id = account_id
        self.display_name = display_name


class _FakeAccountLookup:
    def __init__(self, *accounts: _FakeAccount) -> None:
        self._accounts = {account.account_id: account for account in accounts}

    def get_account(self, account_id: str) -> _FakeAccount:
        try:
            return self._accounts[account_id]
        except KeyError as exc:
            raise KeyError(account_id) from exc


class _FakeRuntimeConfigLookup:
    def __init__(self, *account_ids: str) -> None:
        self._account_ids = set(account_ids)

    def get_runtime_config_by_trigger_id(self, trigger_id: str) -> object | None:
        if trigger_id in self._account_ids:
            return object()
        return None


def _build_service(
    tmp_path: Path,
) -> tuple[
    AutomationFeishuBindingService,
    SessionRepository,
    ExternalSessionBindingRepository,
]:
    db_path = tmp_path / "automation-feishu.db"
    session_repo = SessionRepository(db_path)
    binding_repo = ExternalSessionBindingRepository(db_path)
    service = AutomationFeishuBindingService(
        external_session_binding_repo=binding_repo,
        session_repo=session_repo,
        account_lookup=_FakeAccountLookup(
            _FakeAccount("fsg_main", "Feishu Main"),
        ),
        runtime_config_lookup=_FakeRuntimeConfigLookup("fsg_main"),
    )
    return service, session_repo, binding_repo


def test_list_candidates_returns_existing_feishu_chat_bindings(tmp_path: Path) -> None:
    service, session_repo, binding_repo = _build_service(tmp_path)
    session = session_repo.create(
        session_id="session-im-1",
        workspace_id="default",
        metadata={
            "title": "Feishu Main - Release Updates",
            "source_label": "Release Updates",
            "title_source": "auto",
            "feishu_chat_type": "group",
        },
    )
    binding_repo.upsert_binding(
        platform="feishu",
        trigger_id="fsg_main",
        tenant_key="tenant-1",
        external_chat_id="oc_123",
        session_id=session.session_id,
    )

    candidates = service.list_candidates()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.trigger_id == "fsg_main"
    assert candidate.trigger_name == "Feishu Main"
    assert candidate.chat_id == "oc_123"
    assert candidate.source_label == "Release Updates"
    assert candidate.session_title == "Feishu Main - Release Updates"


def test_list_candidates_preserves_manual_session_title(tmp_path: Path) -> None:
    service, session_repo, binding_repo = _build_service(tmp_path)
    session = session_repo.create(
        session_id="session-im-1",
        workspace_id="default",
        metadata={
            "title": "Manual Oncall Session",
            "title_source": "manual",
            "source_label": "Release Updates",
            "feishu_chat_type": "group",
        },
    )
    binding_repo.upsert_binding(
        platform="feishu",
        trigger_id="fsg_main",
        tenant_key="tenant-1",
        external_chat_id="oc_123",
        session_id=session.session_id,
    )

    candidates = service.list_candidates()

    assert len(candidates) == 1
    assert candidates[0].session_title == "Manual Oncall Session"


def test_validate_binding_rejects_unknown_chat_binding(tmp_path: Path) -> None:
    service, _session_repo, _binding_repo = _build_service(tmp_path)

    try:
        service.validate_binding(
            AutomationFeishuBinding(
                trigger_id="fsg_main",
                tenant_key="tenant-1",
                chat_id="oc_missing",
                session_id="session-im-1",
                chat_type="group",
                source_label="Missing Chat",
            )
        )
    except ValueError as exc:
        assert "existing Feishu chat binding" in str(exc)
    else:
        raise AssertionError("Expected validate_binding to reject unknown chat")


def test_validate_binding_requires_session_id(tmp_path: Path) -> None:
    service, _session_repo, _binding_repo = _build_service(tmp_path)

    try:
        service.validate_binding(
            AutomationFeishuBinding(
                trigger_id="fsg_main",
                tenant_key="tenant-1",
                chat_id="oc_123",
                chat_type="group",
                source_label="Release Updates",
            )
        )
    except ValueError as exc:
        assert "delivery_binding.session_id is required" in str(exc)
    else:
        raise AssertionError("Expected validate_binding to require session_id")


def test_validate_binding_returns_canonical_candidate_with_session_id(
    tmp_path: Path,
) -> None:
    service, session_repo, binding_repo = _build_service(tmp_path)
    session = session_repo.create(
        session_id="session-im-1",
        workspace_id="default",
        metadata={
            "title": "Feishu Main - Release Updates",
            "source_label": "Release Updates",
            "title_source": "auto",
            "feishu_chat_type": "group",
        },
    )
    binding_repo.upsert_binding(
        platform="feishu",
        trigger_id="fsg_main",
        tenant_key="tenant-1",
        external_chat_id="oc_123",
        session_id=session.session_id,
    )

    validated = service.validate_binding(
        AutomationFeishuBinding(
            trigger_id="fsg_main",
            tenant_key="tenant-1",
            chat_id="oc_123",
            session_id="session-im-1",
            chat_type="p2p",
            source_label="Wrong Label",
        )
    )

    assert validated.session_id == "session-im-1"
    assert validated.chat_type == "group"
    assert validated.source_label == "Release Updates"

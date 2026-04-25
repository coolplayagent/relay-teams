from __future__ import annotations

from datetime import UTC, datetime

from relay_teams.automation import (
    AutomationXiaolubanBinding,
    AutomationXiaolubanBindingService,
)
from relay_teams.gateway.xiaoluban import (
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    XiaolubanSecretStatus,
)


class _FakeXiaolubanLookup:
    def __init__(
        self,
        accounts: tuple[XiaolubanAccountRecord, ...],
        usable_account_ids: tuple[str, ...],
    ) -> None:
        self._accounts = {account.account_id: account for account in accounts}
        self._usable_account_ids = set(usable_account_ids)

    def list_accounts(self) -> tuple[XiaolubanAccountRecord, ...]:
        return tuple(self._accounts.values())

    def get_account(self, account_id: str) -> XiaolubanAccountRecord:
        try:
            return self._accounts[account_id]
        except KeyError as exc:
            raise KeyError(account_id) from exc

    def has_usable_credentials(self, account_id: str) -> bool:
        return account_id in self._usable_account_ids


def test_list_candidates_returns_only_accounts_with_usable_credentials() -> None:
    service = AutomationXiaolubanBindingService(
        account_lookup=_FakeXiaolubanLookup(
            accounts=(
                _build_account("xlb_1", "小鲁班主账号", "uid_1"),
                _build_account("xlb_2", "小鲁班备用账号", "uid_2"),
            ),
            usable_account_ids=("xlb_1",),
        )
    )

    candidates = service.list_candidates()

    assert len(candidates) == 1
    assert candidates[0].provider == "xiaoluban"
    assert candidates[0].account_id == "xlb_1"
    assert candidates[0].source_label == "发送给自己（uid_1）"


def test_validate_binding_returns_canonical_binding() -> None:
    service = AutomationXiaolubanBindingService(
        account_lookup=_FakeXiaolubanLookup(
            accounts=(_build_account("xlb_1", "小鲁班主账号", "uid_1"),),
            usable_account_ids=("xlb_1",),
        )
    )

    binding = service.validate_binding(
        AutomationXiaolubanBinding(
            account_id="xlb_1",
            display_name="错误名称",
            derived_uid="wrong_uid",
            source_label="错误来源",
        )
    )

    assert binding.provider == "xiaoluban"
    assert binding.display_name == "小鲁班主账号"
    assert binding.derived_uid == "uid_1"
    assert binding.source_label == "发送给自己（uid_1）"


def test_list_candidates_labels_configured_receiver() -> None:
    service = AutomationXiaolubanBindingService(
        account_lookup=_FakeXiaolubanLookup(
            accounts=(
                _build_account(
                    "xlb_1",
                    "小鲁班主账号",
                    "uid_1",
                    notification_receiver="group-123",
                ),
            ),
            usable_account_ids=("xlb_1",),
        )
    )

    candidates = service.list_candidates()

    assert candidates[0].source_label == "发送给 group-123"


def test_validate_binding_rejects_account_without_usable_credentials() -> None:
    service = AutomationXiaolubanBindingService(
        account_lookup=_FakeXiaolubanLookup(
            accounts=(_build_account("xlb_1", "小鲁班主账号", "uid_1"),),
            usable_account_ids=(),
        )
    )

    try:
        service.validate_binding(
            AutomationXiaolubanBinding(
                account_id="xlb_1",
                display_name="小鲁班主账号",
                derived_uid="uid_1",
                source_label="发送给自己（uid_1）",
            )
        )
    except ValueError as exc:
        assert "usable Xiaoluban credentials" in str(exc)
    else:
        raise AssertionError("Expected validate_binding to reject unusable account")


def test_validate_binding_rejects_missing_account() -> None:
    service = AutomationXiaolubanBindingService(
        account_lookup=_FakeXiaolubanLookup(accounts=(), usable_account_ids=())
    )

    try:
        service.validate_binding(
            AutomationXiaolubanBinding(
                account_id="missing",
                display_name="小鲁班主账号",
                derived_uid="uid_1",
                source_label="发送给自己（uid_1）",
            )
        )
    except ValueError as exc:
        assert "existing Xiaoluban account" in str(exc)
    else:
        raise AssertionError("Expected validate_binding to reject missing account")


def _build_account(
    account_id: str,
    display_name: str,
    derived_uid: str,
    notification_receiver: str | None = None,
) -> XiaolubanAccountRecord:
    now = datetime(2026, 4, 22, tzinfo=UTC)
    return XiaolubanAccountRecord(
        account_id=account_id,
        display_name=display_name,
        status=XiaolubanAccountStatus.ENABLED,
        derived_uid=derived_uid,
        notification_receiver=notification_receiver,
        secret_status=XiaolubanSecretStatus(token_configured=True),
        created_at=now,
        updated_at=now,
    )

# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Protocol

from relay_teams.automation.automation_models import (
    AutomationXiaolubanBinding,
    AutomationXiaolubanBindingCandidate,
)
from relay_teams.gateway.xiaoluban import XiaolubanAccountRecord


class XiaolubanAccountLookup(Protocol):
    def list_accounts(self) -> tuple[XiaolubanAccountRecord, ...]: ...

    def get_account(self, account_id: str) -> XiaolubanAccountRecord: ...

    def has_usable_credentials(self, account_id: str) -> bool: ...


class AutomationXiaolubanBindingService:
    def __init__(self, *, account_lookup: XiaolubanAccountLookup) -> None:
        self._account_lookup = account_lookup

    def list_candidates(self) -> tuple[AutomationXiaolubanBindingCandidate, ...]:
        candidates: list[AutomationXiaolubanBindingCandidate] = []
        for account in self._account_lookup.list_accounts():
            if not self._account_lookup.has_usable_credentials(account.account_id):
                continue
            candidates.append(
                AutomationXiaolubanBindingCandidate(
                    account_id=account.account_id,
                    display_name=account.display_name,
                    derived_uid=account.derived_uid,
                    source_label=_build_source_label(account),
                    updated_at=account.updated_at,
                )
            )
        return tuple(candidates)

    def validate_binding(
        self,
        binding: AutomationXiaolubanBinding,
    ) -> AutomationXiaolubanBinding:
        try:
            _ = self._account_lookup.get_account(binding.account_id)
        except KeyError as exc:
            raise ValueError(
                "delivery_binding must reference an existing Xiaoluban account"
            ) from exc
        if not self._account_lookup.has_usable_credentials(binding.account_id):
            raise ValueError(
                "delivery_binding.account_id does not have usable Xiaoluban credentials"
            )
        matched_candidate: AutomationXiaolubanBindingCandidate | None = None
        for candidate in self.list_candidates():
            if candidate.account_id == binding.account_id:
                matched_candidate = candidate
                break
        if matched_candidate is None:
            raise ValueError(
                "delivery_binding must reference an existing Xiaoluban account"
            )
        return AutomationXiaolubanBinding(
            account_id=matched_candidate.account_id,
            display_name=matched_candidate.display_name,
            derived_uid=matched_candidate.derived_uid,
            source_label=matched_candidate.source_label,
        )


def _build_source_label(account: XiaolubanAccountRecord) -> str:
    receiver = str(account.notification_receiver or "").strip()
    if receiver:
        return f"发送给 {receiver}"
    return f"发送给自己（{account.derived_uid}）"


__all__ = ["AutomationXiaolubanBindingService"]

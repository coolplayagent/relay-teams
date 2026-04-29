# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.gateway.xiaoluban.models import XiaolubanAccountRecord


class NotifyProvider(str, Enum):
    XIAOLUBAN = "xiaoluban"


class NotifyTarget(str, Enum):
    OWNER = "owner"
    CONFIGURED_GROUPS = "configured_groups"
    OWNER_AND_CONFIGURED_GROUPS = "owner_and_configured_groups"
    EXPLICIT = "explicit"


class NotifyRecipientKind(str, Enum):
    OWNER = "owner"
    GROUP = "group"


class NotifyRecipient(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recipient_id: str = Field(min_length=1)
    kind: NotifyRecipientKind


class NotifyAccountSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    derived_uid: str = Field(min_length=1)
    configured_group_count: int = Field(ge=0)


class NotifyResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: XiaolubanAccountRecord
    recipients: tuple[NotifyRecipient, ...]
    filtered_recipients: tuple[str, ...] = ()

    @property
    def includes_group(self) -> bool:
        return any(
            recipient.kind == NotifyRecipientKind.GROUP for recipient in self.recipients
        )

    def target_summary(self) -> str:
        group_count = sum(
            1
            for recipient in self.recipients
            if recipient.kind == NotifyRecipientKind.GROUP
        )
        owner_count = sum(
            1
            for recipient in self.recipients
            if recipient.kind == NotifyRecipientKind.OWNER
        )
        fragments = [
            f"Xiaoluban account {self.account.display_name}"
            f" ({self.account.account_id})",
        ]
        if owner_count:
            fragments.append(f"{owner_count} owner target")
        if group_count:
            fragments.append(f"{group_count} group target(s)")
        return "; ".join(fragments)


class NotifySendAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recipient_id: str = Field(min_length=1)
    kind: NotifyRecipientKind
    ok: bool
    message_id: str = ""
    error: str = ""


class NotifySendResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: NotifyProvider
    account: NotifyAccountSummary
    target: NotifyTarget
    sent: tuple[NotifySendAttempt, ...]
    failed: tuple[NotifySendAttempt, ...] = ()
    filtered_recipients: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")


def summarize_xiaoluban_account(
    account: XiaolubanAccountRecord,
) -> NotifyAccountSummary:
    return NotifyAccountSummary(
        account_id=account.account_id,
        display_name=account.display_name,
        derived_uid=account.derived_uid,
        configured_group_count=len(account.notification_receivers),
    )


__all__ = [
    "NotifyAccountSummary",
    "NotifyProvider",
    "NotifyRecipient",
    "NotifyRecipientKind",
    "NotifyResolution",
    "NotifySendAttempt",
    "NotifySendResult",
    "NotifyTarget",
    "summarize_xiaoluban_account",
]

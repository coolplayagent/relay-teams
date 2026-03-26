from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import hmac
from json import JSONDecodeError, loads
from pathlib import Path
from secrets import token_urlsafe
import uuid

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_teams.paths import get_app_config_dir
from agent_teams.secrets import AppSecretStore, get_secret_store
from agent_teams.triggers.trigger_models import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerCreateInput,
    TriggerDefinition,
    TriggerEventRecord,
    TriggerEventStatus,
    TriggerIngestInput,
    TriggerIngestResult,
    TriggerStatus,
    TriggerUpdateInput,
)
from agent_teams.triggers.trigger_repository import (
    TriggerEventDuplicateError,
    TriggerNameConflictError,
    TriggerRepository,
)

_TRIGGER_AUTH_SECRET_NAMESPACE = "trigger_auth"
_REDACTED_TRIGGER_SECRET = "<redacted>"


class TriggerAuthRejectedError(PermissionError):
    def __init__(self, message: str, event: TriggerEventRecord) -> None:
        super().__init__(message)
        self.event = event


class WebhookIngestEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_key: str | None = None
    occurred_at: datetime | None = None
    payload: dict[str, JsonValue] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class _AuthDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    mode: TriggerAuthMode | None
    reason: str


class TriggerService:
    def __init__(
        self,
        trigger_repo: TriggerRepository,
        *,
        config_dir: Path | None = None,
        secret_store: AppSecretStore | None = None,
    ) -> None:
        self._trigger_repo = trigger_repo
        self._config_dir = (
            get_app_config_dir()
            if config_dir is None
            else config_dir.expanduser().resolve()
        )
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def create_trigger(self, trigger: TriggerCreateInput) -> TriggerDefinition:
        now = datetime.now(tz=UTC)
        display_name = trigger.display_name or trigger.name
        auth_policies = trigger.auth_policies or (
            TriggerAuthPolicy(mode=TriggerAuthMode.NONE),
        )
        definition = TriggerDefinition(
            trigger_id=f"trg_{uuid.uuid4().hex[:12]}",
            name=trigger.name,
            display_name=display_name,
            source_type=trigger.source_type,
            status=TriggerStatus.ENABLED if trigger.enabled else TriggerStatus.DISABLED,
            public_token=trigger.public_token or self._new_public_token(),
            source_config=trigger.source_config,
            auth_policies=self._redact_auth_policies(auth_policies),
            target_config=trigger.target_config,
            created_at=now,
            updated_at=now,
        )
        created = self._trigger_repo.create_trigger(definition)
        self._replace_auth_policy_secrets(created.trigger_id, auth_policies)
        return self._hydrate_trigger(created)

    def update_trigger(
        self, trigger_id: str, trigger: TriggerUpdateInput
    ) -> TriggerDefinition:
        existing = self.get_trigger(trigger_id)
        now = datetime.now(tz=UTC)
        merged_auth_policies = (
            trigger.auth_policies
            if trigger.auth_policies is not None
            else existing.auth_policies
        )

        merged = TriggerDefinition(
            trigger_id=existing.trigger_id,
            name=trigger.name if trigger.name is not None else existing.name,
            display_name=(
                trigger.display_name
                if trigger.display_name is not None
                else existing.display_name
            ),
            source_type=existing.source_type,
            status=existing.status,
            public_token=existing.public_token,
            source_config=(
                trigger.source_config
                if trigger.source_config is not None
                else existing.source_config
            ),
            auth_policies=self._redact_auth_policies(merged_auth_policies),
            target_config=(
                trigger.target_config
                if trigger.target_config is not None
                else existing.target_config
            ),
            created_at=existing.created_at,
            updated_at=now,
        )
        updated = self._trigger_repo.update_trigger(merged)
        self._replace_auth_policy_secrets(updated.trigger_id, merged_auth_policies)
        return self._hydrate_trigger(updated)

    def set_trigger_status(
        self, trigger_id: str, status: TriggerStatus
    ) -> TriggerDefinition:
        existing = self._trigger_repo.get_trigger(trigger_id)
        updated = existing.model_copy(
            update={
                "status": status,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        return self._hydrate_trigger(self._trigger_repo.update_trigger(updated))

    def rotate_public_token(self, trigger_id: str) -> TriggerDefinition:
        existing = self._trigger_repo.get_trigger(trigger_id)
        updated = existing.model_copy(
            update={
                "public_token": self._new_public_token(),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        return self._hydrate_trigger(self._trigger_repo.update_trigger(updated))

    def get_trigger(self, trigger_id: str) -> TriggerDefinition:
        return self._hydrate_trigger(self._trigger_repo.get_trigger(trigger_id))

    def list_triggers(self) -> tuple[TriggerDefinition, ...]:
        return tuple(
            self._hydrate_trigger(trigger)
            for trigger in self._trigger_repo.list_triggers()
        )

    def get_trigger_by_public_token(self, public_token: str) -> TriggerDefinition:
        return self._hydrate_trigger(
            self._trigger_repo.get_trigger_by_public_token(public_token)
        )

    def get_event(self, event_id: str) -> TriggerEventRecord:
        return self._trigger_repo.get_event(event_id)

    def delete_trigger(self, trigger_id: str) -> None:
        _ = self._trigger_repo.get_trigger(trigger_id)
        self._trigger_repo.delete_events_by_trigger(trigger_id)
        self._trigger_repo.delete_trigger(trigger_id)
        self._secret_store.delete_owner(
            self._config_dir,
            namespace=_TRIGGER_AUTH_SECRET_NAMESPACE,
            owner_id=trigger_id,
        )

    def list_events(
        self,
        trigger_id: str,
        *,
        limit: int = 50,
        cursor_event_id: str | None = None,
    ) -> tuple[tuple[TriggerEventRecord, ...], str | None]:
        _ = self._trigger_repo.get_trigger(trigger_id)
        return self._trigger_repo.list_events_by_trigger(
            trigger_id,
            limit=limit,
            cursor_event_id=cursor_event_id,
        )

    def ingest_event(
        self,
        event: TriggerIngestInput,
        *,
        headers: dict[str, str],
        remote_addr: str | None,
        raw_body: str,
    ) -> TriggerIngestResult:
        trigger = self._resolve_trigger(
            trigger_id=event.trigger_id,
            trigger_name=event.trigger_name,
        )
        if event.source_type != trigger.source_type:
            raise ValueError(
                f"Source type mismatch: expected {trigger.source_type.value}, got {event.source_type.value}"
            )
        return self._ingest_for_trigger(
            trigger=trigger,
            event_key=event.event_key,
            occurred_at=event.occurred_at,
            payload=event.payload,
            metadata=event.metadata,
            headers=headers,
            remote_addr=remote_addr,
            raw_body=raw_body,
            presented_public_token=None,
        )

    def ingest_webhook(
        self,
        *,
        public_token: str,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerIngestResult:
        trigger = self.get_trigger_by_public_token(public_token)
        envelope = self._parse_webhook_body(raw_body)
        payload = (
            envelope.payload if envelope.payload is not None else envelope.model_dump()
        )
        return self._ingest_for_trigger(
            trigger=trigger,
            event_key=envelope.event_key,
            occurred_at=envelope.occurred_at,
            payload=payload,
            metadata=envelope.metadata,
            headers=headers,
            remote_addr=remote_addr,
            raw_body=raw_body,
            presented_public_token=public_token,
        )

    def _resolve_trigger(
        self, *, trigger_id: str | None, trigger_name: str | None
    ) -> TriggerDefinition:
        if trigger_id is not None:
            trigger = self.get_trigger(trigger_id)
            if trigger_name is not None and trigger.name != trigger_name:
                raise ValueError("trigger_id and trigger_name do not match")
            return trigger
        if trigger_name is None:
            raise ValueError("trigger_id or trigger_name is required")
        return self._hydrate_trigger(self._trigger_repo.get_trigger_by_name(trigger_name))

    def _ingest_for_trigger(
        self,
        *,
        trigger: TriggerDefinition,
        event_key: str | None,
        occurred_at: datetime | None,
        payload: dict[str, JsonValue],
        metadata: dict[str, str],
        headers: dict[str, str],
        remote_addr: str | None,
        raw_body: str,
        presented_public_token: str | None,
    ) -> TriggerIngestResult:
        if trigger.status != TriggerStatus.ENABLED:
            raise RuntimeError(f"Trigger {trigger.trigger_id} is disabled")

        decision = self._authorize(
            trigger=trigger,
            headers=headers,
            raw_body=raw_body,
            presented_public_token=presented_public_token,
        )
        auth_result = "accepted" if decision.allowed else "rejected"
        status = (
            TriggerEventStatus.RECEIVED
            if decision.allowed
            else TriggerEventStatus.REJECTED_AUTH
        )
        record = TriggerEventRecord(
            sequence_id=0,
            event_id=f"tev_{uuid.uuid4().hex[:16]}",
            trigger_id=trigger.trigger_id,
            trigger_name=trigger.name,
            source_type=trigger.source_type,
            event_key=event_key,
            status=status,
            received_at=datetime.now(tz=UTC),
            occurred_at=occurred_at,
            payload=payload,
            metadata=metadata,
            headers=headers,
            remote_addr=remote_addr,
            auth_mode=decision.mode,
            auth_result=auth_result,
            auth_reason=decision.reason,
        )

        try:
            stored = self._trigger_repo.create_event(record)
        except TriggerEventDuplicateError as exc:
            existing = self._trigger_repo.get_event_by_key(
                exc.trigger_id, exc.event_key
            )
            return TriggerIngestResult(
                accepted=True,
                event_id=existing.event_id,
                duplicate=True,
                status=TriggerEventStatus.DUPLICATE,
                trigger_id=existing.trigger_id,
                trigger_name=existing.trigger_name,
            )

        if not decision.allowed:
            raise TriggerAuthRejectedError(decision.reason, stored)

        return TriggerIngestResult(
            accepted=True,
            event_id=stored.event_id,
            duplicate=False,
            status=stored.status,
            trigger_id=stored.trigger_id,
            trigger_name=stored.trigger_name,
        )

    def _authorize(
        self,
        *,
        trigger: TriggerDefinition,
        headers: dict[str, str],
        raw_body: str,
        presented_public_token: str | None,
    ) -> _AuthDecision:
        normalized_headers = {name.lower(): value for name, value in headers.items()}
        policies = trigger.auth_policies or (
            TriggerAuthPolicy(mode=TriggerAuthMode.NONE),
        )
        reasons: list[str] = []
        for policy in policies:
            if policy.mode == TriggerAuthMode.NONE:
                return _AuthDecision(
                    allowed=True,
                    mode=TriggerAuthMode.NONE,
                    reason="no_auth_required",
                )
            if policy.mode == TriggerAuthMode.URL_TOKEN:
                if (
                    presented_public_token is not None
                    and trigger.public_token is not None
                    and hmac.compare_digest(
                        presented_public_token, trigger.public_token
                    )
                ):
                    return _AuthDecision(
                        allowed=True,
                        mode=TriggerAuthMode.URL_TOKEN,
                        reason="url_token_match",
                    )
                reasons.append("url_token_mismatch")
                continue
            if policy.mode == TriggerAuthMode.HEADER_TOKEN:
                if policy.header_name is None or policy.token is None:
                    reasons.append("header_token_misconfigured")
                    continue
                header_value = normalized_headers.get(policy.header_name.lower())
                if header_value is None:
                    reasons.append("header_token_missing")
                    continue
                if hmac.compare_digest(header_value, policy.token):
                    return _AuthDecision(
                        allowed=True,
                        mode=TriggerAuthMode.HEADER_TOKEN,
                        reason="header_token_match",
                    )
                reasons.append("header_token_mismatch")
                continue
            if policy.mode == TriggerAuthMode.HMAC_SHA256:
                hmac_decision = self._verify_hmac(
                    policy=policy,
                    headers=normalized_headers,
                    raw_body=raw_body,
                )
                if hmac_decision.allowed:
                    return hmac_decision
                reasons.append(hmac_decision.reason)
                continue

        reason = ";".join(reasons) if reasons else "auth_policy_rejected"
        return _AuthDecision(allowed=False, mode=None, reason=reason)

    def _verify_hmac(
        self,
        *,
        policy: TriggerAuthPolicy,
        headers: dict[str, str],
        raw_body: str,
    ) -> _AuthDecision:
        if policy.secret is None:
            return _AuthDecision(
                allowed=False,
                mode=TriggerAuthMode.HMAC_SHA256,
                reason="hmac_secret_missing",
            )
        signature_value = headers.get(policy.signature_header.lower())
        timestamp_value = headers.get(policy.timestamp_header.lower())
        if signature_value is None:
            return _AuthDecision(
                allowed=False,
                mode=TriggerAuthMode.HMAC_SHA256,
                reason="hmac_signature_missing",
            )
        if timestamp_value is None:
            return _AuthDecision(
                allowed=False,
                mode=TriggerAuthMode.HMAC_SHA256,
                reason="hmac_timestamp_missing",
            )
        try:
            timestamp = int(timestamp_value)
        except ValueError:
            return _AuthDecision(
                allowed=False,
                mode=TriggerAuthMode.HMAC_SHA256,
                reason="hmac_timestamp_invalid",
            )

        now_ts = int(datetime.now(tz=UTC).timestamp())
        if abs(now_ts - timestamp) > policy.max_skew_seconds:
            return _AuthDecision(
                allowed=False,
                mode=TriggerAuthMode.HMAC_SHA256,
                reason="hmac_timestamp_skew_exceeded",
            )

        to_sign = f"{timestamp_value}.{raw_body}".encode("utf-8")
        expected = hmac.new(
            policy.secret.encode("utf-8"),
            to_sign,
            hashlib.sha256,
        ).hexdigest()
        actual = signature_value.removeprefix("sha256=")
        if not hmac.compare_digest(expected, actual):
            return _AuthDecision(
                allowed=False,
                mode=TriggerAuthMode.HMAC_SHA256,
                reason="hmac_signature_mismatch",
            )
        return _AuthDecision(
            allowed=True,
            mode=TriggerAuthMode.HMAC_SHA256,
            reason="hmac_signature_match",
        )

    def _parse_webhook_body(self, raw_body: str) -> WebhookIngestEnvelope:
        try:
            parsed = loads(raw_body)
        except JSONDecodeError as exc:
            raise ValueError("Webhook body must be valid JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Webhook body must be a JSON object")
        envelope = WebhookIngestEnvelope.model_validate(parsed)
        if envelope.payload is None:
            return envelope.model_copy(update={"payload": parsed})
        return envelope

    @staticmethod
    def _new_public_token() -> str:
        return token_urlsafe(24)

    def _hydrate_trigger(self, trigger: TriggerDefinition) -> TriggerDefinition:
        return trigger.model_copy(
            update={
                "auth_policies": self._hydrate_auth_policies(
                    trigger.trigger_id,
                    trigger.auth_policies,
                )
            }
        )

    def _hydrate_auth_policies(
        self,
        trigger_id: str,
        policies: tuple[TriggerAuthPolicy, ...],
    ) -> tuple[TriggerAuthPolicy, ...]:
        hydrated: list[TriggerAuthPolicy] = []
        for index, policy in enumerate(policies):
            token = self._resolve_and_migrate_policy_secret(
                trigger_id=trigger_id,
                policy_index=index,
                field_name="token",
                legacy_value=policy.token,
            )
            secret = self._resolve_and_migrate_policy_secret(
                trigger_id=trigger_id,
                policy_index=index,
                field_name="secret",
                legacy_value=policy.secret,
            )
            hydrated.append(policy.model_copy(update={"token": token, "secret": secret}))
        return tuple(hydrated)

    def _redact_auth_policies(
        self,
        policies: tuple[TriggerAuthPolicy, ...],
    ) -> tuple[TriggerAuthPolicy, ...]:
        redacted: list[TriggerAuthPolicy] = []
        for policy in policies:
            token = None
            secret = None
            if policy.mode == TriggerAuthMode.HEADER_TOKEN and policy.token is not None:
                token = _REDACTED_TRIGGER_SECRET
            if policy.mode == TriggerAuthMode.HMAC_SHA256 and policy.secret is not None:
                secret = _REDACTED_TRIGGER_SECRET
            redacted.append(policy.model_copy(update={"token": token, "secret": secret}))
        return tuple(redacted)

    def _replace_auth_policy_secrets(
        self,
        trigger_id: str,
        policies: tuple[TriggerAuthPolicy, ...],
    ) -> None:
        self._secret_store.delete_owner(
            self._config_dir,
            namespace=_TRIGGER_AUTH_SECRET_NAMESPACE,
            owner_id=trigger_id,
        )
        for index, policy in enumerate(policies):
            self._set_policy_secret(
                trigger_id=trigger_id,
                policy_index=index,
                field_name="token",
                value=policy.token,
            )
            self._set_policy_secret(
                trigger_id=trigger_id,
                policy_index=index,
                field_name="secret",
                value=policy.secret,
            )

    def _set_policy_secret(
        self,
        *,
        trigger_id: str,
        policy_index: int,
        field_name: str,
        value: str | None,
    ) -> None:
        self._secret_store.set_secret(
            self._config_dir,
            namespace=_TRIGGER_AUTH_SECRET_NAMESPACE,
            owner_id=trigger_id,
            field_name=self._policy_secret_field_name(policy_index, field_name),
            value=value,
        )

    def _resolve_and_migrate_policy_secret(
        self,
        *,
        trigger_id: str,
        policy_index: int,
        field_name: str,
        legacy_value: str | None,
    ) -> str | None:
        value = self._secret_store.get_secret(
            self._config_dir,
            namespace=_TRIGGER_AUTH_SECRET_NAMESPACE,
            owner_id=trigger_id,
            field_name=self._policy_secret_field_name(policy_index, field_name),
        )
        if value is not None:
            return value
        if legacy_value is None:
            return None
        self._secret_store.migrate_legacy_secret(
            self._config_dir,
            namespace=_TRIGGER_AUTH_SECRET_NAMESPACE,
            owner_id=trigger_id,
            field_name=self._policy_secret_field_name(policy_index, field_name),
            value=legacy_value,
        )
        return legacy_value

    @staticmethod
    def _policy_secret_field_name(policy_index: int, field_name: str) -> str:
        return f"policy:{policy_index}:{field_name}"


__all__ = [
    "TriggerAuthRejectedError",
    "TriggerEventDuplicateError",
    "TriggerNameConflictError",
    "TriggerService",
]

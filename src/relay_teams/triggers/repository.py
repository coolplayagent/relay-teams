# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from relay_teams.logger import get_logger
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.triggers.models import (
    GitHubActionSpec,
    GitHubActionType,
    GitHubRepoSubscriptionRecord,
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountStatus,
    GitHubWebhookStatus,
    TriggerActionAttemptRecord,
    TriggerActionPhase,
    TriggerActionStatus,
    TriggerDeliveryIngestStatus,
    TriggerDispatchRecord,
    TriggerDispatchStatus,
    TriggerDeliveryRecord,
    TriggerDeliverySignatureStatus,
    TriggerEvaluationRecord,
    TriggerProvider,
    TriggerRuleRecord,
    TriggerTargetType,
)
from relay_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


class GitHubTriggerAccountNameConflictError(ValueError):
    pass


class GitHubRepoSubscriptionConflictError(ValueError):
    pass


class TriggerRuleNameConflictError(ValueError):
    pass


class TriggerRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS github_trigger_accounts (
                    account_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_configured INTEGER NOT NULL DEFAULT 0,
                    webhook_secret_configured INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS github_repo_subscriptions (
                    repo_subscription_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    repo_name TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    external_repo_id TEXT,
                    default_branch TEXT,
                    provider_webhook_id TEXT,
                    subscribed_events_json TEXT NOT NULL DEFAULT '[]',
                    webhook_status TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_webhook_sync_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(account_id, full_name)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trigger_rules (
                    trigger_rule_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    repo_subscription_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    match_config_json TEXT NOT NULL,
                    dispatch_config_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    last_error TEXT,
                    last_fired_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(repo_subscription_id, name)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trigger_deliveries (
                    trigger_delivery_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_delivery_id TEXT,
                    account_id TEXT,
                    repo_subscription_id TEXT,
                    event_name TEXT NOT NULL,
                    event_action TEXT,
                    signature_status TEXT NOT NULL,
                    ingest_status TEXT NOT NULL,
                    headers_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    normalized_payload_json TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    processed_at TEXT,
                    last_error TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_trigger_deliveries_provider_delivery
                ON trigger_deliveries(provider, provider_delivery_id)
                WHERE provider_delivery_id IS NOT NULL
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trigger_evaluations (
                    trigger_evaluation_id TEXT PRIMARY KEY,
                    trigger_delivery_id TEXT NOT NULL,
                    trigger_rule_id TEXT NOT NULL,
                    matched INTEGER NOT NULL,
                    reason_code TEXT NOT NULL,
                    reason_detail TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trigger_dispatches (
                    trigger_dispatch_id TEXT PRIMARY KEY,
                    trigger_delivery_id TEXT NOT NULL,
                    trigger_rule_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    session_id TEXT,
                    run_id TEXT,
                    automation_project_id TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trigger_action_attempts (
                    trigger_action_attempt_id TEXT PRIMARY KEY,
                    trigger_dispatch_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    action_spec_json TEXT NOT NULL,
                    request_payload_json TEXT NOT NULL DEFAULT '{}',
                    response_payload_json TEXT NOT NULL DEFAULT '{}',
                    provider_resource_id TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_github_repo_subscriptions_full_name
                ON github_repo_subscriptions(full_name, enabled)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trigger_rules_lookup
                ON trigger_rules(repo_subscription_id, enabled, created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trigger_deliveries_received
                ON trigger_deliveries(repo_subscription_id, received_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trigger_dispatches_status
                ON trigger_dispatches(status, created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trigger_action_attempts_status
                ON trigger_action_attempts(status, updated_at ASC)
                """
            )

        self._run_write(operation_name="init_tables", operation=operation)

    def create_account(
        self,
        record: GitHubTriggerAccountRecord,
    ) -> GitHubTriggerAccountRecord:
        try:
            self._run_write(
                operation_name="create_account",
                operation=lambda: self._conn.execute(
                    """
                    INSERT INTO github_trigger_accounts(
                        account_id, name, display_name, status,
                        token_configured, webhook_secret_configured, last_error,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.account_id,
                        record.name,
                        record.display_name,
                        record.status.value,
                        1 if record.token_configured else 0,
                        1 if record.webhook_secret_configured else 0,
                        record.last_error,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "github_trigger_accounts.name" in str(exc).lower():
                raise GitHubTriggerAccountNameConflictError(
                    f"GitHub trigger account name already exists: {record.name}"
                ) from exc
            raise
        return record

    def update_account(
        self,
        record: GitHubTriggerAccountRecord,
    ) -> GitHubTriggerAccountRecord:
        try:
            self._run_write(
                operation_name="update_account",
                operation=lambda: self._conn.execute(
                    """
                    UPDATE github_trigger_accounts
                    SET name=?,
                        display_name=?,
                        status=?,
                        token_configured=?,
                        webhook_secret_configured=?,
                        last_error=?,
                        updated_at=?
                    WHERE account_id=?
                    """,
                    (
                        record.name,
                        record.display_name,
                        record.status.value,
                        1 if record.token_configured else 0,
                        1 if record.webhook_secret_configured else 0,
                        record.last_error,
                        record.updated_at.isoformat(),
                        record.account_id,
                    ),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "github_trigger_accounts.name" in str(exc).lower():
                raise GitHubTriggerAccountNameConflictError(
                    f"GitHub trigger account name already exists: {record.name}"
                ) from exc
            raise
        return record

    def get_account(self, account_id: str) -> GitHubTriggerAccountRecord:
        row = self._run_read(
            lambda: self._conn.execute(
                "SELECT * FROM github_trigger_accounts WHERE account_id=?",
                (account_id,),
            ).fetchone()
        )
        if row is None:
            raise KeyError(f"Unknown GitHub trigger account: {account_id}")
        return self._to_account_record(row)

    def list_accounts(self) -> tuple[GitHubTriggerAccountRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM github_trigger_accounts
                ORDER BY created_at DESC
                """
            ).fetchall()
        )
        return tuple(self._to_account_record(row) for row in rows)

    def delete_account(self, account_id: str) -> None:
        self._run_write(
            operation_name="delete_account",
            operation=lambda: (
                self._conn.execute(
                    "DELETE FROM trigger_action_attempts WHERE trigger_dispatch_id IN (SELECT trigger_dispatch_id FROM trigger_dispatches WHERE trigger_rule_id IN (SELECT trigger_rule_id FROM trigger_rules WHERE account_id=?))",
                    (account_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_dispatches WHERE trigger_rule_id IN (SELECT trigger_rule_id FROM trigger_rules WHERE account_id=?)",
                    (account_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_evaluations WHERE trigger_rule_id IN (SELECT trigger_rule_id FROM trigger_rules WHERE account_id=?)",
                    (account_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_rules WHERE account_id=?",
                    (account_id,),
                ),
                self._conn.execute(
                    "DELETE FROM github_repo_subscriptions WHERE account_id=?",
                    (account_id,),
                ),
                self._conn.execute(
                    "DELETE FROM github_trigger_accounts WHERE account_id=?",
                    (account_id,),
                ),
            ),
        )

    def create_repo_subscription(
        self,
        record: GitHubRepoSubscriptionRecord,
    ) -> GitHubRepoSubscriptionRecord:
        try:
            self._run_write(
                operation_name="create_repo_subscription",
                operation=lambda: self._conn.execute(
                    """
                    INSERT INTO github_repo_subscriptions(
                        repo_subscription_id, account_id, owner, repo_name, full_name,
                        external_repo_id, default_branch, provider_webhook_id,
                        subscribed_events_json, webhook_status, enabled,
                        last_webhook_sync_at, last_error, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.repo_subscription_id,
                        record.account_id,
                        record.owner,
                        record.repo_name,
                        record.full_name,
                        record.external_repo_id,
                        record.default_branch,
                        record.provider_webhook_id,
                        _json_dumps(record.subscribed_events),
                        record.webhook_status.value,
                        1 if record.enabled else 0,
                        _to_iso(record.last_webhook_sync_at),
                        record.last_error,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "github_repo_subscriptions.account_id" in str(exc).lower():
                raise GitHubRepoSubscriptionConflictError(
                    f"Repository subscription already exists: {record.full_name}"
                ) from exc
            raise
        return record

    def update_repo_subscription(
        self,
        record: GitHubRepoSubscriptionRecord,
    ) -> GitHubRepoSubscriptionRecord:
        try:
            self._run_write(
                operation_name="update_repo_subscription",
                operation=lambda: self._conn.execute(
                    """
                    UPDATE github_repo_subscriptions
                    SET account_id=?,
                        owner=?,
                        repo_name=?,
                        full_name=?,
                        external_repo_id=?,
                        default_branch=?,
                        provider_webhook_id=?,
                        subscribed_events_json=?,
                        webhook_status=?,
                        enabled=?,
                        last_webhook_sync_at=?,
                        last_error=?,
                        updated_at=?
                    WHERE repo_subscription_id=?
                    """,
                    (
                        record.account_id,
                        record.owner,
                        record.repo_name,
                        record.full_name,
                        record.external_repo_id,
                        record.default_branch,
                        record.provider_webhook_id,
                        _json_dumps(record.subscribed_events),
                        record.webhook_status.value,
                        1 if record.enabled else 0,
                        _to_iso(record.last_webhook_sync_at),
                        record.last_error,
                        record.updated_at.isoformat(),
                        record.repo_subscription_id,
                    ),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "github_repo_subscriptions.account_id" in str(exc).lower():
                raise GitHubRepoSubscriptionConflictError(
                    f"Repository subscription already exists: {record.full_name}"
                ) from exc
            raise
        return record

    def get_repo_subscription(
        self, repo_subscription_id: str
    ) -> GitHubRepoSubscriptionRecord:
        row = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM github_repo_subscriptions
                WHERE repo_subscription_id=?
                """,
                (repo_subscription_id,),
            ).fetchone()
        )
        if row is None:
            raise KeyError(f"Unknown GitHub repo subscription: {repo_subscription_id}")
        return self._to_repo_subscription_record(row)

    def list_repo_subscriptions(self) -> tuple[GitHubRepoSubscriptionRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM github_repo_subscriptions
                ORDER BY created_at DESC
                """
            ).fetchall()
        )
        return tuple(self._to_repo_subscription_record(row) for row in rows)

    def list_repo_subscriptions_by_full_name(
        self,
        full_name: str,
    ) -> tuple[GitHubRepoSubscriptionRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM github_repo_subscriptions
                WHERE full_name=?
                ORDER BY created_at DESC
                """,
                (full_name,),
            ).fetchall()
        )
        return tuple(self._to_repo_subscription_record(row) for row in rows)

    def delete_repo_subscription(self, repo_subscription_id: str) -> None:
        self._run_write(
            operation_name="delete_repo_subscription",
            operation=lambda: (
                self._conn.execute(
                    "DELETE FROM trigger_action_attempts WHERE trigger_dispatch_id IN (SELECT trigger_dispatch_id FROM trigger_dispatches WHERE trigger_rule_id IN (SELECT trigger_rule_id FROM trigger_rules WHERE repo_subscription_id=?))",
                    (repo_subscription_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_dispatches WHERE trigger_rule_id IN (SELECT trigger_rule_id FROM trigger_rules WHERE repo_subscription_id=?)",
                    (repo_subscription_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_evaluations WHERE trigger_rule_id IN (SELECT trigger_rule_id FROM trigger_rules WHERE repo_subscription_id=?)",
                    (repo_subscription_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_rules WHERE repo_subscription_id=?",
                    (repo_subscription_id,),
                ),
                self._conn.execute(
                    "DELETE FROM github_repo_subscriptions WHERE repo_subscription_id=?",
                    (repo_subscription_id,),
                ),
            ),
        )

    def create_rule(self, record: TriggerRuleRecord) -> TriggerRuleRecord:
        try:
            self._run_write(
                operation_name="create_rule",
                operation=lambda: self._conn.execute(
                    """
                    INSERT INTO trigger_rules(
                        trigger_rule_id, provider, account_id, repo_subscription_id,
                        name, enabled, match_config_json, dispatch_config_json,
                        version, last_error, last_fired_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.trigger_rule_id,
                        record.provider.value,
                        record.account_id,
                        record.repo_subscription_id,
                        record.name,
                        1 if record.enabled else 0,
                        record.match_config.model_dump_json(),
                        record.dispatch_config.model_dump_json(),
                        record.version,
                        record.last_error,
                        _to_iso(record.last_fired_at),
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "trigger_rules.repo_subscription_id" in str(exc).lower():
                raise TriggerRuleNameConflictError(
                    f"Trigger rule name already exists: {record.name}"
                ) from exc
            raise
        return record

    def update_rule(self, record: TriggerRuleRecord) -> TriggerRuleRecord:
        try:
            self._run_write(
                operation_name="update_rule",
                operation=lambda: self._conn.execute(
                    """
                    UPDATE trigger_rules
                    SET provider=?,
                        account_id=?,
                        repo_subscription_id=?,
                        name=?,
                        enabled=?,
                        match_config_json=?,
                        dispatch_config_json=?,
                        version=?,
                        last_error=?,
                        last_fired_at=?,
                        updated_at=?
                    WHERE trigger_rule_id=?
                    """,
                    (
                        record.provider.value,
                        record.account_id,
                        record.repo_subscription_id,
                        record.name,
                        1 if record.enabled else 0,
                        record.match_config.model_dump_json(),
                        record.dispatch_config.model_dump_json(),
                        record.version,
                        record.last_error,
                        _to_iso(record.last_fired_at),
                        record.updated_at.isoformat(),
                        record.trigger_rule_id,
                    ),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "trigger_rules.repo_subscription_id" in str(exc).lower():
                raise TriggerRuleNameConflictError(
                    f"Trigger rule name already exists: {record.name}"
                ) from exc
            raise
        return record

    def get_rule(self, trigger_rule_id: str) -> TriggerRuleRecord:
        row = self._run_read(
            lambda: self._conn.execute(
                "SELECT * FROM trigger_rules WHERE trigger_rule_id=?",
                (trigger_rule_id,),
            ).fetchone()
        )
        if row is None:
            raise KeyError(f"Unknown trigger rule: {trigger_rule_id}")
        return self._to_rule_record(row)

    def list_rules(self) -> tuple[TriggerRuleRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_rules
                ORDER BY created_at DESC
                """
            ).fetchall()
        )
        return tuple(self._to_rule_record(row) for row in rows)

    def list_enabled_rules_for_repo(
        self,
        repo_subscription_id: str,
    ) -> tuple[TriggerRuleRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_rules
                WHERE repo_subscription_id=? AND enabled=1
                ORDER BY created_at ASC
                """,
                (repo_subscription_id,),
            ).fetchall()
        )
        return tuple(self._to_rule_record(row) for row in rows)

    def delete_rule(self, trigger_rule_id: str) -> None:
        self._run_write(
            operation_name="delete_rule",
            operation=lambda: (
                self._conn.execute(
                    "DELETE FROM trigger_action_attempts WHERE trigger_dispatch_id IN (SELECT trigger_dispatch_id FROM trigger_dispatches WHERE trigger_rule_id=?)",
                    (trigger_rule_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_dispatches WHERE trigger_rule_id=?",
                    (trigger_rule_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_evaluations WHERE trigger_rule_id=?",
                    (trigger_rule_id,),
                ),
                self._conn.execute(
                    "DELETE FROM trigger_rules WHERE trigger_rule_id=?",
                    (trigger_rule_id,),
                ),
            ),
        )

    def create_delivery(self, record: TriggerDeliveryRecord) -> TriggerDeliveryRecord:
        self._run_write(
            operation_name="create_delivery",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO trigger_deliveries(
                    trigger_delivery_id, provider, provider_delivery_id,
                    account_id, repo_subscription_id, event_name, event_action,
                    signature_status, ingest_status, headers_json, payload_json,
                    normalized_payload_json, received_at, processed_at, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trigger_delivery_id,
                    record.provider.value,
                    record.provider_delivery_id,
                    record.account_id,
                    record.repo_subscription_id,
                    record.event_name,
                    record.event_action,
                    record.signature_status.value,
                    record.ingest_status.value,
                    _json_dumps(record.headers),
                    _json_dumps(record.payload),
                    _json_dumps(record.normalized_payload),
                    record.received_at.isoformat(),
                    _to_iso(record.processed_at),
                    record.last_error,
                ),
            ),
        )
        return record

    def update_delivery(self, record: TriggerDeliveryRecord) -> TriggerDeliveryRecord:
        self._run_write(
            operation_name="update_delivery",
            operation=lambda: self._conn.execute(
                """
                UPDATE trigger_deliveries
                SET account_id=?,
                    repo_subscription_id=?,
                    event_name=?,
                    event_action=?,
                    signature_status=?,
                    ingest_status=?,
                    headers_json=?,
                    payload_json=?,
                    normalized_payload_json=?,
                    processed_at=?,
                    last_error=?
                WHERE trigger_delivery_id=?
                """,
                (
                    record.account_id,
                    record.repo_subscription_id,
                    record.event_name,
                    record.event_action,
                    record.signature_status.value,
                    record.ingest_status.value,
                    _json_dumps(record.headers),
                    _json_dumps(record.payload),
                    _json_dumps(record.normalized_payload),
                    _to_iso(record.processed_at),
                    record.last_error,
                    record.trigger_delivery_id,
                ),
            ),
        )
        return record

    def get_delivery(self, trigger_delivery_id: str) -> TriggerDeliveryRecord:
        row = self._run_read(
            lambda: self._conn.execute(
                "SELECT * FROM trigger_deliveries WHERE trigger_delivery_id=?",
                (trigger_delivery_id,),
            ).fetchone()
        )
        if row is None:
            raise KeyError(f"Unknown trigger delivery: {trigger_delivery_id}")
        return self._to_delivery_record(row)

    def get_delivery_by_provider_id(
        self,
        *,
        provider: str,
        provider_delivery_id: str,
    ) -> TriggerDeliveryRecord | None:
        row = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_deliveries
                WHERE provider=? AND provider_delivery_id=?
                """,
                (provider, provider_delivery_id),
            ).fetchone()
        )
        if row is None:
            return None
        return self._to_delivery_record(row)

    def list_deliveries(self) -> tuple[TriggerDeliveryRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_deliveries
                ORDER BY received_at DESC
                """
            ).fetchall()
        )
        return tuple(self._to_delivery_record(row) for row in rows)

    def create_evaluation(
        self,
        record: TriggerEvaluationRecord,
    ) -> TriggerEvaluationRecord:
        self._run_write(
            operation_name="create_evaluation",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO trigger_evaluations(
                    trigger_evaluation_id, trigger_delivery_id, trigger_rule_id,
                    matched, reason_code, reason_detail, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trigger_evaluation_id,
                    record.trigger_delivery_id,
                    record.trigger_rule_id,
                    1 if record.matched else 0,
                    record.reason_code,
                    record.reason_detail,
                    record.created_at.isoformat(),
                ),
            ),
        )
        return record

    def list_evaluations_by_delivery(
        self,
        trigger_delivery_id: str,
    ) -> tuple[TriggerEvaluationRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_evaluations
                WHERE trigger_delivery_id=?
                ORDER BY created_at ASC
                """,
                (trigger_delivery_id,),
            ).fetchall()
        )
        return tuple(self._to_evaluation_record(row) for row in rows)

    def create_dispatch(self, record: TriggerDispatchRecord) -> TriggerDispatchRecord:
        self._run_write(
            operation_name="create_dispatch",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO trigger_dispatches(
                    trigger_dispatch_id, trigger_delivery_id, trigger_rule_id,
                    target_type, status, session_id, run_id, automation_project_id,
                    started_at, completed_at, last_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trigger_dispatch_id,
                    record.trigger_delivery_id,
                    record.trigger_rule_id,
                    record.target_type.value,
                    record.status.value,
                    record.session_id,
                    record.run_id,
                    record.automation_project_id,
                    _to_iso(record.started_at),
                    _to_iso(record.completed_at),
                    record.last_error,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            ),
        )
        return record

    def update_dispatch(self, record: TriggerDispatchRecord) -> TriggerDispatchRecord:
        self._run_write(
            operation_name="update_dispatch",
            operation=lambda: self._conn.execute(
                """
                UPDATE trigger_dispatches
                SET status=?,
                    session_id=?,
                    run_id=?,
                    automation_project_id=?,
                    started_at=?,
                    completed_at=?,
                    last_error=?,
                    updated_at=?
                WHERE trigger_dispatch_id=?
                """,
                (
                    record.status.value,
                    record.session_id,
                    record.run_id,
                    record.automation_project_id,
                    _to_iso(record.started_at),
                    _to_iso(record.completed_at),
                    record.last_error,
                    record.updated_at.isoformat(),
                    record.trigger_dispatch_id,
                ),
            ),
        )
        return record

    def get_dispatch(self, trigger_dispatch_id: str) -> TriggerDispatchRecord:
        row = self._run_read(
            lambda: self._conn.execute(
                "SELECT * FROM trigger_dispatches WHERE trigger_dispatch_id=?",
                (trigger_dispatch_id,),
            ).fetchone()
        )
        if row is None:
            raise KeyError(f"Unknown trigger dispatch: {trigger_dispatch_id}")
        return self._to_dispatch_record(row)

    def list_dispatches(self) -> tuple[TriggerDispatchRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_dispatches
                ORDER BY created_at DESC
                """
            ).fetchall()
        )
        return tuple(self._to_dispatch_record(row) for row in rows)

    def list_dispatches_by_delivery(
        self,
        trigger_delivery_id: str,
    ) -> tuple[TriggerDispatchRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_dispatches
                WHERE trigger_delivery_id=?
                ORDER BY created_at ASC
                """,
                (trigger_delivery_id,),
            ).fetchall()
        )
        return tuple(self._to_dispatch_record(row) for row in rows)

    def list_open_dispatches(self) -> tuple[TriggerDispatchRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_dispatches
                WHERE status IN ('pending', 'running')
                ORDER BY created_at ASC
                """
            ).fetchall()
        )
        return tuple(self._to_dispatch_record(row) for row in rows)

    def create_action_attempt(
        self,
        record: TriggerActionAttemptRecord,
    ) -> TriggerActionAttemptRecord:
        self._run_write(
            operation_name="create_action_attempt",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO trigger_action_attempts(
                    trigger_action_attempt_id, trigger_dispatch_id, phase, action_type,
                    status, action_spec_json, request_payload_json,
                    response_payload_json, provider_resource_id, attempt_count,
                    last_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trigger_action_attempt_id,
                    record.trigger_dispatch_id,
                    record.phase.value,
                    record.action_type.value,
                    record.status.value,
                    record.action_spec.model_dump_json(),
                    _json_dumps(record.request_payload),
                    _json_dumps(record.response_payload),
                    record.provider_resource_id,
                    record.attempt_count,
                    record.last_error,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            ),
        )
        return record

    def update_action_attempt(
        self,
        record: TriggerActionAttemptRecord,
    ) -> TriggerActionAttemptRecord:
        self._run_write(
            operation_name="update_action_attempt",
            operation=lambda: self._conn.execute(
                """
                UPDATE trigger_action_attempts
                SET status=?,
                    action_spec_json=?,
                    request_payload_json=?,
                    response_payload_json=?,
                    provider_resource_id=?,
                    attempt_count=?,
                    last_error=?,
                    updated_at=?
                WHERE trigger_action_attempt_id=?
                """,
                (
                    record.status.value,
                    record.action_spec.model_dump_json(),
                    _json_dumps(record.request_payload),
                    _json_dumps(record.response_payload),
                    record.provider_resource_id,
                    record.attempt_count,
                    record.last_error,
                    record.updated_at.isoformat(),
                    record.trigger_action_attempt_id,
                ),
            ),
        )
        return record

    def list_action_attempts(self) -> tuple[TriggerActionAttemptRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_action_attempts
                ORDER BY created_at DESC
                """
            ).fetchall()
        )
        return tuple(self._to_action_attempt_record(row) for row in rows)

    def list_action_attempts_by_dispatch(
        self,
        trigger_dispatch_id: str,
    ) -> tuple[TriggerActionAttemptRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_action_attempts
                WHERE trigger_dispatch_id=?
                ORDER BY created_at ASC
                """,
                (trigger_dispatch_id,),
            ).fetchall()
        )
        return tuple(self._to_action_attempt_record(row) for row in rows)

    def list_pending_action_attempts(
        self,
    ) -> tuple[TriggerActionAttemptRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM trigger_action_attempts
                WHERE status IN ('pending', 'waiting_run', 'sending')
                ORDER BY created_at ASC
                """
            ).fetchall()
        )
        return tuple(self._to_action_attempt_record(row) for row in rows)

    def _to_account_record(self, row: sqlite3.Row) -> GitHubTriggerAccountRecord:
        return GitHubTriggerAccountRecord(
            account_id=_require_identifier(row["account_id"], field_name="account_id"),
            name=_require_identifier(row["name"], field_name="name"),
            display_name=_require_identifier(
                row["display_name"], field_name="display_name"
            ),
            status=GitHubTriggerAccountStatus(str(row["status"])),
            token_configured=bool(row["token_configured"]),
            webhook_secret_configured=bool(row["webhook_secret_configured"]),
            last_error=normalize_persisted_text(row["last_error"]),
            created_at=_require_datetime(row["created_at"]),
            updated_at=_require_datetime(row["updated_at"]),
        )

    def _to_repo_subscription_record(
        self,
        row: sqlite3.Row,
    ) -> GitHubRepoSubscriptionRecord:
        return GitHubRepoSubscriptionRecord(
            repo_subscription_id=_require_identifier(
                row["repo_subscription_id"], field_name="repo_subscription_id"
            ),
            account_id=_require_identifier(row["account_id"], field_name="account_id"),
            owner=_require_identifier(row["owner"], field_name="owner"),
            repo_name=_require_identifier(row["repo_name"], field_name="repo_name"),
            full_name=_require_identifier(row["full_name"], field_name="full_name"),
            external_repo_id=normalize_persisted_text(row["external_repo_id"]),
            default_branch=normalize_persisted_text(row["default_branch"]),
            provider_webhook_id=normalize_persisted_text(row["provider_webhook_id"]),
            subscribed_events=tuple(_load_json_list(row["subscribed_events_json"])),
            webhook_status=GitHubWebhookStatus(str(row["webhook_status"])),
            enabled=bool(row["enabled"]),
            last_webhook_sync_at=parse_persisted_datetime_or_none(
                row["last_webhook_sync_at"]
            ),
            last_error=normalize_persisted_text(row["last_error"]),
            created_at=_require_datetime(row["created_at"]),
            updated_at=_require_datetime(row["updated_at"]),
        )

    def _to_rule_record(self, row: sqlite3.Row) -> TriggerRuleRecord:
        return TriggerRuleRecord(
            trigger_rule_id=_require_identifier(
                row["trigger_rule_id"], field_name="trigger_rule_id"
            ),
            provider=TriggerProvider(str(row["provider"])),
            account_id=_require_identifier(row["account_id"], field_name="account_id"),
            repo_subscription_id=_require_identifier(
                row["repo_subscription_id"], field_name="repo_subscription_id"
            ),
            name=_require_identifier(row["name"], field_name="name"),
            enabled=bool(row["enabled"]),
            match_config=json.loads(str(row["match_config_json"])),
            dispatch_config=json.loads(str(row["dispatch_config_json"])),
            version=int(row["version"]),
            last_error=normalize_persisted_text(row["last_error"]),
            last_fired_at=parse_persisted_datetime_or_none(row["last_fired_at"]),
            created_at=_require_datetime(row["created_at"]),
            updated_at=_require_datetime(row["updated_at"]),
        )

    def _to_delivery_record(self, row: sqlite3.Row) -> TriggerDeliveryRecord:
        return TriggerDeliveryRecord(
            trigger_delivery_id=_require_identifier(
                row["trigger_delivery_id"], field_name="trigger_delivery_id"
            ),
            provider=TriggerProvider(str(row["provider"])),
            provider_delivery_id=normalize_persisted_text(row["provider_delivery_id"]),
            account_id=normalize_persisted_text(row["account_id"]),
            repo_subscription_id=normalize_persisted_text(row["repo_subscription_id"]),
            event_name=_require_identifier(row["event_name"], field_name="event_name"),
            event_action=normalize_persisted_text(row["event_action"]),
            signature_status=TriggerDeliverySignatureStatus(
                str(row["signature_status"])
            ),
            ingest_status=TriggerDeliveryIngestStatus(str(row["ingest_status"])),
            headers=_load_json_string_dict(row["headers_json"]),
            payload=_load_json_value(row["payload_json"]),
            normalized_payload=_load_json_dict(row["normalized_payload_json"]),
            received_at=_require_datetime(row["received_at"]),
            processed_at=parse_persisted_datetime_or_none(row["processed_at"]),
            last_error=normalize_persisted_text(row["last_error"]),
        )

    def _to_evaluation_record(self, row: sqlite3.Row) -> TriggerEvaluationRecord:
        return TriggerEvaluationRecord(
            trigger_evaluation_id=_require_identifier(
                row["trigger_evaluation_id"], field_name="trigger_evaluation_id"
            ),
            trigger_delivery_id=_require_identifier(
                row["trigger_delivery_id"], field_name="trigger_delivery_id"
            ),
            trigger_rule_id=_require_identifier(
                row["trigger_rule_id"], field_name="trigger_rule_id"
            ),
            matched=bool(row["matched"]),
            reason_code=_require_identifier(
                row["reason_code"], field_name="reason_code"
            ),
            reason_detail=normalize_persisted_text(row["reason_detail"]),
            created_at=_require_datetime(row["created_at"]),
        )

    def _to_dispatch_record(self, row: sqlite3.Row) -> TriggerDispatchRecord:
        return TriggerDispatchRecord(
            trigger_dispatch_id=_require_identifier(
                row["trigger_dispatch_id"], field_name="trigger_dispatch_id"
            ),
            trigger_delivery_id=_require_identifier(
                row["trigger_delivery_id"], field_name="trigger_delivery_id"
            ),
            trigger_rule_id=_require_identifier(
                row["trigger_rule_id"], field_name="trigger_rule_id"
            ),
            target_type=TriggerTargetType(str(row["target_type"])),
            status=TriggerDispatchStatus(str(row["status"])),
            session_id=normalize_persisted_text(row["session_id"]),
            run_id=normalize_persisted_text(row["run_id"]),
            automation_project_id=normalize_persisted_text(
                row["automation_project_id"]
            ),
            started_at=parse_persisted_datetime_or_none(row["started_at"]),
            completed_at=parse_persisted_datetime_or_none(row["completed_at"]),
            last_error=normalize_persisted_text(row["last_error"]),
            created_at=_require_datetime(row["created_at"]),
            updated_at=_require_datetime(row["updated_at"]),
        )

    def _to_action_attempt_record(
        self,
        row: sqlite3.Row,
    ) -> TriggerActionAttemptRecord:
        action_spec = GitHubActionSpec.model_validate_json(str(row["action_spec_json"]))
        return TriggerActionAttemptRecord(
            trigger_action_attempt_id=_require_identifier(
                row["trigger_action_attempt_id"], field_name="trigger_action_attempt_id"
            ),
            trigger_dispatch_id=_require_identifier(
                row["trigger_dispatch_id"], field_name="trigger_dispatch_id"
            ),
            phase=TriggerActionPhase(str(row["phase"])),
            action_type=GitHubActionType(str(row["action_type"])),
            status=TriggerActionStatus(str(row["status"])),
            action_spec=action_spec,
            request_payload=_load_json_dict(row["request_payload_json"]),
            response_payload=_load_json_dict(row["response_payload_json"]),
            provider_resource_id=normalize_persisted_text(row["provider_resource_id"]),
            attempt_count=int(row["attempt_count"]),
            last_error=normalize_persisted_text(row["last_error"]),
            created_at=_require_datetime(row["created_at"]),
            updated_at=_require_datetime(row["updated_at"]),
        )


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _load_json_list(value: object) -> list[str]:
    loaded = _load_json_value(value)
    if not isinstance(loaded, list):
        return []
    return [str(item).strip() for item in loaded if str(item).strip()]


def _load_json_dict(value: object) -> dict[str, JsonValue]:
    loaded = _load_json_value(value)
    if not isinstance(loaded, dict):
        return {}
    return _JSON_OBJECT_ADAPTER.validate_python(loaded)


def _load_json_string_dict(value: object) -> dict[str, str]:
    loaded = _load_json_dict(value)
    return {
        str(key): str(item) for key, item in loaded.items() if isinstance(item, str)
    }


def _load_json_value(value: object) -> JsonValue:
    raw = str(value or "")
    if not raw:
        return _JSON_OBJECT_ADAPTER.validate_python({})
    return _JSON_VALUE_ADAPTER.validate_python(json.loads(raw))


def _require_datetime(value: object) -> datetime:
    parsed = parse_persisted_datetime_or_none(value)
    if parsed is None:
        raise ValueError("Missing persisted datetime value")
    return parsed


def _require_identifier(value: object, *, field_name: str) -> str:
    return require_persisted_identifier(
        normalize_persisted_text(value),
        field_name=field_name,
    )


__all__ = [
    "GitHubRepoSubscriptionConflictError",
    "GitHubTriggerAccountNameConflictError",
    "TriggerRepository",
    "TriggerRuleNameConflictError",
]

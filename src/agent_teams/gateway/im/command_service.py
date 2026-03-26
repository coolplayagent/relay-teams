# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Protocol

from agent_teams.gateway.feishu.models import (
    FEISHU_PLATFORM,
    FeishuChatQueueClearResult,
    FeishuChatQueueItemPreview,
    FeishuChatQueueSummary,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
)
from agent_teams.gateway.gateway_session_service import GatewaySessionService
from agent_teams.logger import get_logger, log_event
from agent_teams.providers.token_usage_repo import SessionTokenUsage
from agent_teams.sessions import ExternalSessionBindingRepository, SessionService
from agent_teams.sessions.runs.run_manager import RunManager

_SESSION_COMMANDS: frozenset[str] = frozenset({"help", "status", "clear"})

LOGGER = get_logger(__name__)


class _FeishuQueueLookup(Protocol):
    def get_chat_summary(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
        preview_limit: int = 3,
    ) -> FeishuChatQueueSummary: ...

    def clear_chat(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
    ) -> FeishuChatQueueClearResult: ...


class ImSessionCommandService:
    def __init__(
        self,
        *,
        session_service: SessionService,
        run_service: RunManager,
        external_session_binding_repo: ExternalSessionBindingRepository,
        gateway_session_service: GatewaySessionService,
        feishu_message_pool_service: _FeishuQueueLookup,
    ) -> None:
        self._session_service = session_service
        self._run_service = run_service
        self._external_session_binding_repo = external_session_binding_repo
        self._gateway_session_service = gateway_session_service
        self._feishu_message_pool_service = feishu_message_pool_service

    def is_command(self, text: str) -> bool:
        return self._normalize_command(text) is not None

    def handle_feishu_command(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str | None:
        command = self._normalize_command(message.trigger_text)
        if command is None:
            return None
        if command == "help":
            return self._cmd_help()
        if command == "status":
            return self._cmd_feishu_status(
                runtime_config=runtime_config,
                message=message,
            )
        return self._cmd_feishu_clear(
            runtime_config=runtime_config,
            message=message,
        )

    def handle_wechat_command(
        self,
        *,
        session_id: str,
        gateway_session_id: str,
        text: str,
    ) -> str | None:
        command = self._normalize_command(text)
        if command is None:
            return None
        if command == "help":
            return self._cmd_help()
        if command == "status":
            return self._cmd_wechat_status(session_id=session_id)
        return self._cmd_wechat_clear(
            session_id=session_id,
            gateway_session_id=gateway_session_id,
        )

    @staticmethod
    def _normalize_command(text: str) -> str | None:
        normalized = text.strip().casefold()
        if normalized in _SESSION_COMMANDS:
            return normalized
        return None

    @staticmethod
    def _cmd_help() -> str:
        lines = [
            "[Session Commands]",
            "",
            "help   - Show this help message",
            "status - Show current session state",
            "clear  - Clear session context",
        ]
        return "\n".join(lines)

    def _cmd_feishu_status(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str:
        session_id = self._resolve_feishu_session_id(
            runtime_config=runtime_config,
            message=message,
        )
        queue_summary = self._feishu_message_pool_service.get_chat_summary(
            trigger_id=runtime_config.trigger_id,
            tenant_key=message.tenant_key,
            chat_id=message.chat_id,
        )
        return self._build_status_text(
            session_id=session_id,
            queue_summary=queue_summary,
        )

    def _cmd_wechat_status(self, *, session_id: str) -> str:
        resolved_session_id = self._require_existing_session_id(session_id)
        return self._build_status_text(
            session_id=resolved_session_id,
            queue_summary=None,
        )

    def _cmd_feishu_clear(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str:
        session_id = self._resolve_feishu_session_id(
            runtime_config=runtime_config,
            message=message,
        )
        cleared_session_messages = 0
        if session_id is not None:
            try:
                cleared_session_messages = self._session_service.clear_session_messages(
                    session_id
                )
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="im.command.feishu.clear_session_failed",
                    message="Failed to clear Feishu session messages",
                    payload={"session_id": session_id, "error": str(exc)},
                )
                return "[Clear] Failed to clear session messages."
        try:
            queue_result = self._feishu_message_pool_service.clear_chat(
                trigger_id=runtime_config.trigger_id,
                tenant_key=message.tenant_key,
                chat_id=message.chat_id,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="im.command.feishu.clear_queue_failed",
                message="Failed to clear queued Feishu messages",
                payload={
                    "trigger_id": runtime_config.trigger_id,
                    "tenant_key": message.tenant_key,
                    "chat_id": message.chat_id,
                    "error": str(exc),
                },
            )
            return "[Clear] Failed to clear queued messages."
        if session_id is None and queue_result.cleared_queue_count == 0:
            return "[Clear] No active session or queued messages. Nothing to clear."
        return (
            "[Clear] "
            f"Cleared {cleared_session_messages} active session messages and "
            f"{queue_result.cleared_queue_count} queued messages. "
            f"Stopped {queue_result.stopped_run_count} active runs."
        )

    def _cmd_wechat_clear(
        self,
        *,
        session_id: str,
        gateway_session_id: str,
    ) -> str:
        resolved_session_id = self._require_existing_session_id(session_id)
        if resolved_session_id is None:
            try:
                self._gateway_session_service.bind_active_run(gateway_session_id, None)
            except KeyError as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="im.command.wechat.clear_binding_failed",
                    message="Failed to clear WeChat active run binding",
                    payload={
                        "session_id": session_id,
                        "gateway_session_id": gateway_session_id,
                        "error": str(exc),
                    },
                )
            return "[Clear] No active session state. Nothing to clear."
        recovery_snapshot = self._session_service.get_recovery_snapshot(
            resolved_session_id
        )
        cleared_session_messages = 0
        try:
            cleared_session_messages = self._session_service.clear_session_messages(
                resolved_session_id
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="im.command.wechat.clear_session_failed",
                message="Failed to clear WeChat session messages",
                payload={"session_id": resolved_session_id, "error": str(exc)},
            )
            return "[Clear] Failed to clear session messages."
        stopped_run_count = 0
        active_run = recovery_snapshot.get("active_run")
        if isinstance(active_run, Mapping):
            run_id = str(active_run.get("run_id") or "").strip()
            if run_id:
                try:
                    self._run_service.stop_run(run_id)
                    stopped_run_count = 1
                except Exception as exc:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        event="im.command.wechat.stop_run_failed",
                        message="Failed to stop active WeChat run while clearing",
                        payload={
                            "session_id": resolved_session_id,
                            "gateway_session_id": gateway_session_id,
                            "run_id": run_id,
                            "error": str(exc),
                        },
                    )
        try:
            self._gateway_session_service.bind_active_run(gateway_session_id, None)
        except KeyError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="im.command.wechat.clear_binding_failed",
                message="Failed to clear WeChat active run binding",
                payload={
                    "session_id": resolved_session_id,
                    "gateway_session_id": gateway_session_id,
                    "error": str(exc),
                },
            )
        if cleared_session_messages == 0 and stopped_run_count == 0:
            return "[Clear] No active session state. Nothing to clear."
        return (
            "[Clear] "
            f"Cleared {cleared_session_messages} active session messages. "
            f"Stopped {stopped_run_count} active runs."
        )

    def _build_status_text(
        self,
        *,
        session_id: str | None,
        queue_summary: FeishuChatQueueSummary | None,
    ) -> str:
        lines = ["[Session Status]", ""]
        if session_id is None:
            lines.append("Session: (none)")
        else:
            messages = self._session_service.get_session_messages(session_id)
            usage = self._session_service.get_token_usage_by_session(session_id)
            recovery_snapshot = self._session_service.get_recovery_snapshot(session_id)
            lines.extend(self._build_session_summary_lines(session_id, messages, usage))
            active_run_lines = self._build_active_run_lines(recovery_snapshot)
            if active_run_lines:
                lines.append("")
                lines.extend(active_run_lines)
        if queue_summary is not None:
            lines.append("")
            lines.append(
                "Queue: "
                f"active={queue_summary.active_total} "
                f"queued={queue_summary.queued_count} "
                f"claimed={queue_summary.claimed_count} "
                f"waiting={queue_summary.waiting_result_count} "
                f"retryable_failed={queue_summary.retryable_failed_count} "
                f"dead_letter={queue_summary.dead_letter_count} "
                f"cancelled={queue_summary.cancelled_count}"
            )
            if queue_summary.processing_item is not None:
                lines.append("")
                lines.append(
                    "Processing: "
                    + self._format_queue_item(queue_summary.processing_item)
                )
            if queue_summary.queued_items:
                lines.append("")
                lines.append("Queued messages:")
                for item in queue_summary.queued_items:
                    lines.append("  " + self._format_queue_item(item))
        return "\n".join(lines)

    def _build_session_summary_lines(
        self,
        session_id: str,
        messages: list[dict[str, object]],
        usage: SessionTokenUsage,
    ) -> list[str]:
        lines = [
            f"Session: {session_id}",
            f"Messages: {len(messages)}",
            f"Tokens: input={usage.total_input_tokens}"
            f"  output={usage.total_output_tokens}"
            f"  total={usage.total_tokens}",
            f"Requests: {usage.total_requests}",
        ]
        recent = messages[-3:]
        if recent:
            lines.append("")
            lines.append("Recent messages:")
            for msg in recent:
                role = str(msg.get("role", "unknown"))
                preview = self._extract_content_preview(msg)
                lines.append(f"  [{role}] {preview}")
        return lines

    @staticmethod
    def _build_active_run_lines(
        recovery_snapshot: Mapping[str, object],
    ) -> list[str]:
        active_run = recovery_snapshot.get("active_run")
        if not isinstance(active_run, Mapping):
            return []
        run_id = str(active_run.get("run_id") or "").strip()
        status = str(active_run.get("status") or "").strip()
        phase = str(active_run.get("phase") or "").strip()
        if not run_id:
            return []
        segments = [f"Run: {run_id}"]
        if status:
            segments.append(f"status={status}")
        if phase:
            segments.append(f"phase={phase}")
        return [" | ".join(segments)]

    def _resolve_feishu_session_id(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        message: FeishuNormalizedMessage,
    ) -> str | None:
        binding = self._external_session_binding_repo.get_binding(
            platform=FEISHU_PLATFORM,
            trigger_id=runtime_config.trigger_id,
            tenant_key=message.tenant_key,
            external_chat_id=message.chat_id,
        )
        if binding is None:
            return None
        return self._require_existing_session_id(binding.session_id)

    def _require_existing_session_id(self, session_id: str) -> str | None:
        try:
            self._session_service.get_session(session_id)
        except KeyError:
            return None
        return session_id

    @staticmethod
    def _format_queue_item(item: FeishuChatQueueItemPreview) -> str:
        segments = [item.processing_status.value]
        if item.intent_preview:
            segments.append(item.intent_preview)
        if item.run_id:
            segments.append(f"run={item.run_id}")
        if item.run_status:
            segments.append(f"status={item.run_status}")
        if item.run_phase:
            segments.append(f"phase={item.run_phase}")
        if item.blocking_reason:
            segments.append(f"blocked={item.blocking_reason}")
        if item.last_error:
            segments.append(f"error={item.last_error}")
        return " | ".join(segments)

    @staticmethod
    def _extract_content_preview(
        msg: Mapping[str, object],
        *,
        max_length: int = 60,
    ) -> str:
        message_payload = msg.get("message")
        if not isinstance(message_payload, Mapping):
            return "(no content)"
        parts = message_payload.get("parts")
        if not isinstance(parts, list) or not parts:
            return "(no content)"
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            kind = str(part.get("part_kind", ""))
            if kind not in {"user-prompt", "text"}:
                continue
            content = part.get("content")
            if isinstance(content, str) and content.strip():
                text = content.strip().replace("\n", " ")
                if len(text) > max_length:
                    return text[:max_length] + "..."
                return text
        return "(no content)"

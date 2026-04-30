from __future__ import annotations

from threading import Lock

from relay_teams.media import UserPromptContent, user_prompt_content_to_text
from relay_teams.sessions.runs.enums import InjectionDeliveryMode, InjectionSource
from relay_teams.sessions.runs.run_models import InjectionMessage
from relay_teams.system_reminder_text import is_rendered_system_reminder_text


class RunInjectionManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active_runs: set[str] = set()
        self._queues: dict[str, dict[str, list[InjectionMessage]]] = {}

    def activate(self, run_id: str) -> None:
        with self._lock:
            self._active_runs.add(run_id)
            self._queues.setdefault(run_id, {})

    def deactivate(self, run_id: str) -> None:
        with self._lock:
            self._active_runs.discard(run_id)
            self._queues.pop(run_id, None)

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active_runs

    # noinspection PyTypeHints
    def enqueue(
        self,
        run_id: str,
        recipient_instance_id: str,
        source: InjectionSource,
        content: UserPromptContent,
        delivery_mode: InjectionDeliveryMode = InjectionDeliveryMode.QUEUED,
        sender_instance_id: str | None = None,
        sender_role_id: str | None = None,
        client_message_id: str | None = None,
        visibility: str = "public",
        internal_kind: str = "",
        internal_delivery_mode: str = "",
        internal_issue_key: str = "",
    ) -> InjectionMessage:
        priority = _priority_for(source)
        message = InjectionMessage(
            run_id=run_id,
            recipient_instance_id=recipient_instance_id,
            source=source,
            delivery_mode=delivery_mode,
            visibility="internal" if visibility == "internal" else "public",
            internal_kind=internal_kind,
            internal_delivery_mode=internal_delivery_mode,
            internal_issue_key=internal_issue_key,
            content=content,
            client_message_id=client_message_id,
            sender_instance_id=sender_instance_id,
            sender_role_id=sender_role_id,
            priority=priority,
        )
        with self._lock:
            if run_id not in self._active_runs:
                raise KeyError(f"Run is not active: {run_id}")
            per_run = self._queues.setdefault(run_id, {})
            per_run.setdefault(recipient_instance_id, []).append(message)
        return message

    def drain_at_boundary(
        self, run_id: str, recipient_instance_id: str
    ) -> tuple[InjectionMessage, ...]:
        with self._lock:
            queue = self._queues.get(run_id, {}).get(recipient_instance_id, [])
            if not queue:
                return ()
            ordered = sorted(queue, key=lambda item: (item.priority, item.created_at))
            self._queues.setdefault(run_id, {})[recipient_instance_id] = []
        return tuple(ordered)

    def drain_interrupt(
        self, run_id: str, recipient_instance_id: str
    ) -> tuple[InjectionMessage, ...]:
        with self._lock:
            queue = self._queues.get(run_id, {}).get(recipient_instance_id, [])
            if not queue:
                return ()
            interrupting: list[InjectionMessage] = []
            remaining: list[InjectionMessage] = []
            for message in queue:
                if message.delivery_mode == InjectionDeliveryMode.INTERRUPT:
                    interrupting.append(message)
                else:
                    remaining.append(message)
            if not interrupting:
                return ()
            self._queues.setdefault(run_id, {})[recipient_instance_id] = remaining
        return tuple(
            sorted(interrupting, key=lambda item: (item.priority, item.created_at))
        )

    def force_user_queued_to_interrupt(
        self,
        run_id: str,
        recipient_instance_id: str,
    ) -> InjectionMessage:
        with self._lock:
            if run_id not in self._active_runs:
                raise KeyError(f"Run is not active: {run_id}")
            queue = self._queues.get(run_id, {}).get(recipient_instance_id, [])
            if not queue:
                raise ValueError("No queued injections to force")
            promotable: list[InjectionMessage] = []
            remaining: list[InjectionMessage] = []
            for message in queue:
                if (
                    message.source == InjectionSource.USER
                    and message.delivery_mode == InjectionDeliveryMode.QUEUED
                    and message.visibility == "public"
                ):
                    promotable.append(message)
                else:
                    remaining.append(message)
            if not promotable:
                raise ValueError("No queued injections to force")
            ordered = sorted(
                promotable, key=lambda item: (item.priority, item.created_at)
            )
            merged_content = _merge_user_messages(ordered)
            promoted = InjectionMessage(
                run_id=run_id,
                recipient_instance_id=recipient_instance_id,
                source=InjectionSource.USER,
                delivery_mode=InjectionDeliveryMode.INTERRUPT,
                content=merged_content,
                superseded_injection_ids=tuple(
                    message.injection_id for message in ordered
                ),
                superseded_client_message_ids=tuple(
                    message.client_message_id
                    for message in ordered
                    if message.client_message_id
                ),
                priority=_priority_for(InjectionSource.USER),
            )
            self._queues.setdefault(run_id, {})[recipient_instance_id] = [
                *remaining,
                promoted,
            ]
        return promoted

    def drain_system_reminders_at_start(
        self, run_id: str, recipient_instance_id: str
    ) -> tuple[InjectionMessage, ...]:
        with self._lock:
            queue = self._queues.get(run_id, {}).get(recipient_instance_id, [])
            if not queue:
                return ()
            reminders: list[InjectionMessage] = []
            remaining: list[InjectionMessage] = []
            for message in queue:
                if _is_system_reminder_injection(message):
                    reminders.append(message)
                else:
                    remaining.append(message)
            if not reminders:
                return ()
            self._queues.setdefault(run_id, {})[recipient_instance_id] = remaining
        return tuple(
            sorted(reminders, key=lambda item: (item.priority, item.created_at))
        )


def _priority_for(source: InjectionSource) -> int:
    if source == InjectionSource.SYSTEM:
        return 0
    if source == InjectionSource.USER:
        return 1
    return 2


def _is_system_reminder_injection(message: InjectionMessage) -> bool:
    if message.source != InjectionSource.SYSTEM:
        return False
    text = user_prompt_content_to_text(message.content).strip()
    return is_rendered_system_reminder_text(text)


def _merge_user_messages(messages: list[InjectionMessage]) -> str:
    parts = [
        user_prompt_content_to_text(message.content).strip()
        for message in messages
        if user_prompt_content_to_text(message.content).strip()
    ]
    return "\n\n".join(parts).strip()

from __future__ import annotations

from enum import Enum
from json import dumps

from relay_teams.media import user_prompt_content_to_text
from relay_teams.sessions.runs.enums import InjectionSource
from relay_teams.sessions.runs.run_models import InjectionMessage
from relay_teams.system_reminder_delivery import SystemReminderDeliveryMode
from relay_teams.system_reminder_text import is_rendered_system_reminder_text


class InjectionDisposition(str, Enum):
    APPLY = "apply"
    DISCARD = "discard"


class InjectionBoundaryContext:
    def __init__(self, *, final_answer_ready: bool = False) -> None:
        self.final_answer_ready = final_answer_ready


class RunInjectionClassifier:
    def disposition(
        self,
        message: InjectionMessage,
        *,
        context: InjectionBoundaryContext,
    ) -> InjectionDisposition:
        if (
            context.final_answer_ready
            and self.is_internal_system_reminder(message)
            and self.delivery_mode(message) == SystemReminderDeliveryMode.GUIDANCE
        ):
            return InjectionDisposition.DISCARD
        return InjectionDisposition.APPLY

    @staticmethod
    def is_internal_system_reminder(message: InjectionMessage) -> bool:
        if message.source != InjectionSource.SYSTEM:
            return False
        text = user_prompt_content_to_text(message.content).strip()
        return is_rendered_system_reminder_text(text)

    @staticmethod
    def delivery_mode(message: InjectionMessage) -> SystemReminderDeliveryMode:
        try:
            return SystemReminderDeliveryMode(message.internal_delivery_mode)
        except ValueError:
            return SystemReminderDeliveryMode.GUIDANCE


INJECTION_CLASSIFIER = RunInjectionClassifier()


def public_injection_payload_json(message: InjectionMessage) -> str:
    if not INJECTION_CLASSIFIER.is_internal_system_reminder(message):
        return message.model_dump_json()
    text = user_prompt_content_to_text(message.content)
    payload: dict[str, object] = {
        "run_id": message.run_id,
        "recipient_instance_id": message.recipient_instance_id,
        "source": message.source.value,
        "visibility": message.visibility,
        "internal_kind": message.internal_kind,
        "internal_delivery_mode": message.internal_delivery_mode,
        "internal_issue_key": message.internal_issue_key,
        "content_redacted": True,
        "content_length": len(text),
        "sender_instance_id": message.sender_instance_id,
        "sender_role_id": message.sender_role_id,
        "priority": message.priority,
        "created_at": message.created_at.isoformat(),
    }
    return dumps(payload, ensure_ascii=False)

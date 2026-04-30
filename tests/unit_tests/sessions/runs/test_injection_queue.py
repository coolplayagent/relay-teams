import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ImageUrl

from relay_teams.media import user_prompt_content_to_text
from relay_teams.reminders import render_system_reminder
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.enums import InjectionDeliveryMode, InjectionSource
from relay_teams.sessions.runs.run_models import InjectionMessage


def test_injection_manager_isolated_by_recipient() -> None:
    mgr = RunInjectionManager()
    mgr.activate("run1")

    mgr.enqueue(
        "run1",
        "a1",
        InjectionSource.SUBAGENT,
        "m1",
        sender_instance_id="b1",
        sender_role_id="generalist",
    )
    mgr.enqueue(
        "run1",
        "a2",
        InjectionSource.SUBAGENT,
        "m2",
        sender_instance_id="b1",
        sender_role_id="generalist",
    )

    a1 = mgr.drain_at_boundary("run1", "a1")
    a2 = mgr.drain_at_boundary("run1", "a2")

    assert len(a1) == 1
    assert a1[0].content == "m1"
    assert len(a2) == 1
    assert a2[0].content == "m2"


def test_injection_manager_preserves_structured_prompt_content() -> None:
    mgr = RunInjectionManager()
    mgr.activate("run1")

    mgr.enqueue(
        "run1",
        "a1",
        InjectionSource.SYSTEM,
        (
            "inspect this image",
            ImageUrl(
                url="/api/sessions/session-1/media/asset-1/file",
                media_type="image/png",
            ),
        ),
    )

    injected = mgr.drain_at_boundary("run1", "a1")

    assert len(injected) == 1
    assert user_prompt_content_to_text(injected[0].content) == (
        "inspect this image\n\n[image: file]"
    )


def test_injection_manager_drains_system_reminders_at_start_only() -> None:
    mgr = RunInjectionManager()
    mgr.activate("run1")
    reminder = render_system_reminder("Check todos.")

    mgr.enqueue("run1", "a1", InjectionSource.USER, "follow up")
    mgr.enqueue("run1", "a1", InjectionSource.SYSTEM, reminder)
    mgr.enqueue("run1", "a1", InjectionSource.SYSTEM, "plain system note")
    mgr.enqueue(
        "run1",
        "a1",
        InjectionSource.SYSTEM,
        "<system-reminder>\nUser-authored wrapper.\n</system-reminder>",
    )

    startup = mgr.drain_system_reminders_at_start("run1", "a1")
    remaining = mgr.drain_at_boundary("run1", "a1")

    assert [message.content for message in startup] == [reminder]
    assert [message.content for message in remaining] == [
        "plain system note",
        "<system-reminder>\nUser-authored wrapper.\n</system-reminder>",
        "follow up",
    ]


def test_injection_manager_drains_interrupt_messages_first() -> None:
    mgr = RunInjectionManager()
    mgr.activate("run1")

    mgr.enqueue("run1", "a1", InjectionSource.USER, "queued")
    mgr.enqueue(
        "run1",
        "a1",
        InjectionSource.USER,
        "interrupt",
        delivery_mode=InjectionDeliveryMode.INTERRUPT,
    )

    interrupted = mgr.drain_interrupt("run1", "a1")
    remaining = mgr.drain_at_boundary("run1", "a1")

    assert [message.content for message in interrupted] == ["interrupt"]
    assert interrupted[0].delivery_mode == InjectionDeliveryMode.INTERRUPT
    assert [message.content for message in remaining] == ["queued"]


def test_injection_manager_forces_queued_user_injections_to_interrupt() -> None:
    mgr = RunInjectionManager()
    mgr.activate("run1")

    first = mgr.enqueue(
        "run1",
        "a1",
        InjectionSource.USER,
        "first",
        client_message_id="client-first",
    )
    mgr.enqueue("run1", "a1", InjectionSource.SYSTEM, "system note")
    second = mgr.enqueue(
        "run1",
        "a1",
        InjectionSource.USER,
        "second",
        client_message_id="client-second",
    )

    promoted = mgr.force_user_queued_to_interrupt("run1", "a1")
    interrupted = mgr.drain_interrupt("run1", "a1")
    remaining = mgr.drain_at_boundary("run1", "a1")

    assert promoted.delivery_mode == InjectionDeliveryMode.INTERRUPT
    assert promoted.content == "first\n\nsecond"
    assert promoted.superseded_injection_ids == (
        first.injection_id,
        second.injection_id,
    )
    assert promoted.superseded_client_message_ids == (
        "client-first",
        "client-second",
    )
    assert [message.content for message in interrupted] == ["first\n\nsecond"]
    assert [message.content for message in remaining] == ["system note"]


def test_injection_manager_force_requires_active_run() -> None:
    mgr = RunInjectionManager()

    with pytest.raises(KeyError, match="Run is not active"):
        mgr.force_user_queued_to_interrupt("run1", "a1")


def test_injection_manager_force_requires_queued_message() -> None:
    mgr = RunInjectionManager()
    mgr.activate("run1")

    with pytest.raises(ValueError, match="No queued injections"):
        mgr.force_user_queued_to_interrupt("run1", "a1")


def test_injection_manager_force_requires_user_queued_message() -> None:
    mgr = RunInjectionManager()
    mgr.activate("run1")
    mgr.enqueue("run1", "a1", InjectionSource.SYSTEM, "system note")

    with pytest.raises(ValueError, match="No queued injections"):
        mgr.force_user_queued_to_interrupt("run1", "a1")


def test_injection_manager_startup_drain_returns_empty_without_runtime_reminders() -> (
    None
):
    mgr = RunInjectionManager()
    mgr.activate("run1")

    assert mgr.drain_system_reminders_at_start("run1", "a1") == ()

    mgr.enqueue("run1", "a1", InjectionSource.SYSTEM, "plain system note")

    assert mgr.drain_system_reminders_at_start("run1", "a1") == ()
    assert [message.content for message in mgr.drain_at_boundary("run1", "a1")] == [
        "plain system note"
    ]


def test_injection_message_serializes_structured_prompt_content() -> None:
    message = InjectionMessage(
        run_id="run-1",
        recipient_instance_id="a1",
        source=InjectionSource.SYSTEM,
        content=(
            "inspect this image",
            ImageUrl(
                url="/api/sessions/session-1/media/asset-1/file",
                media_type="image/png",
            ),
        ),
        priority=0,
    )

    payload = message.model_dump(mode="json")

    assert payload["content"][0] == "inspect this image"
    image_payload = payload["content"][1]
    assert image_payload["url"] == "/api/sessions/session-1/media/asset-1/file"
    assert image_payload["kind"] == "image-url"
    assert image_payload["media_type"] == "image/png"


def test_injection_message_serializes_client_message_id() -> None:
    message = InjectionMessage(
        run_id="run-1",
        recipient_instance_id="a1",
        source=InjectionSource.USER,
        content="follow up",
        client_message_id="client-1",
        priority=1,
    )

    payload = message.model_dump(mode="json")

    assert payload["client_message_id"] == "client-1"


def test_injection_message_rejects_whitespace_only_content() -> None:
    with pytest.raises(ValidationError, match="Injection content must not be empty"):
        InjectionMessage(
            run_id="run-1",
            recipient_instance_id="a1",
            source=InjectionSource.SYSTEM,
            content="   ",
            priority=0,
        )

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ImageUrl

from relay_teams.media import user_prompt_content_to_text
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.enums import InjectionSource
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


def test_injection_message_rejects_whitespace_only_content() -> None:
    with pytest.raises(ValidationError, match="Injection content must not be empty"):
        InjectionMessage(
            run_id="run-1",
            recipient_instance_id="a1",
            source=InjectionSource.SYSTEM,
            content="   ",
            priority=0,
        )

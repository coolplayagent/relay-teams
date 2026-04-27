from __future__ import annotations

import pytest

from pydantic import ValidationError

from relay_teams.agents.tasks.enums import TaskSpecStrictness, TaskTimeoutAction
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskHandoff,
    TaskLifecyclePolicy,
    TaskSpec,
    VerificationCommand,
    VerificationPlan,
    _split_command_string,
)


def test_task_envelope_requires_fields() -> None:
    with pytest.raises(ValidationError):
        TaskEnvelope(
            task_id="",
            session_id="s1",
            trace_id="t1",
            objective="obj",
            verification=VerificationPlan(checklist=("echo",)),
        )


def test_task_envelope_accepts_spec_lifecycle_and_handoff() -> None:
    envelope = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Implement endpoint",
        verification=VerificationPlan(),
        spec=TaskSpec(
            summary="Add API endpoint",
            requirements=("persist state", ""),
            constraints=("use pathlib",),
            acceptance_criteria=("unit tests pass",),
            evidence_expectations=("pytest output",),
            strictness=TaskSpecStrictness.HIGH,
        ),
        lifecycle=TaskLifecyclePolicy(
            timeout_seconds=30,
            heartbeat_interval_seconds=5,
            on_timeout=TaskTimeoutAction.HUMAN_GATE,
        ),
        handoff=TaskHandoff(next_steps=("rerun tests",), reason="paused"),
    )

    assert envelope.spec is not None
    assert envelope.spec.requirements == ("persist state",)
    assert envelope.lifecycle.on_timeout == TaskTimeoutAction.HUMAN_GATE
    assert envelope.handoff is not None
    assert envelope.handoff.next_steps == ("rerun tests",)


def test_task_contract_models_normalize_optional_text_inputs() -> None:
    command = VerificationCommand.model_validate({"command": "pytest -q"})
    verification = VerificationPlan.model_validate(
        {
            "checklist": None,
            "acceptance_criteria": "unit tests pass",
            "evidence_expectations": (" coverage output ", ""),
        }
    )
    spec = TaskSpec.model_validate({"summary": None, "requirements": "persist state"})
    handoff = TaskHandoff.model_validate({"reason": None, "completed": "implemented"})

    assert command.command == ("pytest", "-q")
    assert verification.checklist == ("non_empty_response",)
    assert verification.acceptance_criteria == ("unit tests pass",)
    assert verification.evidence_expectations == ("coverage output",)
    assert spec.summary == ""
    assert spec.requirements == ("persist state",)
    assert handoff.reason == ""
    assert handoff.completed == ("implemented",)


def test_verification_command_uses_windows_aware_string_splitting() -> None:
    assert _split_command_string(
        r"C:\tmp\check.py --flag",
        platform="win32",
    ) == (r"C:\tmp\check.py", "--flag")
    assert _split_command_string(
        r'"C:\Program Files\Python\python.exe" "C:\tmp\check.py"',
        platform="win32",
    ) == (r"C:\Program Files\Python\python.exe", r"C:\tmp\check.py")
    assert _split_command_string(
        r'python -c "print(\"hi\")"',
        platform="win32",
    ) == ("python", "-c", 'print("hi")')


def test_task_contract_models_reject_non_text_sequences() -> None:
    with pytest.raises(TypeError, match="checklist"):
        VerificationPlan.model_validate({"checklist": object()})

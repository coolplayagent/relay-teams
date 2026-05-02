# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.agents.orchestration.harnesses.prompt_harness import TaskPromptHarness
from relay_teams.agents.tasks.enums import (
    FormalVerificationLanguage,
    FormalVerificationToolProfile,
    TaskSpecStrictness,
    TaskSpecSyncStatus,
)
from relay_teams.agents.tasks.models import (
    FormalVerificationPlan,
    TaskEnvelope,
    TaskSpec,
    VerificationCommand,
    VerificationPlan,
)


def test_task_contract_prompt_renders_formal_verification_details() -> None:
    prompt = TaskPromptHarness.task_contract_prompt(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            trace_id="run-1",
            objective="Implement the formal contract",
            verification=VerificationPlan(),
            spec_artifact_id="spec-1",
            spec_source_task_id="task-source",
            spec=TaskSpec(
                summary="Formal lifecycle contract",
                requirements=("persist spec",),
                acceptance_criteria=("proof replays",),
                evidence_expectations=("tlc output",),
                strictness=TaskSpecStrictness.HIGH,
                prompt_artifact_version=3,
                prompt_code_sync_status=TaskSpecSyncStatus.IN_SYNC,
                formal_verification=FormalVerificationPlan(
                    spec_language=FormalVerificationLanguage.TLA_PLUS,
                    tool_profile=FormalVerificationToolProfile.TLC,
                    properties=("Invariant",),
                    proof_artifacts=(Path("model.tla"),),
                    counterexample_path=Path("counterexample.out"),
                    replay_command=VerificationCommand(
                        command=("tlc", "model.tla"),
                    ),
                ),
            ),
        )
    )

    assert "- Formal Verification:" in prompt
    assert "  - Spec Language: tla_plus" in prompt
    assert "  - Tool Profile: tlc" in prompt
    assert "    - Invariant" in prompt
    assert "    - model.tla" in prompt
    assert "  - Counterexample Path: counterexample.out" in prompt
    assert "  - Replay Command: tlc model.tla" in prompt
    assert "- Completion Evidence: cite each acceptance criterion" in prompt

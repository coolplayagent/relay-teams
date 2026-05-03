# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.agents.tasks.ids import new_task_id
from relay_teams.agents.tasks.models import (
    SpecArtifactDiffFieldChange,
    SpecCheckpointEvaluation,
    TaskEnvelope,
    TaskSpec,
    VerificationPlan,
)
from relay_teams.agents.tasks.spec_artifact_diff_service import (
    SpecArtifactDiffService,
    _build_diff_summary,
    _diff_task_specs,
)
from relay_teams.agents.tasks.task_repository import TaskRepository


def _make_spec(
    *,
    summary: str = "Test task",
    requirements: tuple[str, ...] = (),
    constraints: tuple[str, ...] = (),
    acceptance_criteria: tuple[str, ...] = (),
    out_of_scope: tuple[str, ...] = (),
    entities: tuple[str, ...] = (),
    approach: tuple[str, ...] = (),
    structure: tuple[str, ...] = (),
    operations: tuple[str, ...] = (),
    norms: tuple[str, ...] = (),
    safeguards: tuple[str, ...] = (),
) -> TaskSpec:
    return TaskSpec(
        summary=summary,
        requirements=requirements,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        out_of_scope=out_of_scope,
        entities=entities,
        approach=approach,
        structure=structure,
        operations=operations,
        norms=norms,
        safeguards=safeguards,
    )


def _make_evaluation(
    *,
    task_id: str,
    artifact_id: str,
    checkpoint_seq: int = 1,
    overall_score: float = 4.5,
    drift_detected: bool = False,
) -> SpecCheckpointEvaluation:
    return SpecCheckpointEvaluation(
        evaluation_id=f"speval-{checkpoint_seq}",
        task_id=task_id,
        artifact_id=artifact_id,
        session_id="session-1",
        trace_id="trace-1",
        checkpoint_seq=checkpoint_seq,
        evaluator="test",
        overall_score=overall_score,
        drift_detected=drift_detected,
    )


def _create_task(
    repo: TaskRepository, task_id: str, *, spec: TaskSpec | None = None
) -> None:
    repo.create(
        TaskEnvelope(
            task_id=str(task_id),
            session_id="session-1",
            parent_task_id=None,
            trace_id="trace-1",
            objective="test spec artifacts",
            verification=VerificationPlan(),
            spec=spec,
        )
    )


@pytest.fixture
def repo(tmp_path: Path) -> TaskRepository:
    return TaskRepository(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Pure diff unit tests (no database)
# ---------------------------------------------------------------------------


class TestDiffTaskSpecsNoChanges:
    def test_identical_specs_report_no_changes(self) -> None:
        spec = _make_spec(
            summary="Hello",
            requirements=("r1", "r2"),
            constraints=("c1",),
        )
        changes = _diff_task_specs(spec, spec)
        assert all(c.change_type == "unchanged" for c in changes)

    def test_empty_specs_report_no_changes(self) -> None:
        spec = _make_spec()
        changes = _diff_task_specs(spec, spec)
        assert all(c.change_type == "unchanged" for c in changes)


class TestDiffTaskSpecsFieldModified:
    def test_summary_change_detected(self) -> None:
        old_spec = _make_spec(summary="Original")
        new_spec = _make_spec(summary="Modified")
        changes = _diff_task_specs(old_spec, new_spec)
        summary_change = next(c for c in changes if c.field_name == "summary")
        assert summary_change.change_type == "modified"
        assert summary_change.old_value == "Original"
        assert summary_change.new_value == "Modified"

    def test_requirements_addition_detected(self) -> None:
        old_spec = _make_spec(requirements=("r1",))
        new_spec = _make_spec(requirements=("r1", "r2"))
        changes = _diff_task_specs(old_spec, new_spec)
        req_change = next(c for c in changes if c.field_name == "requirements")
        assert req_change.change_type == "modified"
        assert "r2" in req_change.added_items
        assert len(req_change.removed_items) == 0

    def test_constraints_removal_detected(self) -> None:
        old_spec = _make_spec(constraints=("c1", "c2"))
        new_spec = _make_spec(constraints=("c1",))
        changes = _diff_task_specs(old_spec, new_spec)
        constraint_change = next(c for c in changes if c.field_name == "constraints")
        assert constraint_change.change_type == "modified"
        assert "c2" in constraint_change.removed_items
        assert len(constraint_change.added_items) == 0


class TestDiffTaskSpecsFieldAdded:
    def test_tuple_field_going_from_empty_to_populated(self) -> None:
        old_spec = _make_spec(entities=())
        new_spec = _make_spec(entities=("User", "Order"))
        changes = _diff_task_specs(old_spec, new_spec)
        entity_change = next(c for c in changes if c.field_name == "entities")
        assert entity_change.change_type == "added"
        assert len(entity_change.added_items) == 2


class TestDiffTaskSpecsFieldRemoved:
    def test_tuple_field_going_from_populated_to_empty(self) -> None:
        old_spec = _make_spec(approach=("step1", "step2"))
        new_spec = _make_spec(approach=())
        changes = _diff_task_specs(old_spec, new_spec)
        approach_change = next(c for c in changes if c.field_name == "approach")
        assert approach_change.change_type == "removed"
        assert len(approach_change.removed_items) == 2


class TestDiffTaskSpecsMultipleFields:
    def test_multiple_field_changes(self) -> None:
        old_spec = _make_spec(
            summary="Old summary",
            requirements=("r1",),
            safeguards=("s1",),
        )
        new_spec = _make_spec(
            summary="New summary",
            requirements=("r1", "r2"),
            safeguards=(),
        )
        changes = _diff_task_specs(old_spec, new_spec)
        changed_fields = [c.field_name for c in changes if c.change_type != "unchanged"]
        assert "summary" in changed_fields
        assert "requirements" in changed_fields
        assert "safeguards" in changed_fields


class TestBuildDiffSummary:
    def test_no_changes_summary(self) -> None:
        changes: tuple[SpecArtifactDiffFieldChange, ...] = (
            SpecArtifactDiffFieldChange(
                field_name="summary",
                field_label="Summary",
                change_type="unchanged",
            ),
        )
        summary = _build_diff_summary(changes)
        assert "No changes" in summary

    def test_single_change_summary(self) -> None:
        changes: tuple[SpecArtifactDiffFieldChange, ...] = (
            SpecArtifactDiffFieldChange(
                field_name="summary",
                field_label="Summary",
                change_type="modified",
                old_value="old",
                new_value="new",
            ),
        )
        summary = _build_diff_summary(changes)
        assert "1 field changed" in summary
        assert "Summary" in summary

    def test_multiple_changes_summary(self) -> None:
        changes: tuple[SpecArtifactDiffFieldChange, ...] = (
            SpecArtifactDiffFieldChange(
                field_name="summary",
                field_label="Summary",
                change_type="modified",
            ),
            SpecArtifactDiffFieldChange(
                field_name="requirements",
                field_label="Requirements",
                change_type="modified",
                added_items=("r2",),
                removed_items=(),
            ),
        )
        summary = _build_diff_summary(changes)
        assert "2 fields changed" in summary

    def test_tuple_items_count_in_summary(self) -> None:
        changes: tuple[SpecArtifactDiffFieldChange, ...] = (
            SpecArtifactDiffFieldChange(
                field_name="requirements",
                field_label="Requirements",
                change_type="modified",
                added_items=("r2", "r3"),
                removed_items=("r1",),
            ),
        )
        summary = _build_diff_summary(changes)
        assert "+2 items" in summary
        assert "-1 item" in summary


class TestDiffTaskSpecsFormalVerification:
    def test_formal_verification_added(self) -> None:
        from relay_teams.agents.tasks.enums import FormalVerificationLanguage
        from relay_teams.agents.tasks.models import FormalVerificationPlan

        old_spec = _make_spec()
        new_spec = _make_spec().model_copy(
            update={
                "formal_verification": FormalVerificationPlan(
                    spec_language=FormalVerificationLanguage.TLA_PLUS,
                    properties=("Safety",),
                )
            }
        )
        changes = _diff_task_specs(old_spec, new_spec)
        fv_change = next(c for c in changes if c.field_name == "formal_verification")
        assert fv_change.change_type == "added"

    def test_formal_verification_removed(self) -> None:
        from relay_teams.agents.tasks.enums import FormalVerificationLanguage
        from relay_teams.agents.tasks.models import FormalVerificationPlan

        fv = FormalVerificationPlan(
            spec_language=FormalVerificationLanguage.TLA_PLUS,
            properties=("Safety",),
        )
        old_spec = _make_spec().model_copy(update={"formal_verification": fv})
        new_spec = _make_spec().model_copy(update={"formal_verification": None})
        changes = _diff_task_specs(old_spec, new_spec)
        fv_change = next(c for c in changes if c.field_name == "formal_verification")
        assert fv_change.change_type == "removed"

    def test_formal_verification_unchanged(self) -> None:
        from relay_teams.agents.tasks.enums import FormalVerificationLanguage
        from relay_teams.agents.tasks.models import FormalVerificationPlan

        fv = FormalVerificationPlan(
            spec_language=FormalVerificationLanguage.TLA_PLUS,
            properties=("Safety",),
        )
        old_spec = _make_spec().model_copy(update={"formal_verification": fv})
        new_spec = _make_spec().model_copy(update={"formal_verification": fv})
        changes = _diff_task_specs(old_spec, new_spec)
        fv_change = next(c for c in changes if c.field_name == "formal_verification")
        assert fv_change.change_type == "unchanged"


# ---------------------------------------------------------------------------
# Database-backed integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListSpecArtifacts:
    async def test_list_artifacts_by_task(self, repo: TaskRepository) -> None:
        task_id = str(new_task_id())
        spec_v1 = TaskSpec(summary="V1", requirements=("r1",))
        _create_task(repo, task_id, spec=spec_v1)
        spec_v2 = TaskSpec(summary="V2", requirements=("r1", "r2"))
        envelope = repo.get(task_id).envelope
        repo.update_envelope(task_id, envelope.model_copy(update={"spec": spec_v2}))

        artifacts = await repo.list_spec_artifacts_by_task_async(task_id)
        assert len(artifacts) == 2
        assert artifacts[0].version == 1
        assert artifacts[1].version == 2

    async def test_list_empty_for_unknown_task(self, repo: TaskRepository) -> None:
        artifacts = await repo.list_spec_artifacts_by_task_async("nonexistent")
        assert len(artifacts) == 0


@pytest.mark.asyncio
class TestGetSpecArtifactByVersion:
    async def test_get_existing_version(self, repo: TaskRepository) -> None:
        task_id = str(new_task_id())
        spec = TaskSpec(summary="Test", requirements=("r1",))
        _create_task(repo, task_id, spec=spec)

        result = await repo.get_spec_artifact_by_version_async(task_id, 1)
        assert result.version == 1

    async def test_raises_for_missing_version(self, repo: TaskRepository) -> None:
        with pytest.raises(KeyError):
            await repo.get_spec_artifact_by_version_async("nonexistent", 99)


@pytest.mark.asyncio
class TestSpecArtifactDiffService:
    async def test_compute_diff_between_versions(self, repo: TaskRepository) -> None:
        task_id = str(new_task_id())
        spec_v1 = TaskSpec(summary="Original", requirements=("r1",))
        _create_task(repo, task_id, spec=spec_v1)
        spec_v2 = TaskSpec(summary="Updated", requirements=("r1", "r2"))
        envelope = repo.get(task_id).envelope
        repo.update_envelope(task_id, envelope.model_copy(update={"spec": spec_v2}))

        service = SpecArtifactDiffService(repo)
        result = await service.compute_diff_async(
            task_id=task_id, from_version=1, to_version=2
        )
        assert result.has_changes is True
        assert result.from_version == 1
        assert result.to_version == 2
        summary_change = next(
            c for c in result.field_changes if c.field_name == "summary"
        )
        assert summary_change.change_type == "modified"

    async def test_compute_diff_raises_for_missing_version(
        self, repo: TaskRepository
    ) -> None:
        service = SpecArtifactDiffService(repo)
        with pytest.raises(KeyError):
            await service.compute_diff_async(
                task_id="nonexistent", from_version=1, to_version=2
            )


@pytest.mark.asyncio
class TestSpecCheckpointEvaluations:
    async def test_save_and_list_evaluations(self, repo: TaskRepository) -> None:
        task_repo = repo

        evaluation = _make_evaluation(
            task_id="task-1",
            artifact_id="art-1",
            checkpoint_seq=1,
            overall_score=4.5,
        )
        await task_repo.save_spec_checkpoint_evaluation_async(evaluation)

        evaluations = await task_repo.list_spec_checkpoint_evaluations_async("task-1")
        assert len(evaluations) == 1
        assert evaluations[0].evaluation_id == "speval-1"
        assert evaluations[0].overall_score == 4.5

    async def test_list_evaluations_filters_by_checkpoint_seq(
        self, repo: TaskRepository
    ) -> None:
        task_repo = repo
        for seq in (1, 2, 3):
            evaluation = _make_evaluation(
                task_id="task-2",
                artifact_id="art-1",
                checkpoint_seq=seq,
                overall_score=float(seq),
            )
            await task_repo.save_spec_checkpoint_evaluation_async(evaluation)

        all_evals = await task_repo.list_spec_checkpoint_evaluations_async("task-2")
        assert len(all_evals) == 3

        filtered = await task_repo.list_spec_checkpoint_evaluations_async(
            "task-2", checkpoint_seq=2
        )
        assert len(filtered) == 1
        assert filtered[0].checkpoint_seq == 2

    async def test_list_empty_for_unknown_task(self, repo: TaskRepository) -> None:
        evaluations = await repo.list_spec_checkpoint_evaluations_async("nonexistent")
        assert len(evaluations) == 0

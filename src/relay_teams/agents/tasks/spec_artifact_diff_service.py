# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.tasks.models import (
    SpecArtifactDiffFieldChange,
    SpecArtifactDiffResult,
    TaskSpec,
    TaskSpecArtifact,
)
from relay_teams.agents.tasks.task_repository import TaskRepository


def _diff_task_specs(
    old_spec: TaskSpec,
    new_spec: TaskSpec,
) -> tuple[SpecArtifactDiffFieldChange, ...]:
    changes: list[SpecArtifactDiffFieldChange] = []

    scalar_text_fields: list[tuple[str, str]] = [
        ("summary", "Summary"),
    ]
    for field_name, field_label in scalar_text_fields:
        old_val = getattr(old_spec, field_name)
        new_val = getattr(new_spec, field_name)
        if old_val == new_val:
            changes.append(
                SpecArtifactDiffFieldChange(
                    field_name=field_name,
                    field_label=field_label,
                    change_type="unchanged",
                    old_value=old_val or None,
                    new_value=new_val or None,
                )
            )
        else:
            changes.append(
                SpecArtifactDiffFieldChange(
                    field_name=field_name,
                    field_label=field_label,
                    change_type="modified",
                    old_value=old_val or None,
                    new_value=new_val or None,
                )
            )

    scalar_enum_fields: list[tuple[str, str]] = [
        ("strictness", "Strictness"),
        ("prompt_code_sync_status", "Prompt/Code Sync Status"),
    ]
    for field_name, field_label in scalar_enum_fields:
        old_val = getattr(old_spec, field_name)
        new_val = getattr(new_spec, field_name)
        if old_val == new_val:
            changes.append(
                SpecArtifactDiffFieldChange(
                    field_name=field_name,
                    field_label=field_label,
                    change_type="unchanged",
                    old_value=old_val.value if old_val else None,
                    new_value=new_val.value if new_val else None,
                )
            )
        else:
            changes.append(
                SpecArtifactDiffFieldChange(
                    field_name=field_name,
                    field_label=field_label,
                    change_type="modified",
                    old_value=old_val.value if old_val else None,
                    new_value=new_val.value if new_val else None,
                )
            )

    scalar_int_fields: list[tuple[str, str]] = [
        ("prompt_artifact_version", "Prompt Artifact Version"),
    ]
    for field_name, field_label in scalar_int_fields:
        old_val = getattr(old_spec, field_name)
        new_val = getattr(new_spec, field_name)
        if old_val == new_val:
            changes.append(
                SpecArtifactDiffFieldChange(
                    field_name=field_name,
                    field_label=field_label,
                    change_type="unchanged",
                    old_value=str(old_val),
                    new_value=str(new_val),
                )
            )
        else:
            changes.append(
                SpecArtifactDiffFieldChange(
                    field_name=field_name,
                    field_label=field_label,
                    change_type="modified",
                    old_value=str(old_val),
                    new_value=str(new_val),
                )
            )

    tuple_fields: list[tuple[str, str]] = [
        ("requirements", "Requirements"),
        ("constraints", "Constraints"),
        ("acceptance_criteria", "Acceptance Criteria"),
        ("out_of_scope", "Out of Scope"),
        ("verification_commands", "Verification Commands"),
        ("evidence_expectations", "Evidence Expectations"),
        ("entities", "Entities"),
        ("approach", "Approach"),
        ("structure", "Structure"),
        ("operations", "Operations"),
        ("norms", "Norms"),
        ("safeguards", "Safeguards"),
    ]
    for field_name, field_label in tuple_fields:
        old_items = getattr(old_spec, field_name)
        new_items = getattr(new_spec, field_name)
        old_set = set(old_items)
        new_set = set(new_items)
        added = tuple(sorted(new_set - old_set))
        removed = tuple(sorted(old_set - new_set))
        if not old_items and new_items:
            change_type = "added"
        elif old_items and not new_items:
            change_type = "removed"
        elif added or removed:
            change_type = "modified"
        else:
            change_type = "unchanged"
        changes.append(
            SpecArtifactDiffFieldChange(
                field_name=field_name,
                field_label=field_label,
                change_type=change_type,
                old_items=old_items,
                new_items=new_items,
                added_items=added,
                removed_items=removed,
            )
        )

    old_fv = old_spec.formal_verification
    new_fv = new_spec.formal_verification
    if old_fv is None and new_fv is None:
        changes.append(
            SpecArtifactDiffFieldChange(
                field_name="formal_verification",
                field_label="Formal Verification",
                change_type="unchanged",
            )
        )
    elif old_fv is None and new_fv is not None:
        changes.append(
            SpecArtifactDiffFieldChange(
                field_name="formal_verification",
                field_label="Formal Verification",
                change_type="added",
            )
        )
    elif old_fv is not None and new_fv is None:
        changes.append(
            SpecArtifactDiffFieldChange(
                field_name="formal_verification",
                field_label="Formal Verification",
                change_type="removed",
            )
        )
    else:
        old_dump = old_fv.model_dump_json()  # type: ignore[union-attr]
        new_dump = new_fv.model_dump_json()  # type: ignore[union-attr]
        if old_dump == new_dump:
            changes.append(
                SpecArtifactDiffFieldChange(
                    field_name="formal_verification",
                    field_label="Formal Verification",
                    change_type="unchanged",
                )
            )
        else:
            changes.append(
                SpecArtifactDiffFieldChange(
                    field_name="formal_verification",
                    field_label="Formal Verification",
                    change_type="modified",
                )
            )

    return tuple(changes)


def _build_diff_summary(
    changes: tuple[SpecArtifactDiffFieldChange, ...],
) -> str:
    changed = [change for change in changes if change.change_type != "unchanged"]
    if not changed:
        return "No changes detected between versions."
    parts: list[str] = []
    for change in changed:
        if change.added_items or change.removed_items:
            detail_parts: list[str] = []
            if change.added_items:
                detail_parts.append(
                    f"+{len(change.added_items)} item{'s' if len(change.added_items) != 1 else ''}"
                )
            if change.removed_items:
                detail_parts.append(
                    f"-{len(change.removed_items)} item{'s' if len(change.removed_items) != 1 else ''}"
                )
            parts.append(f"{change.field_label} ({', '.join(detail_parts)})")
        elif change.change_type == "added":
            parts.append(f"{change.field_label} (added)")
        elif change.change_type == "removed":
            parts.append(f"{change.field_label} (removed)")
        else:
            parts.append(f"{change.field_label} (modified)")
    count = len(parts)
    return f"{count} field{'s' if count != 1 else ''} changed: {', '.join(parts)}"


class SpecArtifactDiffService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo

    async def compute_diff_async(
        self,
        *,
        task_id: str,
        from_version: int,
        to_version: int,
    ) -> SpecArtifactDiffResult:
        artifacts = await self._task_repo.list_spec_artifacts_by_task_async(task_id)
        from_artifact = _find_artifact_by_version(artifacts, from_version)
        to_artifact = _find_artifact_by_version(artifacts, to_version)
        field_changes = _diff_task_specs(from_artifact.spec, to_artifact.spec)
        has_changes = any(c.change_type != "unchanged" for c in field_changes)
        summary = _build_diff_summary(field_changes)
        return SpecArtifactDiffResult(
            task_id=task_id,
            from_artifact_id=from_artifact.artifact_id,
            to_artifact_id=to_artifact.artifact_id,
            from_version=from_version,
            to_version=to_version,
            field_changes=field_changes,
            has_changes=has_changes,
            summary=summary,
        )


def _find_artifact_by_version(
    artifacts: tuple[TaskSpecArtifact, ...],
    version: int,
) -> TaskSpecArtifact:
    for artifact in artifacts:
        if artifact.version == version:
            return artifact
    raise KeyError(f"No spec artifact found with version {version}")

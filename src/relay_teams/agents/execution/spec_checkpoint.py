# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    ToolReturnPart,
)

from relay_teams.agents.execution.conversation_compaction import (
    ConversationTokenEstimator,
)
from relay_teams.agents.tasks.models import (
    SpecCheckpointPolicy,
    TaskEnvelope,
    TaskSpec,
)

SPEC_CHECKPOINT_MARKER = "<!-- relay-spec-checkpoint"
_SEQUENCE_ATTRIBUTE = 'sequence="'
_TASK_ID_ATTRIBUTE = 'task_id="'
_ITEM_MAX_CHARS = 700


class SpecCheckpointDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    should_inject: bool = False
    content: str = ""
    sequence: int = Field(default=0, ge=0)
    reason: str = ""
    tool_calls_since_last_checkpoint: int = Field(default=0, ge=0)
    messages_since_last_checkpoint: int = Field(default=0, ge=0)
    history_tokens_since_last_checkpoint: int = Field(default=0, ge=0)


def build_spec_checkpoint_decision(
    *,
    task: TaskEnvelope,
    role_id: str,
    history: Sequence[ModelRequest | ModelResponse],
    current_artifact_version: int | None = None,
) -> SpecCheckpointDecision:
    policy = task.lifecycle.spec_checkpoint
    spec = task.spec
    if not policy.enabled or spec is None or not task_spec_has_content(spec):
        return SpecCheckpointDecision()

    last_checkpoint_index, last_sequence = latest_spec_checkpoint_position(
        history=history,
        task_id=task.task_id,
    )
    history_since_checkpoint = tuple(history[last_checkpoint_index + 1 :])
    tool_calls_since_checkpoint = count_completed_tool_calls(history_since_checkpoint)
    messages_since_checkpoint = len(history_since_checkpoint)
    tokens_since_checkpoint = ConversationTokenEstimator().estimate_history_tokens(
        history_since_checkpoint
    )

    version_change_info: tuple[int, int, str] | None = None
    version_reason = ""

    if (
        policy.refresh_on_version_change
        and current_artifact_version is not None
        and spec.prompt_artifact_version > current_artifact_version
    ):
        version_reason = "spec_version_changed"
        version_change_info = (
            current_artifact_version,
            spec.prompt_artifact_version,
            f"Spec version changed from {current_artifact_version} to {spec.prompt_artifact_version}",
        )

    reason = spec_checkpoint_reason(
        policy=policy,
        tool_calls_since_checkpoint=tool_calls_since_checkpoint,
        messages_since_checkpoint=messages_since_checkpoint,
        tokens_since_checkpoint=tokens_since_checkpoint,
    )

    if version_reason and not reason:
        reason = version_reason
    elif version_reason and reason:
        reason = f"{version_reason}, {reason}"

    if not reason:
        return SpecCheckpointDecision(
            tool_calls_since_last_checkpoint=tool_calls_since_checkpoint,
            messages_since_last_checkpoint=messages_since_checkpoint,
            history_tokens_since_last_checkpoint=tokens_since_checkpoint,
        )

    sequence = last_sequence + 1
    return SpecCheckpointDecision(
        should_inject=True,
        content=render_spec_checkpoint(
            task=task,
            role_id=role_id,
            sequence=sequence,
            reason=reason,
            policy=policy,
            tool_calls_since_checkpoint=tool_calls_since_checkpoint,
            messages_since_checkpoint=messages_since_checkpoint,
            tokens_since_checkpoint=tokens_since_checkpoint,
            version_change=version_change_info,
        ),
        sequence=sequence,
        reason=reason,
        tool_calls_since_last_checkpoint=tool_calls_since_checkpoint,
        messages_since_last_checkpoint=messages_since_checkpoint,
        history_tokens_since_last_checkpoint=tokens_since_checkpoint,
    )


def task_spec_has_content(spec: TaskSpec) -> bool:
    return bool(
        spec.summary
        or spec.requirements
        or spec.constraints
        or spec.acceptance_criteria
        or spec.out_of_scope
        or spec.verification_commands
        or spec.evidence_expectations
        or spec.entities
        or spec.approach
        or spec.structure
        or spec.operations
        or spec.norms
        or spec.safeguards
        or spec.formal_verification is not None
    )


def spec_checkpoint_reason(
    *,
    policy: SpecCheckpointPolicy,
    tool_calls_since_checkpoint: int,
    messages_since_checkpoint: int,
    tokens_since_checkpoint: int,
) -> str:
    reasons: list[str] = []
    if tool_calls_since_checkpoint >= policy.refresh_interval_tool_calls:
        reasons.append(f"tool_calls>={policy.refresh_interval_tool_calls}")
    if messages_since_checkpoint >= policy.refresh_interval_messages:
        reasons.append(f"messages>={policy.refresh_interval_messages}")
    if tokens_since_checkpoint >= policy.refresh_interval_history_tokens:
        reasons.append(f"history_tokens>={policy.refresh_interval_history_tokens}")
    return ", ".join(reasons)


def render_spec_checkpoint(
    *,
    task: TaskEnvelope,
    role_id: str,
    sequence: int,
    reason: str,
    policy: SpecCheckpointPolicy,
    tool_calls_since_checkpoint: int,
    messages_since_checkpoint: int,
    tokens_since_checkpoint: int,
    version_change: tuple[int, int, str] | None = None,
) -> str:
    spec = task.spec
    if spec is None:
        return ""
    lines = [
        spec_checkpoint_marker(task_id=task.task_id, sequence=sequence),
        "## Spec Checkpoint",
        (
            "Automatic specification refresh. Treat this task spec as "
            "authoritative over older compressed or conversational context."
        ),
        "",
        f"- Task ID: {task.task_id}",
        f"- Role ID: {role_id}",
        f"- Sequence: {sequence}",
        f"- Trigger: {reason}",
        (
            "- Since Previous Checkpoint: "
            f"{tool_calls_since_checkpoint} tool calls, "
            f"{messages_since_checkpoint} messages, "
            f"{tokens_since_checkpoint} estimated history tokens"
        ),
        "",
        "### Task Spec",
    ]
    if task.spec_artifact_id is not None:
        lines.append(f"- Spec Artifact ID: {task.spec_artifact_id}")
    if task.spec_source_task_id is not None:
        lines.append(f"- Spec Source Task ID: {task.spec_source_task_id}")
    if spec.summary:
        lines.append(f"- Summary: {_clip_item(spec.summary)}")
    lines.extend(_format_items("Requirements", spec.requirements))
    lines.extend(_format_items("Entities", spec.entities))
    lines.extend(_format_items("Approach", spec.approach))
    lines.extend(_format_items("Structure", spec.structure))
    lines.extend(_format_items("Operations", spec.operations))
    lines.extend(_format_items("Norms", spec.norms))
    lines.extend(_format_items("Safeguards", spec.safeguards))
    lines.extend(_format_items("Constraints", spec.constraints))
    lines.extend(_format_items("Acceptance Criteria", spec.acceptance_criteria))
    lines.extend(_format_items("Out of Scope", spec.out_of_scope))
    lines.extend(_format_items("Verification Commands", spec.verification_commands))
    lines.extend(_format_items("Evidence Expectations", spec.evidence_expectations))
    lines.append(f"- Strictness: {spec.strictness.value}")
    lines.append(f"- Prompt Artifact Version: {spec.prompt_artifact_version}")
    lines.append(f"- Prompt/Code Sync Status: {spec.prompt_code_sync_status.value}")
    if spec.formal_verification is not None:
        formal = spec.formal_verification
        lines.append("- Formal Verification:")
        lines.append(f"  - Spec Language: {formal.spec_language.value}")
        lines.append(f"  - Tool Profile: {formal.tool_profile.value}")
        lines.extend(_format_nested_items("Properties", formal.properties))
        lines.extend(
            _format_nested_items(
                "Proof Artifacts",
                tuple(str(path) for path in formal.proof_artifacts),
            )
        )
        if formal.counterexample_path is not None:
            lines.append(f"  - Counterexample Path: {formal.counterexample_path}")
        if formal.replay_command is not None:
            lines.append(
                "  - Replay Command: " + " ".join(formal.replay_command.command)
            )
    if spec.acceptance_criteria or spec.evidence_expectations:
        lines.append(
            "- Completion Evidence: cite each acceptance criterion and "
            "evidence expectation in the final handoff."
        )
    if policy.include_reasons:
        lines.extend(
            _format_reasons_canvas(
                reason,
                tool_calls_since_checkpoint,
                messages_since_checkpoint,
                tokens_since_checkpoint,
            )
        )
    if version_change is not None:
        old_ver, new_ver, diff_summary = version_change
        lines.append("")
        lines.append("### Spec Version Change")
        lines.append(f"- Previous Version: {old_ver}")
        lines.append(f"- Current Version: {new_ver}")
        lines.append(f"- Diff Summary: {diff_summary}")
    return _clip_checkpoint_text(
        "\n".join(lines).strip(),
        max_chars=policy.max_summary_chars,
    )


def spec_checkpoint_marker(*, task_id: str, sequence: int) -> str:
    return (
        f'{SPEC_CHECKPOINT_MARKER} task_id="{task_id}" '
        f'sequence="{max(0, sequence)}" -->'
    )


def latest_spec_checkpoint_position(
    *,
    history: Sequence[ModelRequest | ModelResponse],
    task_id: str,
) -> tuple[int, int]:
    last_index = -1
    last_sequence = 0
    for index, message in enumerate(history):
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, SystemPromptPart):
                continue
            content = str(part.content or "")
            if not is_spec_checkpoint_content(content, task_id=task_id):
                continue
            last_index = index
            last_sequence = max(last_sequence, _extract_sequence(content))
    return last_index, last_sequence


def is_spec_checkpoint_content(content: str, *, task_id: str | None = None) -> bool:
    if SPEC_CHECKPOINT_MARKER not in content:
        return False
    if task_id is None:
        return True
    task_id_marker = f'{_TASK_ID_ATTRIBUTE}{task_id}"'
    return task_id_marker in content


def count_completed_tool_calls(
    history: Sequence[ModelRequest | ModelResponse],
) -> int:
    count = 0
    for message in history:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                count += 1
                continue
            if isinstance(part, RetryPromptPart) and str(part.tool_name or "").strip():
                count += 1
    return count


def _format_items(label: str, items: tuple[str, ...]) -> list[str]:
    if not items:
        return []
    return [f"- {label}:"] + [f"  - {_clip_item(item)}" for item in items]


def _format_nested_items(label: str, items: tuple[str, ...]) -> list[str]:
    if not items:
        return []
    return [f"  - {label}:"] + [f"    - {_clip_item(item)}" for item in items]


def _clip_item(item: str) -> str:
    text = str(item or "").strip()
    if len(text) <= _ITEM_MAX_CHARS:
        return text
    clipped = text[: _ITEM_MAX_CHARS - 15].rstrip()
    return f"{clipped} [truncated]"


def _clip_checkpoint_text(text: str, *, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    prefix = stripped[: max(0, max_chars - 48)].rstrip()
    last_newline = prefix.rfind("\n")
    if last_newline > 0:
        prefix = prefix[:last_newline].rstrip()
    return f"{prefix}\n[spec checkpoint truncated]"


def _extract_sequence(content: str) -> int:
    start = content.find(_SEQUENCE_ATTRIBUTE)
    if start < 0:
        return 0
    start += len(_SEQUENCE_ATTRIBUTE)
    end = content.find('"', start)
    if end < 0:
        return 0
    raw_sequence = content[start:end].strip()
    if not raw_sequence.isdigit():
        return 0
    return int(raw_sequence)


def _format_reasons_canvas(
    trigger_reason: str,
    tool_calls: int,
    messages: int,
    tokens: int,
) -> list[str]:
    """Format the REASONS section for spec checkpoint rendering (FE-5 FE5-14)."""
    if not trigger_reason:
        return []
    from datetime import datetime, timezone

    timestamp = datetime.now(tz=timezone.utc).isoformat()
    changed_fields: list[str] = []
    if tool_calls > 0:
        changed_fields.append(f"tool_calls={tool_calls}")
    if messages > 0:
        changed_fields.append(f"messages={messages}")
    if tokens > 0:
        changed_fields.append(f"history_tokens={tokens}")
    lines = [
        "",
        "### REASONS",
        f"- timestamp: {timestamp}",
        f"- trigger: {trigger_reason}",
        f"- changed_fields: {', '.join(changed_fields)}",
        (
            "- reason: Automatic spec checkpoint refresh triggered by "
            f"threshold breach: {trigger_reason}."
        ),
    ]
    return lines

# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


class ContextEditJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    session_id: str
    run_id: str
    old_spec_summary: str
    new_spec_summary: str
    diff_description: str
    affected_criteria: tuple[str, ...] = ()
    injected_at: str = ""


class ContextEditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: ContextEditJob
    injection_message: str
    accepted: bool
    reason: str = ""


def build_diff_injection(
    *,
    task_id: str,
    session_id: str,
    run_id: str,
    old_spec: str,
    new_spec: str,
    affected_criteria: tuple[str, ...] = (),
) -> ContextEditJob:
    """Generate a human-readable diff description comparing old and new spec.

    Uses standard library ``difflib`` to identify added/removed/changed lines.
    """
    old_lines = old_spec.splitlines()
    new_lines = new_spec.splitlines()
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="old_spec",
        tofile="new_spec",
        lineterm="",
    )
    diff_text = "\n".join(diff)
    if not diff_text.strip():
        diff_description = "No changes detected between old and new spec."
    else:
        lines = diff_text.split("\n")
        if len(lines) > 50:
            diff_description = (
                "Spec updated with the following changes (truncated):\n"
                + "\n".join(lines[:50])
                + "\n... (additional changes omitted)"
            )
        else:
            diff_description = "Spec updated with the following changes:\n" + diff_text

    return ContextEditJob(
        task_id=task_id,
        session_id=session_id,
        run_id=run_id,
        old_spec_summary=old_spec[:200],
        new_spec_summary=new_spec[:200],
        diff_description=diff_description,
        affected_criteria=affected_criteria,
        injected_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def build_injection_message(job: ContextEditJob) -> str:
    """Build the system message to inject for a context edit."""
    parts = [
        "[CONTEXT EDIT - Spec Update]",
        "",
        f"Task: {job.task_id}",
        "",
    ]
    if job.affected_criteria:
        parts.append("Affected acceptance criteria:")
        for criterion in job.affected_criteria:
            parts.append(f"  - {criterion}")
        parts.append("")
    parts.append(job.diff_description)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# EP-1: Compact section rendering for context window management
# ---------------------------------------------------------------------------

_CONTEXT_COMPACT_TRUNCATE_LENGTH = 300


def render_compact_section(
    *,
    title: str,
    content: str,
    max_length: int = _CONTEXT_COMPACT_TRUNCATE_LENGTH,
) -> str:
    """Render a compact section suitable for limited context windows.

    Strips leading/trailing whitespace, dedents, and truncates with an
    ellipsis marker when the rendered content exceeds *max_length*.

    Returns the compacted section string including a header line.
    """
    cleaned = content.strip()
    if not cleaned:
        return f"{title}: (empty)"
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "..."
    return f"{title}:\n{cleaned}"

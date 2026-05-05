# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.memory.models import MemoryEntryKind

_SEMANTIC_CONSOLIDATION_SYSTEM_PROMPT: str = """\
You are a knowledge extraction specialist. Your task is to analyze an agent
conversation and extract structured memory entries. Extract only high-signal
information that will be useful for future tasks.

For each extraction, classify into one of these kinds:
- DECISION: A design or implementation choice made, with rationale
- CONSTRAINT: A technical or domain limitation discovered
- FAILURE_MODE: Root cause of an error, trigger conditions, mitigation
- INSIGHT: Non-obvious discovery or pattern recognized
- PREFERENCE: Explicit style/convention preference from the user
- FACT: Verifiable technical fact established during the conversation
- SUMMARY: Condensed summary of the conversation outcome

Output ONLY valid JSON matching the schema. Do not include commentary."""

_EXTRACTION_KIND_PROMPTS: dict[MemoryEntryKind, str] = {
    MemoryEntryKind.DECISION: (
        "Extract design or implementation decisions. For each decision,"
        " describe what was chosen, why, and what alternatives were considered."
    ),
    MemoryEntryKind.CONSTRAINT: (
        "Extract technical or domain constraints discovered during the"
        " conversation. Include the constraint, its source, and its impact"
        " on future work."
    ),
    MemoryEntryKind.FAILURE_MODE: (
        "Extract error patterns and failure modes. For each failure, describe"
        " the root cause, the trigger conditions, and recommended mitigations."
    ),
    MemoryEntryKind.INSIGHT: (
        "Extract non-obvious discoveries and recognized patterns."
        " Focus on information that was not obvious before the conversation"
        " and that would help future tasks."
    ),
    MemoryEntryKind.PREFERENCE: (
        "Extract explicit user preferences about style, conventions, tool"
        " choices, or workflow. Include the preference and the context in"
        " which it was stated."
    ),
    MemoryEntryKind.FACT: (
        "Extract verifiable technical facts established during the"
        " conversation. Include the fact, how it was verified, and its"
        " relevance to the project."
    ),
    MemoryEntryKind.SUMMARY: (
        "Generate a condensed summary of the conversation outcome,"
        " highlighting key accomplishments, unresolved issues, and next steps."
    ),
}


def _build_extraction_instructions(
    kinds: tuple[MemoryEntryKind, ...],
) -> str:
    """Build extraction instructions for the given memory entry kinds.

    Returns an instruction string that combines the per-kind prompts for
    the selected kinds, or a default combined prompt if no filter is given.
    """
    if not kinds:
        kinds = tuple(MemoryEntryKind)

    parts: list[str] = []
    for kind in kinds:
        prompt = _EXTRACTION_KIND_PROMPTS.get(kind)
        if prompt is not None:
            parts.append(f"- {kind.value}: {prompt}")

    if not parts:
        return "Extract all relevant memory entries from the conversation."

    return "Extract the following kinds of memory entries:\n" + "\n".join(parts)

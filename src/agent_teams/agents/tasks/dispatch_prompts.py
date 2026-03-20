from __future__ import annotations


def build_dispatch_prompt(
    *,
    title: str,
    objective: str,
    prompt: str,
) -> str:
    request = str(prompt or "").strip()
    if not request:
        request = "Execute this task contract and return the requested result."
    return (
        "## Task Contract\n"
        f"Title: {title}\n"
        f"Objective: {objective}\n\n"
        "## Coordinator Prompt\n"
        f"{request}\n\n"
    )

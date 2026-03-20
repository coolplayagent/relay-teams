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
        "## Execution Rules\n"
        "- Treat this as a fresh execution turn for the current task.\n"
        "- Satisfy the task contract first, then follow the coordinator prompt.\n"
        "- If tools or fresh data are required, use them again instead of relying on stale outputs.\n"
        "- Keep the final result focused on the requested deliverable."
    )

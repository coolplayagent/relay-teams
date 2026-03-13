from __future__ import annotations


def build_revise_followup_prompt(feedback: str) -> str:
    request = str(feedback or "").strip()
    if not request:
        request = "Run the task again and produce an updated result."
    return (
        "## Follow-Up Request\n"
        f"{request}\n\n"
        "## Execution Rules\n"
        "- Treat this as a new execution turn for the same task.\n"
        "- Do not just restate or lightly edit your previous answer.\n"
        "- If the task needs fresh data or external tools, call them again.\n"
        "- Use the earlier task history only as context."
    )

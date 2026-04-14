# -*- coding: utf-8 -*-
from __future__ import annotations


def summarize_clawhub_command_failure(*chunks: str) -> str | None:
    for chunk in chunks:
        normalized_lines = _normalized_lines(chunk)
        filtered_lines = [
            line for line in normalized_lines if not _is_progress_line(line)
        ]
        if filtered_lines:
            return _join_error_lines(filtered_lines)
        if normalized_lines:
            return _join_error_lines(normalized_lines)
    return None


def should_retry_clawhub_without_endpoint_overrides(
    error_message: str,
    *,
    endpoint_overrides_configured: bool,
) -> bool:
    if not endpoint_overrides_configured:
        return False
    lowered = error_message.lower()
    return "user: invalid value" in lowered or (
        "validation error" in lowered and "user" in lowered
    )


def explain_clawhub_failure(
    error_message: str,
    *,
    endpoint_overrides_configured: bool,
    endpoint_fallback_used: bool,
) -> str:
    lowered = error_message.lower()
    if "user: invalid value" not in lowered and not (
        "validation error" in lowered and "user" in lowered
    ):
        return error_message

    details = [
        "ClawHub returned an invalid user payload.",
        "Confirm the reset token is a newly generated value instead of the previous token.",
    ]
    if endpoint_overrides_configured and not endpoint_fallback_used:
        details.append(
            "The configured ClawHub site or registry endpoint may have returned an unexpected response."
        )
    if endpoint_fallback_used:
        details.append(
            "The command was retried without CLAWHUB_SITE/CLAWHUB_REGISTRY and still failed."
        )
    return f"{' '.join(details)} Raw error: {error_message}"


def combine_clawhub_failure_messages(
    primary_error_message: str,
    fallback_error_message: str,
) -> str:
    if primary_error_message == fallback_error_message:
        return primary_error_message
    return (
        f"{primary_error_message} Retried without CLAWHUB_SITE/CLAWHUB_REGISTRY and "
        f"got: {fallback_error_message}"
    )


def _normalized_lines(chunk: str) -> list[str]:
    return [line.strip() for line in chunk.splitlines() if line.strip()]


def _is_progress_line(line: str) -> bool:
    return line.startswith("- ") or line.startswith("✔")


def _join_error_lines(lines: list[str]) -> str:
    deduped_lines = list(dict.fromkeys(lines))
    if len(deduped_lines) == 1:
        return deduped_lines[0]
    return " | ".join(deduped_lines[-2:])

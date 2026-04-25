# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.metrics.definitions import (
    LLM_CACHED_INPUT_TOKENS,
    LLM_INPUT_TOKENS,
    LLM_OUTPUT_TOKENS,
)
from relay_teams.metrics.models import MetricTagSet
from relay_teams.metrics.recorder import MetricRecorder


def record_token_usage(
    recorder: MetricRecorder,
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    instance_id: str,
    role_id: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> None:
    tags = MetricTagSet(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        instance_id=instance_id,
        role_id=role_id,
    )
    if input_tokens > 0:
        recorder.emit(
            definition_name=LLM_INPUT_TOKENS.name,
            value=input_tokens,
            tags=tags,
        )
    if cached_input_tokens > 0:
        recorder.emit(
            definition_name=LLM_CACHED_INPUT_TOKENS.name,
            value=cached_input_tokens,
            tags=tags,
        )
    if output_tokens > 0:
        recorder.emit(
            definition_name=LLM_OUTPUT_TOKENS.name,
            value=output_tokens,
            tags=tags,
        )


async def record_token_usage_async(
    recorder: MetricRecorder,
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    instance_id: str,
    role_id: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> None:
    tags = MetricTagSet(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        instance_id=instance_id,
        role_id=role_id,
    )
    if input_tokens > 0:
        await recorder.emit_async(
            definition_name=LLM_INPUT_TOKENS.name,
            value=input_tokens,
            tags=tags,
        )
    if cached_input_tokens > 0:
        await recorder.emit_async(
            definition_name=LLM_CACHED_INPUT_TOKENS.name,
            value=cached_input_tokens,
            tags=tags,
        )
    if output_tokens > 0:
        await recorder.emit_async(
            definition_name=LLM_OUTPUT_TOKENS.name,
            value=output_tokens,
            tags=tags,
        )

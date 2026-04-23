# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ToolReturnPart,
    UserPromptPart,
)

from relay_teams.media import user_prompt_content_key, user_prompt_content_to_text


def drop_duplicate_leading_request(
    *,
    history: Sequence[ModelRequest | ModelResponse],
    new_messages: list[ModelRequest | ModelResponse],
) -> list[ModelRequest | ModelResponse]:
    if not history or not new_messages:
        return new_messages
    last_history = history[-1]
    first_new = new_messages[0]
    if not isinstance(last_history, ModelRequest):
        return new_messages
    if not isinstance(first_new, ModelRequest):
        return new_messages
    if not model_requests_match_user_prompt(last_history, first_new):
        if not model_request_matches_tool_result_replay(
            history=history,
            replayed_request=first_new,
        ):
            return new_messages
    return new_messages[1:]


def model_requests_match_user_prompt(
    left: ModelRequest,
    right: ModelRequest,
) -> bool:
    left_prompt_key = user_prompt_parts_key(parts=left.parts)
    if left_prompt_key is None:
        return False
    right_prompt_key = user_prompt_parts_key(parts=right.parts)
    if right_prompt_key is None:
        return False
    return left_prompt_key == right_prompt_key


def model_request_matches_tool_result_replay(
    *,
    history: Sequence[ModelRequest | ModelResponse],
    replayed_request: ModelRequest,
) -> bool:
    expected_parts = tool_result_replay_parts(history=history)
    if expected_parts is None:
        return False
    expected_tool_returns, expected_user_prompts = expected_parts
    actual_tool_returns = [
        part for part in replayed_request.parts if isinstance(part, ToolReturnPart)
    ]
    actual_user_prompts = [
        part for part in replayed_request.parts if isinstance(part, UserPromptPart)
    ]
    if len(actual_tool_returns) + len(actual_user_prompts) != len(
        replayed_request.parts
    ):
        return False
    if len(expected_tool_returns) != len(actual_tool_returns):
        return False
    if any(
        not tool_return_parts_match(
            expected_part=expected_part,
            actual_part=actual_part,
        )
        for expected_part, actual_part in zip(
            expected_tool_returns, actual_tool_returns
        )
    ):
        return False
    expected_prompt_key = user_prompt_parts_key(parts=expected_user_prompts)
    actual_prompt_key = user_prompt_parts_key(parts=actual_user_prompts)
    if expected_prompt_key is None or actual_prompt_key is None:
        return False
    return expected_prompt_key == actual_prompt_key


# noinspection PyTypeHints
def tool_result_replay_parts(
    *,
    history: Sequence[ModelRequest | ModelResponse],
) -> tuple[list[ToolReturnPart], list[UserPromptPart]] | None:
    if not history:
        return None
    mixed_replay = mixed_tool_result_replay_parts(history[-1])
    if mixed_replay is not None:
        return mixed_replay
    if len(history) < 2:
        return None
    previous_message = history[-2]
    synthetic_prompt = history[-1]
    if not isinstance(previous_message, ModelRequest):
        return None
    if not isinstance(synthetic_prompt, ModelRequest):
        return None
    if not model_request_contains_only_tool_returns(previous_message):
        return None
    if not model_request_contains_only_user_prompts(synthetic_prompt):
        return None
    expected_tool_returns = [
        part for part in previous_message.parts if isinstance(part, ToolReturnPart)
    ]
    expected_user_prompts = [
        part for part in synthetic_prompt.parts if isinstance(part, UserPromptPart)
    ]
    return expected_tool_returns, expected_user_prompts


# noinspection PyTypeHints
def mixed_tool_result_replay_parts(
    message: ModelRequest | ModelResponse,
) -> tuple[list[ToolReturnPart], list[UserPromptPart]] | None:
    if not isinstance(message, ModelRequest):
        return None
    tool_returns = [part for part in message.parts if isinstance(part, ToolReturnPart)]
    user_prompts = [part for part in message.parts if isinstance(part, UserPromptPart)]
    if not tool_returns or not user_prompts:
        return None
    if len(tool_returns) + len(user_prompts) != len(message.parts):
        return None
    return tool_returns, user_prompts


def model_request_contains_only_tool_returns(message: ModelRequest) -> bool:
    return bool(message.parts) and all(
        isinstance(part, ToolReturnPart) for part in message.parts
    )


def model_request_contains_only_user_prompts(message: ModelRequest) -> bool:
    return bool(message.parts) and all(
        isinstance(part, UserPromptPart) for part in message.parts
    )


# noinspection PyTypeHints
def user_prompt_parts_key(
    *,
    parts: Sequence[ModelRequestPart],
) -> str | None:
    if not parts or not all(isinstance(part, UserPromptPart) for part in parts):
        return None
    prompt_contents = [
        part.content for part in parts if isinstance(part, UserPromptPart)
    ]
    return user_prompt_content_key(
        prompt_contents[0] if len(prompt_contents) == 1 else prompt_contents
    )


def tool_return_parts_match(
    *,
    expected_part: ToolReturnPart,
    actual_part: ToolReturnPart,
) -> bool:
    return (
        expected_part.tool_name == actual_part.tool_name
        and expected_part.tool_call_id == actual_part.tool_call_id
        and expected_part.content == actual_part.content
    )


# noinspection PyTypeHints
def extract_user_prompt_text(message: ModelRequest) -> str | None:
    prompt_parts = [part for part in message.parts if isinstance(part, UserPromptPart)]
    if len(prompt_parts) != len(message.parts):
        return None
    combined = "\n".join(
        user_prompt_content_to_text(part.content) for part in prompt_parts
    ).strip()
    return combined or None


def history_ends_with_user_prompt(
    history: Sequence[ModelRequest | ModelResponse],
    content_key: str,
) -> bool:
    target = str(content_key or "").strip()
    if not target or not history:
        return False
    last = history[-1]
    if not isinstance(last, ModelRequest):
        return False
    parts = [part for part in last.parts if isinstance(part, UserPromptPart)]
    if len(parts) != len(last.parts):
        return False
    prompt_contents = [part.content for part in parts]
    current_key = user_prompt_content_key(
        prompt_contents[0] if len(prompt_contents) == 1 else prompt_contents
    )
    return current_key == target

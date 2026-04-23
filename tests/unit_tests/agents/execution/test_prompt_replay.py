# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from relay_teams.agents.execution.prompt_replay import (
    drop_duplicate_leading_request,
    model_request_contains_only_tool_returns,
    model_request_contains_only_user_prompts,
    model_request_matches_tool_result_replay,
    model_requests_match_user_prompt,
    tool_return_parts_match,
    user_prompt_parts_key,
)

from .agent_llm_session_test_support import BinaryContent


def test_model_request_matches_tool_result_replay_helpers_accept_matching_replay() -> (
    None
):
    expected_tool_return = ToolReturnPart(
        tool_name="read",
        tool_call_id="call-read-1",
        content={"ok": True},
    )
    previous_message = ModelRequest(parts=[expected_tool_return])
    synthetic_prompt = ModelRequest(
        parts=[UserPromptPart(content=("describe this image", "second line"))]
    )
    replayed_request = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="read",
                tool_call_id="call-read-1",
                content={"ok": True},
            ),
            UserPromptPart(content=("describe this image", "second line")),
        ]
    )

    assert model_request_contains_only_tool_returns(previous_message)
    assert model_request_contains_only_user_prompts(synthetic_prompt)
    assert tool_return_parts_match(
        expected_part=expected_tool_return,
        actual_part=cast(ToolReturnPart, replayed_request.parts[0]),
    )
    assert (
        user_prompt_parts_key(
            parts=cast(Sequence[ModelRequestPart], synthetic_prompt.parts),
        )
        is not None
    )
    assert model_request_matches_tool_result_replay(
        history=[previous_message, synthetic_prompt],
        replayed_request=replayed_request,
    )
    assert model_request_matches_tool_result_replay(
        history=[
            ModelRequest(
                parts=[
                    expected_tool_return,
                    UserPromptPart(content=("describe this image", "second line")),
                ]
            )
        ],
        replayed_request=replayed_request,
    )


def test_model_request_matches_tool_result_replay_helpers_reject_invalid_shapes() -> (
    None
):
    expected_tool_return = ToolReturnPart(
        tool_name="read",
        tool_call_id="call-read-1",
        content={"ok": True},
    )
    previous_message = ModelRequest(parts=[expected_tool_return])
    synthetic_prompt = ModelRequest(
        parts=[UserPromptPart(content="describe this image")]
    )
    matching_replayed_request = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="read",
                tool_call_id="call-read-1",
                content={"ok": True},
            ),
            UserPromptPart(content="describe this image"),
        ]
    )

    assert not model_request_matches_tool_result_replay(
        history=[previous_message],
        replayed_request=matching_replayed_request,
    )
    assert not model_request_matches_tool_result_replay(
        history=[
            ModelResponse(parts=[TextPart(content="done")], model_name="fake"),
            synthetic_prompt,
        ],
        replayed_request=matching_replayed_request,
    )
    assert not model_request_matches_tool_result_replay(
        history=[
            previous_message,
            ModelResponse(parts=[TextPart(content="done")], model_name="fake"),
        ],
        replayed_request=matching_replayed_request,
    )
    assert not model_request_matches_tool_result_replay(
        history=[
            ModelRequest(
                parts=[expected_tool_return, UserPromptPart(content="unexpected")]
            ),
            synthetic_prompt,
        ],
        replayed_request=matching_replayed_request,
    )
    assert not model_request_matches_tool_result_replay(
        history=[
            previous_message,
            ModelRequest(
                parts=[
                    UserPromptPart(content="describe"),
                    cast(ModelRequestPart, TextPart(content="unexpected")),
                ]
            ),
        ],
        replayed_request=matching_replayed_request,
    )
    assert not model_request_matches_tool_result_replay(
        history=[previous_message, synthetic_prompt],
        replayed_request=ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    tool_call_id="call-read-1",
                    content={"ok": True},
                ),
                cast(ModelRequestPart, TextPart(content="unexpected")),
            ]
        ),
    )
    assert not model_request_matches_tool_result_replay(
        history=[previous_message, synthetic_prompt],
        replayed_request=ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    tool_call_id="call-read-2",
                    content={"ok": True},
                ),
                UserPromptPart(content="describe this image"),
            ]
        ),
    )
    assert not model_request_matches_tool_result_replay(
        history=[previous_message, synthetic_prompt],
        replayed_request=ModelRequest(parts=[expected_tool_return]),
    )
    assert not model_request_contains_only_tool_returns(ModelRequest(parts=[]))
    assert not model_request_contains_only_user_prompts(ModelRequest(parts=[]))
    assert (
        user_prompt_parts_key(
            parts=cast(
                Sequence[ModelRequestPart],
                [cast(ModelRequestPart, TextPart(content="not a prompt"))],
            ),
        )
        is None
    )
    assert not tool_return_parts_match(
        expected_part=expected_tool_return,
        actual_part=ToolReturnPart(
            tool_name="read",
            tool_call_id="call-read-1",
            content={"ok": False},
        ),
    )


def test_model_requests_match_user_prompt_uses_normalized_prompt_text() -> None:
    matching_left = ModelRequest(parts=[UserPromptPart(content="describe this image")])
    matching_right = ModelRequest(
        parts=[UserPromptPart(content="  describe this image  ")]
    )

    assert model_requests_match_user_prompt(matching_left, matching_right)
    assert not model_requests_match_user_prompt(
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    tool_call_id="call-read-1",
                    content={"ok": True},
                )
            ]
        ),
        matching_right,
    )
    assert not model_requests_match_user_prompt(
        matching_left,
        ModelRequest(
            parts=[
                UserPromptPart(content="describe this image"),
                RetryPromptPart(content="retry"),
            ]
        ),
    )


def test_model_requests_match_user_prompt_compares_binary_content_identity() -> None:
    left = ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "describe this image",
                    BinaryContent(data=b"image-one", media_type="image/png"),
                )
            )
        ]
    )
    right = ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "describe this image",
                    BinaryContent(data=b"image-two", media_type="image/png"),
                )
            )
        ]
    )

    assert not model_requests_match_user_prompt(left, right)
    assert drop_duplicate_leading_request(
        history=[left],
        new_messages=[right],
    ) == [right]


def test_drop_duplicate_leading_request_handles_prompt_and_tool_replay_matches() -> (
    None
):
    prompt_request = ModelRequest(parts=[UserPromptPart(content="describe this image")])
    response = ModelResponse(parts=[TextPart(content="done")], model_name="fake")

    assert drop_duplicate_leading_request(
        history=[prompt_request],
        new_messages=[prompt_request, response],
    ) == [response]
    unchanged_messages = drop_duplicate_leading_request(
        history=[prompt_request],
        new_messages=[
            ModelResponse(parts=[TextPart(content="done")], model_name="fake")
        ],
    )
    unchanged_response = unchanged_messages[0]
    assert isinstance(unchanged_response, ModelResponse)
    unchanged_part = unchanged_response.parts[0]
    assert isinstance(unchanged_part, TextPart)
    assert unchanged_part.content == "done"

    expected_tool_return = ToolReturnPart(
        tool_name="read",
        tool_call_id="call-read-1",
        content={"ok": True},
    )
    synthetic_prompt = ModelRequest(
        parts=[UserPromptPart(content="describe this image")]
    )
    replayed_request = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="read",
                tool_call_id="call-read-1",
                content={"ok": True},
            ),
            UserPromptPart(content="describe this image"),
        ]
    )

    assert drop_duplicate_leading_request(
        history=[ModelRequest(parts=[expected_tool_return]), synthetic_prompt],
        new_messages=[replayed_request, response],
    ) == [response]
    assert drop_duplicate_leading_request(
        history=[
            ModelRequest(
                parts=[
                    expected_tool_return,
                    UserPromptPart(content="describe this image"),
                ]
            )
        ],
        new_messages=[replayed_request, response],
    ) == [response]
    preserved_messages = drop_duplicate_leading_request(
        history=[prompt_request],
        new_messages=[
            ModelRequest(parts=[UserPromptPart(content="different prompt")]),
            response,
        ],
    )
    preserved_request = preserved_messages[0]
    assert isinstance(preserved_request, ModelRequest)
    preserved_part = preserved_request.parts[0]
    assert isinstance(preserved_part, UserPromptPart)
    assert preserved_part.content == "different prompt"
    assert preserved_messages[1] == response


def test_model_request_matches_tool_result_replay_rejects_tool_return_count_mismatch() -> (
    None
):
    assert not model_request_matches_tool_result_replay(
        history=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="read",
                        tool_call_id="call-read-1",
                        content={"ok": True},
                    ),
                    ToolReturnPart(
                        tool_name="read",
                        tool_call_id="call-read-2",
                        content={"ok": True},
                    ),
                ]
            ),
            ModelRequest(parts=[UserPromptPart(content="describe this image")]),
        ],
        replayed_request=ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    tool_call_id="call-read-1",
                    content={"ok": True},
                ),
                UserPromptPart(content="describe this image"),
            ]
        ),
    )

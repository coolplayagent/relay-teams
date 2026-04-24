# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, cast

import pytest

from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionBudget,
)

from .agent_llm_session_test_support import (
    AgentLlmSession,
    BinaryContent,
    ConversationCompactionService,
    ConversationMicrocompactResult,
    ConversationMicrocompactService,
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    ImageUrl,
    MediaModality,
    MediaRefContentPart,
    MessageRepository,
    ModelCapabilities,
    ModelEndpointConfig,
    ModelModalityMatrix,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    RunIntentRepository,
    TextContentPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
    _FakeCompactionService,
    _FakeMessageRepo,
    _FakeMicrocompactService,
    _FakePromptHookService,
    _FakeRunIntentRepo,
    _build_request,
    _zero_mcp_context_tokens,
)


def test_resolve_hook_prompt_text_prefers_request_prompt() -> None:
    session = object.__new__(AgentLlmSession)
    session._message_repo = cast(
        MessageRepository,
        _FakeMessageRepo(
            history=[ModelRequest(parts=[UserPromptPart(content="Persisted prompt")])]
        ),
    )

    resolved = AgentLlmSession._resolve_hook_prompt_text(
        session,
        _build_request(user_prompt="Live prompt"),
    )

    assert resolved == "Live prompt"


def test_resolve_hook_prompt_text_uses_latest_pure_user_prompt_from_history() -> None:
    session = object.__new__(AgentLlmSession)
    session._message_repo = cast(
        MessageRepository,
        _FakeMessageRepo(
            history=[
                ModelRequest(parts=[UserPromptPart(content="Older prompt")]),
                ModelRequest(
                    parts=[
                        UserPromptPart(content="Ignore mixed request"),
                        RetryPromptPart(
                            content="validation failed",
                            tool_name="shell",
                            tool_call_id="call-1",
                        ),
                    ]
                ),
                ModelRequest(parts=[UserPromptPart(content="Latest prompt")]),
            ]
        ),
    )

    resolved = AgentLlmSession._resolve_hook_prompt_text(
        session,
        _build_request(user_prompt=None),
    )

    assert resolved == "Latest prompt"


def test_persist_hook_system_context_if_needed_skips_blank_entries() -> None:
    session = object.__new__(AgentLlmSession)
    message_repo = _FakeMessageRepo(history=[])
    session._message_repo = cast(MessageRepository, message_repo)

    AgentLlmSession._persist_hook_system_context_if_needed(
        session,
        request=_build_request(),
        contexts=("  ", "Hook context", "\nSecond context\n"),
    )

    assert message_repo.appended_system_prompts == ["Hook context", "Second context"]


@pytest.mark.asyncio
async def test_prepare_prompt_context_applies_microcompact_before_full_compaction() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    base_history = [
        ModelRequest(parts=[UserPromptPart(content="summarize the file")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path":"README.md"}',
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="A" * 4000,
                )
            ]
        ),
    ]
    microcompacted_history = [
        base_history[0],
        base_history[1],
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="[Compacted tool result]",
                )
            ]
        ),
    ]
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session._message_repo = cast(MessageRepository, _FakeMessageRepo(base_history))
    microcompact_service = _FakeMicrocompactService(
        ConversationMicrocompactResult(
            messages=tuple(microcompacted_history),
            estimated_tokens_before=260,
            estimated_tokens_after=80,
            compacted_message_count=1,
            compacted_part_count=1,
        )
    )
    compaction_service = _FakeCompactionService(
        prompt_section="## Compacted Conversation Summary\nsummary"
    )
    session._conversation_microcompact_service = cast(
        ConversationMicrocompactService,
        microcompact_service,
    )
    session._conversation_compaction_service = cast(
        ConversationCompactionService,
        compaction_service,
    )
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Summarize the file and preserve tool outputs."),
    )
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120

    prepared = await AgentLlmSession._prepare_prompt_context(
        session,
        request=_build_request(),
        conversation_id="conv-1",
        system_prompt="System prompt",
        reserve_user_prompt_tokens=True,
        allowed_tools=("shell",),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert list(prepared.history) == microcompacted_history
    assert prepared.system_prompt.endswith("summary")
    assert microcompact_service.calls
    assert compaction_service.calls
    compaction_call = compaction_service.calls[0]
    assert compaction_call["history"] == microcompacted_history
    assert compaction_call["source_history"] == base_history
    assert compaction_call["estimated_tokens_before_microcompact"] == 260
    assert compaction_call["estimated_tokens_after_microcompact"] == 80


@pytest.mark.asyncio
async def test_prepare_prompt_context_protects_precommitted_current_prompt_from_compaction() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    current_prompt = ModelRequest(parts=[UserPromptPart(content="Current prompt")])
    base_history = [
        ModelRequest(parts=[UserPromptPart(content="Older prompt")]),
        ModelResponse(parts=[]),
        current_prompt,
    ]
    compacted_history = [
        ModelRequest(parts=[UserPromptPart(content="Compacted carryover")])
    ]
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session._message_repo = cast(MessageRepository, _FakeMessageRepo(base_history))
    session._conversation_microcompact_service = None
    compaction_service = _FakeCompactionService(
        applied=True,
        messages=tuple(compacted_history),
    )
    session._conversation_compaction_service = cast(
        ConversationCompactionService,
        compaction_service,
    )
    session._hook_service = None
    session._reminder_service = None
    session._run_event_hub = cast(Any, None)
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Current prompt"),
    )
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120

    prepared = await AgentLlmSession._prepare_prompt_context(
        session,
        request=_build_request(user_prompt=None),
        conversation_id="conv-1",
        system_prompt="System prompt",
        reserve_user_prompt_tokens=True,
        allowed_tools=("shell",),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert list(prepared.history) == [*compacted_history, current_prompt]
    assert compaction_service.calls
    assert compaction_service.calls[0]["history"] == base_history[:-1]
    assert compaction_service.calls[0]["source_history"] == base_history[:-1]


@pytest.mark.asyncio
async def test_maybe_compact_history_emits_pre_and_post_compact_hooks() -> None:
    session = object.__new__(AgentLlmSession)
    history = [
        ModelRequest(parts=[UserPromptPart(content="summarize the file")]),
        ModelResponse(parts=[]),
    ]
    compacted_history = [history[-1]]
    hook_service = _FakePromptHookService(
        HookDecisionBundle(decision=HookDecisionType.ALLOW)
    )
    session._conversation_compaction_service = cast(
        ConversationCompactionService,
        _FakeCompactionService(
            applied=True,
            messages=tuple(compacted_history),
        ),
    )
    session._conversation_microcompact_service = cast(
        ConversationMicrocompactService,
        None,
    )
    session._hook_service = cast(Any, hook_service)
    session._run_event_hub = cast(Any, None)

    result = await AgentLlmSession._maybe_compact_history(
        session,
        request=_build_request(),
        history=history,
        source_history=list(history),
        conversation_id="conv-1",
        budget=ConversationCompactionBudget(
            context_window=100,
            history_trigger_tokens=80,
            history_target_tokens=40,
        ),
        estimated_tokens_before_microcompact=120,
        estimated_tokens_after_microcompact=80,
    )

    assert result == compacted_history
    assert hook_service.events == [
        HookEventName.PRE_COMPACT,
        HookEventName.POST_COMPACT,
    ]


@pytest.mark.asyncio
async def test_prepare_prompt_context_inserts_replay_bridge_for_resume_history() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    base_history = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path":"README.md"}',
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="README contents",
                )
            ]
        ),
    ]
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session._message_repo = cast(MessageRepository, _FakeMessageRepo(base_history))
    session._conversation_microcompact_service = None
    session._conversation_compaction_service = None
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Build the release handoff and keep prior artifacts."),
    )

    prepared = await AgentLlmSession._prepare_prompt_context(
        session,
        request=_build_request(user_prompt=None),
        conversation_id="conv-1",
        system_prompt="System prompt",
        reserve_user_prompt_tokens=False,
        allowed_tools=("shell",),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    prepared_history = list(prepared.history)
    assert len(prepared_history) == 3
    bridge_message = prepared_history[0]
    assert isinstance(bridge_message, ModelRequest)
    bridge_part = bridge_message.parts[0]
    assert isinstance(bridge_part, UserPromptPart)
    assert "Original task intent:" in bridge_part.content
    assert "Build the release handoff" in bridge_part.content
    assert prepared_history[1:] == base_history


@pytest.mark.asyncio
async def test_prepare_prompt_context_keeps_persisted_media_urls_for_prompt_deduplication() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    persisted_prompt = (
        "describe this image",
        ImageUrl(
            url="/api/sessions/session-1/media/asset-1/file",
            media_type="image/png",
        ),
    )
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session._message_repo = cast(
        MessageRepository,
        _FakeMessageRepo(
            [ModelRequest(parts=[UserPromptPart(content=persisted_prompt)])]
        ),
    )
    session._conversation_microcompact_service = None
    session._conversation_compaction_service = None
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Describe the preserved image."),
    )

    class _FakeMediaAssetService:
        def hydrate_user_prompt_content(self, *, content: object) -> object:
            if content == persisted_prompt:
                return (
                    "describe this image",
                    BinaryContent(data=b"image-bytes", media_type="image/png"),
                )
            return content

    cast(Any, session)._media_asset_service = _FakeMediaAssetService()

    prepared = await AgentLlmSession._prepare_prompt_context(
        session,
        request=_build_request(user_prompt="describe this image"),
        conversation_id="conv-1",
        system_prompt="System prompt",
        reserve_user_prompt_tokens=True,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    prepared_history = list(prepared.history)
    assert len(prepared_history) == 1
    prepared_message = prepared_history[0]
    assert isinstance(prepared_message, ModelRequest)
    prepared_part = prepared_message.parts[0]
    assert isinstance(prepared_part, UserPromptPart)
    assert prepared_part.content == persisted_prompt

    next_history, rebuild_context = AgentLlmSession._persist_user_prompt_if_needed(
        session,
        request=_build_request(user_prompt="describe this image"),
        history=prepared_history,
        content=persisted_prompt,
    )

    assert rebuild_context is False
    assert next_history == prepared_history


def test_coerce_history_to_provider_safe_sequence_drops_orphan_tool_prefix() -> None:
    session = object.__new__(AgentLlmSession)
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Investigate the preserved tool execution state."),
    )
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="missing-call",
                    content="orphaned",
                )
            ]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path":"README.md"}',
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="README contents",
                )
            ]
        ),
    ]

    repaired = AgentLlmSession._coerce_history_to_provider_safe_sequence(
        session,
        request=_build_request(user_prompt=None),
        history=history,
    )

    assert len(repaired) == 3
    bridge_message = repaired[0]
    assert isinstance(bridge_message, ModelRequest)
    bridge_part = bridge_message.parts[0]
    assert isinstance(bridge_part, UserPromptPart)
    assert "Investigate the preserved tool execution state." in bridge_part.content
    assert repaired[1:] == history[1:]


def test_coerce_history_to_provider_safe_sequence_keeps_bridge_when_prefix_drop_empties_history() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Resume the preserved execution state after repair."),
    )
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="missing-call",
                    content="orphaned",
                )
            ]
        )
    ]

    repaired = AgentLlmSession._coerce_history_to_provider_safe_sequence(
        session,
        request=_build_request(user_prompt=None),
        history=history,
    )

    assert len(repaired) == 1
    bridge_message = repaired[0]
    assert isinstance(bridge_message, ModelRequest)
    bridge_part = bridge_message.parts[0]
    assert isinstance(bridge_part, UserPromptPart)
    assert "Resume the preserved execution state after repair." in bridge_part.content


def test_validate_request_input_capabilities_rejects_unsupported_image() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="text-only",
        base_url="https://example.test/v1",
        api_key="secret",
        capabilities=ModelCapabilities(
            input=ModelModalityMatrix(text=True, image=False),
            output=ModelModalityMatrix(text=True),
        ),
    )

    with pytest.raises(ValueError, match="does not support image input"):
        AgentLlmSession._validate_request_input_capabilities(
            session,
            _build_request(
                user_prompt=None,
                input=(
                    MediaRefContentPart(
                        kind="media_ref",
                        asset_id="asset-1",
                        session_id="session-1",
                        modality=MediaModality.IMAGE,
                        mime_type="image/png",
                        url="/api/sessions/session-1/media/asset-1/file",
                    ),
                ),
            ),
        )


def test_validate_request_input_capabilities_rejects_unknown_image_support() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="unknown-image-support",
        base_url="https://example.test/v1",
        api_key="secret",
        capabilities=ModelCapabilities(
            input=ModelModalityMatrix(text=True, image=None),
            output=ModelModalityMatrix(text=True),
        ),
    )

    with pytest.raises(ValueError, match="support for image input is unknown"):
        AgentLlmSession._validate_request_input_capabilities(
            session,
            _build_request(
                user_prompt=None,
                input=(
                    MediaRefContentPart(
                        kind="media_ref",
                        asset_id="asset-1",
                        session_id="session-1",
                        modality=MediaModality.IMAGE,
                        mime_type="image/png",
                        url="/api/sessions/session-1/media/asset-1/file",
                    ),
                ),
            ),
        )


def test_validate_history_input_capabilities_rejects_unsupported_image() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="text-only",
        base_url="https://example.test/v1",
        api_key="secret",
        capabilities=ModelCapabilities(
            input=ModelModalityMatrix(text=True, image=False),
            output=ModelModalityMatrix(text=True),
        ),
    )

    with pytest.raises(ValueError, match="does not support image input"):
        AgentLlmSession._validate_history_input_capabilities(
            session,
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=(
                                "describe this image",
                                ImageUrl(
                                    url="/api/sessions/session-1/media/asset-1/file",
                                    media_type="image/png",
                                ),
                            )
                        )
                    ]
                )
            ],
        )


def test_coerce_history_to_provider_safe_sequence_prefers_explicit_user_prompt_over_bridge() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Resume the preserved execution state after repair."),
    )
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="missing-call",
                    content="orphaned",
                )
            ]
        )
    ]

    repaired = AgentLlmSession._coerce_history_to_provider_safe_sequence(
        session,
        request=_build_request(user_prompt="restart from the latest user request"),
        history=history,
    )

    assert repaired == []


def test_drop_duplicate_leading_request_removes_matching_pure_user_prompt() -> None:
    session = object.__new__(AgentLlmSession)
    history = [ModelRequest(parts=[UserPromptPart(content="Repeat prompt")])]
    new_messages: list[ModelRequest | ModelResponse] = [
        ModelRequest(parts=[UserPromptPart(content="Repeat prompt")])
    ]

    deduplicated = AgentLlmSession._drop_duplicate_leading_request(
        session,
        history=history,
        new_messages=new_messages,
    )

    assert deduplicated == []


def test_drop_duplicate_leading_request_keeps_mixed_request() -> None:
    session = object.__new__(AgentLlmSession)
    history = [ModelRequest(parts=[UserPromptPart(content="Repeat prompt")])]
    new_messages: list[ModelRequest | ModelResponse] = [
        ModelRequest(
            parts=[
                UserPromptPart(content="Repeat prompt"),
                RetryPromptPart(
                    content="validation failed",
                    tool_name="shell",
                    tool_call_id="call-1",
                ),
            ]
        )
    ]

    deduplicated = AgentLlmSession._drop_duplicate_leading_request(
        session,
        history=history,
        new_messages=new_messages,
    )

    assert deduplicated == new_messages


def test_persist_user_prompt_keeps_microcompacted_history_in_memory() -> None:
    session = object.__new__(AgentLlmSession)
    compacted_history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="[Compacted tool result]\ntool: read_file",
                )
            ]
        )
    ]
    message_repo = _FakeMessageRepo(history=[])
    session._message_repo = cast(MessageRepository, message_repo)

    next_history, rebuild_context = AgentLlmSession._persist_user_prompt_if_needed(
        session,
        request=_build_request(user_prompt="new prompt"),
        history=list(compacted_history),
        content="new prompt",
    )

    assert rebuild_context is False
    assert message_repo.pruned_conversation_ids == ["conv-1"]
    assert len(message_repo.append_calls) == 1
    assert next_history[:-1] == compacted_history
    appended_message = next_history[-1]
    assert isinstance(appended_message, ModelRequest)
    appended_part = appended_message.parts[0]
    assert isinstance(appended_part, UserPromptPart)
    assert appended_part.content == "new prompt"


def test_current_request_prompt_content_uses_persisted_media_references() -> None:
    session = object.__new__(AgentLlmSession)

    class _FakeMediaAssetService:
        def to_persisted_user_prompt_content(self, *, parts: object) -> object:
            _ = parts
            return (
                "describe this image",
                ImageUrl(
                    url="/api/sessions/session-1/media/asset-1/file",
                    media_type="image/png",
                ),
            )

    cast(Any, session)._media_asset_service = _FakeMediaAssetService()

    content = AgentLlmSession._current_request_prompt_content(
        session,
        _build_request(
            user_prompt="describe this image",
            input=(
                TextContentPart(text="describe this image"),
                MediaRefContentPart(
                    asset_id="asset-1",
                    session_id="session-1",
                    modality=MediaModality.IMAGE,
                    mime_type="image/png",
                    url="/api/sessions/session-1/media/asset-1/file",
                ),
            ),
        ),
    )

    assert content == (
        "describe this image",
        ImageUrl(
            url="/api/sessions/session-1/media/asset-1/file",
            media_type="image/png",
        ),
    )


def test_current_request_prompt_content_falls_back_to_user_prompt_without_persistence_service() -> (
    None
):
    session = object.__new__(AgentLlmSession)

    content = AgentLlmSession._current_request_prompt_content(
        session,
        _build_request(
            user_prompt="fallback prompt",
            input=(
                TextContentPart(text="fallback prompt"),
                MediaRefContentPart(
                    asset_id="asset-1",
                    session_id="session-1",
                    modality=MediaModality.IMAGE,
                    mime_type="image/png",
                    url="/api/sessions/session-1/media/asset-1/file",
                ),
            ),
        ),
    )

    assert content == "fallback prompt"


def test_hydrate_history_media_content_replaces_local_urls_before_provider_send() -> (
    None
):
    session = object.__new__(AgentLlmSession)

    class _FakeMediaAssetService:
        def hydrate_user_prompt_content(self, *, content: object) -> object:
            if content == (
                "describe this image",
                ImageUrl(
                    url="/api/sessions/session-1/media/asset-1/file",
                    media_type="image/png",
                ),
            ):
                return (
                    "describe this image",
                    BinaryContent(
                        data=b"image-bytes",
                        media_type="image/png",
                    ),
                )
            return content

    cast(Any, session)._media_asset_service = _FakeMediaAssetService()
    history = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "describe this image",
                        ImageUrl(
                            url="/api/sessions/session-1/media/asset-1/file",
                            media_type="image/png",
                        ),
                    )
                )
            ]
        )
    ]

    hydrated = AgentLlmSession._hydrate_history_media_content(session, history)

    assert len(hydrated) == 1
    hydrated_message = hydrated[0]
    assert isinstance(hydrated_message, ModelRequest)
    hydrated_part = hydrated_message.parts[0]
    assert isinstance(hydrated_part, UserPromptPart)
    assert hydrated_part.content[0] == "describe this image"
    assert isinstance(hydrated_part.content[1], BinaryContent)
    assert hydrated_part.content[1].data == b"image-bytes"


def test_provider_history_for_model_turn_details_returns_hydrated_history_only() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    persisted_prompt = (
        "describe this image",
        ImageUrl(
            url="http://127.0.0.1:8000/api/sessions/session-1/media/asset-1/file",
            media_type="image/png",
            force_download="allow-local",
        ),
    )

    class _FakeMediaAssetService:
        def hydrate_user_prompt_content(self, *, content: object) -> object:
            if content == persisted_prompt:
                return (
                    "describe this image",
                    BinaryContent(
                        data=b"image-bytes",
                        media_type="image/png",
                    ),
                )
            return content

    cast(Any, session)._media_asset_service = _FakeMediaAssetService()

    provider_history, injected_tool_call_ids = (
        AgentLlmSession._provider_history_for_model_turn_details(
            session,
            request=_build_request(),
            history=[ModelRequest(parts=[UserPromptPart(content=persisted_prompt)])],
            consumed_tool_call_ids={"call-read-1"},
        )
    )

    assert injected_tool_call_ids == ()
    assert len(provider_history) == 1
    hydrated_request = provider_history[0]
    assert isinstance(hydrated_request, ModelRequest)
    hydrated_part = hydrated_request.parts[0]
    assert isinstance(hydrated_part, UserPromptPart)
    assert hydrated_part.content[0] == "describe this image"
    assert isinstance(hydrated_part.content[1], BinaryContent)
    assert hydrated_part.content[1].data == b"image-bytes"


def test_prompt_content_provider_service_requires_provider_capability() -> None:
    session = object.__new__(AgentLlmSession)
    cast(Any, session)._media_asset_service = object()

    assert AgentLlmSession._prompt_content_provider_service(session) is None

    class _FakeProviderService:
        def to_provider_user_prompt_content(self, *, parts: object) -> object:
            _ = parts
            return "attached"

    provider_service = _FakeProviderService()
    cast(Any, session)._media_asset_service = provider_service

    assert AgentLlmSession._prompt_content_provider_service(session) is provider_service


def test_hydrate_history_media_content_returns_original_messages_without_hydrator() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    response = ModelResponse(
        parts=[ToolCallPart(tool_name="noop", args="{}", tool_call_id="call-0")]
    )
    request = ModelRequest(parts=[UserPromptPart(content="prompt text")])
    history = [request, response]

    hydrated = AgentLlmSession._hydrate_history_media_content(session, history)

    assert hydrated == history
    assert hydrated[0] is request
    assert hydrated[1] is response

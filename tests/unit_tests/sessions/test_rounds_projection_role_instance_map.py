from __future__ import annotations

from typing import cast

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import AgentRuntimeRecord
from relay_teams.media import InlineMediaContentPart
from relay_teams.media import MediaModality
from relay_teams.media import TextContentPart
from relay_teams.sessions.session_rounds_projection import build_session_rounds
from relay_teams.sessions.session_rounds_projection import build_session_timeline_rounds
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan
from relay_teams.workspace import build_conversation_id


class _FakeAgentRepo:
    def __init__(self, agents: tuple[AgentRuntimeRecord, ...] = ()) -> None:
        self._agents = agents

    def list_by_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        return self._agents

    def list_session_role_instances(
        self, session_id: str
    ) -> tuple[AgentRuntimeRecord, ...]:
        return self._agents


class _FakeTaskRepo:
    def __init__(self, tasks: tuple[TaskRecord, ...] = ()) -> None:
        self._tasks = tasks

    def list_by_session(self, session_id: str) -> tuple[TaskRecord, ...]:
        return self._tasks


class _FakeRunRuntimeRepo:
    def __init__(self, runtimes: tuple[RunRuntimeRecord, ...] = ()) -> None:
        self._runtimes = runtimes

    def list_by_session(self, session_id: str) -> tuple[RunRuntimeRecord, ...]:
        return self._runtimes


def test_build_session_rounds_uses_task_bound_role_instance_map() -> None:
    session_id = "session-1"
    run_id = "run-1"
    role_id = "spec_coder"

    agent = AgentRuntimeRecord(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id="inst-new",
        role_id=role_id,
        workspace_id="default",
        conversation_id=build_conversation_id(session_id, role_id),
        status=InstanceStatus.IDLE,
    )
    runtime = RunRuntimeRecord(
        run_id=run_id,
        session_id=session_id,
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    delegated_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-1",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            role_id=role_id,
            objective="implement",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id="inst-new",
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(
            AgentInstanceRepository,
            cast(object, _FakeAgentRepo((agent,))),
        ),
        task_repo=cast(
            TaskRepository,
            cast(object, _FakeTaskRepo((root_task, delegated_task))),
        ),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    instance_role_map = cast(dict[str, str], round_item["instance_role_map"])
    role_instance_map = cast(dict[str, str], round_item["role_instance_map"])
    assert instance_role_map == {"inst-new": role_id}
    assert role_instance_map[role_id] == "inst-new"


def test_build_session_rounds_includes_task_instance_map() -> None:
    session_id = "session-1"
    run_id = "run-1"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id=None,
    )
    task_first = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-first",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            objective="first",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id="inst-first",
    )
    task_second = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-second",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            objective="second",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id="inst-second",
    )
    task_without_instance = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-unassigned",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            objective="unassigned",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id=None,
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(
            TaskRepository,
            cast(
                object,
                _FakeTaskRepo(
                    (root_task, task_first, task_second, task_without_instance)
                ),
            ),
        ),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo()),
        ),
        get_session_messages=lambda _: [],
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    task_instance_map = cast(dict[str, str], round_item["task_instance_map"])
    task_status_map = cast(dict[str, str], round_item["task_status_map"])
    assert task_instance_map == {
        "task-first": "inst-first",
        "task-second": "inst-second",
    }
    assert task_status_map == {
        "task-root": "created",
        "task-first": "created",
        "task-second": "created",
        "task-unassigned": "created",
    }


def test_build_session_rounds_projects_structured_user_prompt_intent_parts() -> None:
    session_id = "session-1"
    run_id = "run-vision"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="[image: stale-objective.png]",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo()),
        ),
        get_session_messages=lambda _: [
            {
                "trace_id": run_id,
                "role": "user",
                "created_at": "2026-04-21T10:46:50+00:00",
                "message": {
                    "parts": [
                        {
                            "part_kind": "user-prompt",
                            "content": [
                                {"kind": "text", "text": "这个是什么图片"},
                                {
                                    "kind": "inline_media",
                                    "modality": "image",
                                    "mime_type": "image/png",
                                    "base64_data": "QUJD",
                                    "name": "image.png",
                                },
                            ],
                        }
                    ]
                },
            }
        ],
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    intent_parts = cast(list[dict[str, object]], round_item["intent_parts"])
    assert round_item["intent"] == "这个是什么图片\n\n[image: image.png]"
    assert intent_parts == [
        {"kind": "text", "text": "这个是什么图片"},
        {
            "kind": "inline_media",
            "modality": "image",
            "mime_type": "image/png",
            "base64_data": "QUJD",
            "name": "image.png",
            "size_bytes": None,
            "width": None,
            "height": None,
            "duration_ms": None,
            "thumbnail_asset_id": None,
        },
    ]


def test_build_session_rounds_prefers_run_intent_input_over_message_text() -> None:
    session_id = "session-1"
    run_id = "run-intent"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="fallback objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo()),
        ),
        get_session_messages=lambda _: [
            {
                "trace_id": run_id,
                "role": "user",
                "created_at": "2026-04-21T10:46:50+00:00",
                "message": {
                    "parts": [
                        {
                            "part_kind": "user-prompt",
                            "content": "这个是什么图片\n\n[image: image.png]",
                        }
                    ]
                },
            }
        ],
        get_run_intent_input=lambda requested_run_id: (
            (
                TextContentPart(text="这个是什么图片"),
                InlineMediaContentPart(
                    modality=MediaModality.IMAGE,
                    mime_type="image/png",
                    base64_data="QUJD",
                    name="image.png",
                ),
            )
            if requested_run_id == run_id
            else None
        ),
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    intent_parts = cast(list[dict[str, object]], round_item["intent_parts"])
    assert round_item["intent"] == "这个是什么图片\n\n[image: image.png]"
    assert intent_parts[0] == {"kind": "text", "text": "这个是什么图片"}
    assert intent_parts[1]["kind"] == "inline_media"
    assert intent_parts[1]["name"] == "image.png"


def test_build_session_rounds_projects_persisted_url_user_prompt_intent_parts() -> None:
    session_id = "session-1"
    run_id = "run-persisted-vision"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="[image: stale-objective.png]",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo()),
        ),
        get_session_messages=lambda _: [
            {
                "trace_id": run_id,
                "role": "user",
                "created_at": "2026-04-21T10:46:50+00:00",
                "message": {
                    "parts": [
                        {
                            "part_kind": "user-prompt",
                            "content": [
                                "这个是什么图片",
                                {
                                    "kind": "image-url",
                                    "url": "https://cdn.example.com/assets/image.png",
                                    "media_type": "image/png",
                                },
                            ],
                        }
                    ]
                },
            }
        ],
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    intent_parts = cast(list[dict[str, object]], round_item["intent_parts"])
    assert round_item["intent"] == "这个是什么图片\n\n[image: image.png]"
    assert intent_parts == [
        {"kind": "text", "text": "这个是什么图片"},
        {
            "kind": "image-url",
            "modality": "image",
            "url": "https://cdn.example.com/assets/image.png",
            "media_type": "image/png",
            "label": "image.png",
            "name": "image.png",
        },
    ]


def test_build_session_rounds_projects_legacy_binary_user_prompt_intent_parts() -> None:
    session_id = "session-1"
    run_id = "run-persisted-binary"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="[image: stale-objective.png]",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo()),
        ),
        get_session_messages=lambda _: [
            {
                "trace_id": run_id,
                "role": "user",
                "created_at": "2026-04-21T10:46:50+00:00",
                "message": {
                    "parts": [
                        {
                            "part_kind": "user-prompt",
                            "content": [
                                "这个是什么图片",
                                {
                                    "kind": "binary",
                                    "media_type": "image/png",
                                    "data": "QUJD",
                                    "name": "legacy-image.png",
                                },
                            ],
                        }
                    ]
                },
            }
        ],
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    intent_parts = cast(list[dict[str, object]], round_item["intent_parts"])
    assert round_item["intent"] == "这个是什么图片\n\n[image: image]"
    assert intent_parts == [
        {"kind": "text", "text": "这个是什么图片"},
        {
            "kind": "binary",
            "media_type": "image/png",
            "data": "QUJD",
            "name": "legacy-image.png",
            "modality": "image",
            "label": "legacy-image.png",
        },
    ]


def test_build_session_rounds_only_keeps_active_retry_card() -> None:
    session_id = "session-1"
    run_id = "run-1"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )
    runtime = RunRuntimeRecord(
        run_id=run_id,
        session_id=session_id,
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
        get_session_events=lambda _: [
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:00Z",
                "payload_json": '{"attempt_number":2,"total_attempts":6,"retry_in_ms":1000,"error_code":"network_error"}',
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:01Z",
                "payload_json": '{"attempt_number":3,"total_attempts":6,"retry_in_ms":2000,"error_code":"network_error"}',
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.MODEL_STEP_STARTED.value,
                "occurred_at": "2026-03-19T12:00:02Z",
                "payload_json": "{}",
            },
        ],
    )

    assert len(rounds) == 1
    assert rounds[0]["retry_events"] == []

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
        get_session_events=lambda _: [
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:00Z",
                "payload_json": '{"attempt_number":2,"total_attempts":6,"retry_in_ms":1000,"error_code":"network_error"}',
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:01Z",
                "payload_json": '{"attempt_number":3,"total_attempts":6,"retry_in_ms":2000,"error_code":"network_error"}',
            },
        ],
    )

    retry_events = cast(list[dict[str, object]], rounds[0]["retry_events"])
    assert len(retry_events) == 1
    assert retry_events[0]["attempt_number"] == 3
    assert retry_events[0]["retry_in_ms"] == 2000
    assert retry_events[0]["phase"] == "scheduled"
    assert retry_events[0]["is_active"] is True

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
        get_session_events=lambda _: [
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:00Z",
                "payload_json": '{"attempt_number":2,"total_attempts":6,"retry_in_ms":1000,"error_code":"network_error"}',
            },
            {
                "trace_id": run_id,
                "event_type": "unrelated_event",
                "occurred_at": "2026-03-19T12:00:01Z",
                "payload_json": "{}",
            },
        ],
    )

    retry_events = cast(list[dict[str, object]], rounds[0]["retry_events"])
    assert retry_events[0]["phase"] == "scheduled"

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
        get_session_events=lambda _: [
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:00Z",
                "payload_json": '{"attempt_number":2,"total_attempts":6,"retry_in_ms":1000,"error_code":"network_error"}',
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.RUN_FAILED.value,
                "occurred_at": "2026-03-19T12:00:01Z",
                "payload_json": '{"error":"network_error"}',
            },
        ],
    )

    assert rounds[0]["retry_events"] == []


def test_build_session_rounds_keeps_exhausted_retry_card_after_run_failed() -> None:
    session_id = "session-1"
    run_id = "run-1"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )
    runtime = RunRuntimeRecord(
        run_id=run_id,
        session_id=session_id,
        status=RunRuntimeStatus.FAILED,
        phase=RunRuntimePhase.TERMINAL,
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
        get_session_events=lambda _: [
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_EXHAUSTED.value,
                "occurred_at": "2026-03-19T12:00:05Z",
                "payload_json": '{"attempt_number":6,"total_attempts":6,"error_code":"network_error","error_message":"still failing"}',
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.RUN_FAILED.value,
                "occurred_at": "2026-03-19T12:00:06Z",
                "payload_json": '{"error":"still failing"}',
            },
        ],
    )

    retry_events = cast(list[dict[str, object]], rounds[0]["retry_events"])
    assert len(retry_events) == 1
    assert retry_events[0]["attempt_number"] == 6
    assert retry_events[0]["phase"] == "failed"
    assert retry_events[0]["is_active"] is False


def test_build_session_rounds_projects_fallback_event() -> None:
    session_id = "session-1"
    run_id = "run-1"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )
    runtime = RunRuntimeRecord(
        run_id=run_id,
        session_id=session_id,
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
        get_session_events=lambda _: [
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_FALLBACK_ACTIVATED.value,
                "occurred_at": "2026-03-19T12:00:05Z",
                "payload_json": (
                    '{"from_profile_id":"primary","to_profile_id":"secondary",'
                    '"strategy_id":"same_provider_then_other_provider","hop":1}'
                ),
            },
        ],
    )

    retry_events = cast(list[dict[str, object]], rounds[0]["retry_events"])
    assert len(retry_events) == 1
    assert retry_events[0]["kind"] == "fallback"
    assert retry_events[0]["to_profile_id"] == "secondary"
    assert retry_events[0]["phase"] == "activated"


def test_build_session_rounds_keeps_fallback_event_after_run_completed() -> None:
    session_id = "session-1"
    run_id = "run-1"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )
    runtime = RunRuntimeRecord(
        run_id=run_id,
        session_id=session_id,
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
        get_session_events=lambda _: [
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_FALLBACK_ACTIVATED.value,
                "occurred_at": "2026-03-19T12:00:05Z",
                "payload_json": (
                    '{"from_profile_id":"primary","to_profile_id":"secondary",'
                    '"strategy_id":"same_provider_then_other_provider","hop":1}'
                ),
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.RUN_COMPLETED.value,
                "occurred_at": "2026-03-19T12:00:08Z",
                "payload_json": '{"completion_reason":"assistant_response"}',
            },
        ],
    )

    retry_events = cast(list[dict[str, object]], rounds[0]["retry_events"])
    assert len(retry_events) == 1
    assert retry_events[0]["kind"] == "fallback"
    assert retry_events[0]["to_profile_id"] == "secondary"
    assert retry_events[0]["phase"] == "activated"


def test_build_session_rounds_keeps_fallback_history_when_retry_follows() -> None:
    session_id = "session-1"
    run_id = "run-1"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )
    runtime = RunRuntimeRecord(
        run_id=run_id,
        session_id=session_id,
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo((root_task,)))),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
        get_session_events=lambda _: [
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_FALLBACK_ACTIVATED.value,
                "occurred_at": "2026-03-19T12:00:05Z",
                "payload_json": (
                    '{"from_profile_id":"primary","to_profile_id":"secondary",'
                    '"strategy_id":"same_provider_then_other_provider","hop":1}'
                ),
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:06Z",
                "payload_json": (
                    '{"attempt_number":2,"total_attempts":6,"retry_in_ms":2000,'
                    '"error_code":"network_error"}'
                ),
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.RUN_COMPLETED.value,
                "occurred_at": "2026-03-19T12:00:08Z",
                "payload_json": '{"completion_reason":"assistant_response"}',
            },
        ],
    )

    retry_events = cast(list[dict[str, object]], rounds[0]["retry_events"])
    assert len(retry_events) == 1
    assert retry_events[0]["kind"] == "fallback"
    assert retry_events[0]["to_profile_id"] == "secondary"
    assert retry_events[0]["phase"] == "activated"


def test_build_session_timeline_rounds_projects_lightweight_run_overlays() -> None:
    session_id = "session-1"
    run_id = "run-1"
    exhausted_run_id = "run-exhausted"
    fallback_run_id = "run-fallback"
    failed_after_retry_run_id = "run-failed-after-retry"
    excluded_run_id = "run-excluded"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="fallback objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    child_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-child",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            role_id="Worker",
            objective="child task",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    excluded_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-excluded",
            session_id=session_id,
            parent_task_id=None,
            trace_id=excluded_run_id,
            objective="excluded",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    runtimes = (
        RunRuntimeRecord(
            run_id=run_id,
            session_id=session_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING,
        ),
        RunRuntimeRecord(
            run_id=exhausted_run_id,
            session_id=session_id,
            status=RunRuntimeStatus.FAILED,
            phase=RunRuntimePhase.TERMINAL,
        ),
        RunRuntimeRecord(
            run_id=fallback_run_id,
            session_id=session_id,
            status=RunRuntimeStatus.COMPLETED,
            phase=RunRuntimePhase.TERMINAL,
        ),
        RunRuntimeRecord(
            run_id=failed_after_retry_run_id,
            session_id=session_id,
            status=RunRuntimeStatus.FAILED,
            phase=RunRuntimePhase.TERMINAL,
        ),
    )

    rounds = build_session_timeline_rounds(
        session_id=session_id,
        task_repo=cast(
            TaskRepository,
            cast(object, _FakeTaskRepo((root_task, child_task, excluded_task))),
        ),
        approval_tickets_by_run={run_id: [{"ticket_id": "approval-1"}]},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo(runtimes)),
        ),
        get_session_user_messages=lambda _: [
            {
                "trace_id": run_id,
                "created_at": "2026-03-19T12:00:00Z",
                "message": {
                    "parts": [{"part_kind": "user-prompt", "content": "from message"}]
                },
            },
            {"trace_id": "", "created_at": "2026-03-19T12:00:00Z", "message": {}},
        ],
        get_run_intent_input=lambda current_run_id: (
            (TextContentPart(text="persisted intent"),)
            if current_run_id == run_id
            else None
        ),
        get_session_events=lambda _: [
            {
                "trace_id": "",
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:00Z",
                "payload_json": "{}",
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:01Z",
                "payload_json": '{"attempt_number":2,"total_attempts":4,"retry_in_ms":1000,"error_code":"network_error"}',
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.MODEL_STEP_STARTED.value,
                "occurred_at": "2026-03-19T12:00:02Z",
                "payload_json": '{"microcompact_applied":true,"estimated_tokens_before_microcompact":120,"estimated_tokens_after_microcompact":60,"microcompact_compacted_message_count":2,"microcompact_compacted_part_count":3}',
            },
            {
                "trace_id": run_id,
                "event_type": RunEventType.MODEL_STEP_FINISHED.value,
                "occurred_at": "2026-03-19T12:00:03Z",
                "payload_json": '{"microcompact_applied":false,"estimated_tokens_before_microcompact":0,"estimated_tokens_after_microcompact":0,"microcompact_compacted_message_count":0,"microcompact_compacted_part_count":0}',
            },
            {
                "trace_id": exhausted_run_id,
                "event_type": RunEventType.LLM_RETRY_EXHAUSTED.value,
                "occurred_at": "2026-03-19T12:00:04Z",
                "payload_json": '{"attempt_number":4,"total_attempts":4,"error_code":"rate_limit","error_message":"still limited"}',
            },
            {
                "trace_id": fallback_run_id,
                "event_type": RunEventType.LLM_FALLBACK_ACTIVATED.value,
                "occurred_at": "2026-03-19T12:00:05Z",
                "payload_json": '{"from_profile_id":"primary","to_profile_id":"secondary","from_provider":"openai","to_provider":"backup","from_model":"a","to_model":"b","strategy_id":"default","hop":1,"reason":"timeout"}',
            },
            {
                "trace_id": fallback_run_id,
                "event_type": RunEventType.LLM_FALLBACK_EXHAUSTED.value,
                "occurred_at": "2026-03-19T12:00:06Z",
                "payload_json": '{"from_profile_id":"secondary","from_provider":"backup","from_model":"b","hop":2,"error_code":"all_failed","error_message":"done"}',
            },
            {
                "trace_id": failed_after_retry_run_id,
                "event_type": RunEventType.LLM_RETRY_SCHEDULED.value,
                "occurred_at": "2026-03-19T12:00:07Z",
                "payload_json": '{"attempt_number":2,"total_attempts":4,"retry_in_ms":1000,"error_code":"network_error"}',
            },
            {
                "trace_id": failed_after_retry_run_id,
                "event_type": RunEventType.RUN_FAILED.value,
                "occurred_at": "2026-03-19T12:00:08Z",
                "payload_json": '{"error":"network_error"}',
            },
        ],
        excluded_run_ids={excluded_run_id},
    )

    rounds_by_run = {str(item["run_id"]): item for item in rounds}

    assert excluded_run_id not in rounds_by_run
    assert rounds_by_run[run_id]["intent"] == "persisted intent"
    assert rounds_by_run[run_id]["primary_role_id"] == "Coordinator"
    assert rounds_by_run[run_id]["pending_tool_approval_count"] == 1
    assert rounds_by_run[run_id]["has_user_messages"] is True
    assert rounds_by_run[run_id]["retry_events"] == []
    assert rounds_by_run[run_id]["microcompact"] is None

    exhausted_retry_events = cast(
        list[dict[str, object]],
        rounds_by_run[exhausted_run_id]["retry_events"],
    )
    assert exhausted_retry_events[0]["phase"] == "failed"
    assert exhausted_retry_events[0]["error_message"] == "still limited"

    fallback_events = cast(
        list[dict[str, object]],
        rounds_by_run[fallback_run_id]["retry_events"],
    )
    assert [event["phase"] for event in fallback_events] == ["activated", "failed"]
    assert fallback_events[0]["to_profile_id"] == "secondary"
    assert fallback_events[1]["error_code"] == "all_failed"
    assert rounds_by_run[failed_after_retry_run_id]["retry_events"] == []


def test_build_session_rounds_excludes_background_subagent_runs() -> None:
    session_id = "session-1"
    main_run_id = "run-1"
    background_run_id = "subagent-run-1"
    main_root = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=main_run_id,
            role_id="MainAgent",
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )
    background_root = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-bg-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=background_run_id,
            role_id="Explorer",
            objective="background work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
    )
    main_runtime = RunRuntimeRecord(
        run_id=main_run_id,
        session_id=session_id,
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
    )
    background_runtime = RunRuntimeRecord(
        run_id=background_run_id,
        session_id=session_id,
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(
            TaskRepository,
            cast(object, _FakeTaskRepo((main_root, background_root))),
        ),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((main_runtime, background_runtime))),
        ),
        get_session_messages=lambda _: [],
        excluded_run_ids={background_run_id},
    )

    assert [round_item["run_id"] for round_item in rounds] == [main_run_id]

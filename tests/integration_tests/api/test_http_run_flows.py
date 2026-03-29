from __future__ import annotations

import httpx

from integration_tests.support.environment import IntegrationEnvironment
from integration_tests.support.api_helpers import (
    create_task_batch,
    create_run,
    create_session,
    dispatch_task,
    get_coordinator_role_id,
    new_session_id,
    stream_run_until_terminal,
)


def test_health_endpoint(api_client: httpx.Client) -> None:
    response = api_client.get("/api/system/health")
    response.raise_for_status()
    body = response.json()
    assert body["status"] == "ok"
    assert body["python_executable"]
    assert body["package_root"]
    assert body["config_dir"]
    assert body["builtin_roles_dir"]
    assert body["builtin_skills_dir"]
    role_registry_sanity = body["role_registry_sanity"]
    assert role_registry_sanity["builtin_role_count"] >= 1
    assert role_registry_sanity["has_builtin_coordinator"] is True
    assert role_registry_sanity["has_builtin_main_agent"] is True
    skill_registry_sanity = body["skill_registry_sanity"]
    assert skill_registry_sanity["builtin_skill_count"] >= 1
    assert skill_registry_sanity["has_builtin_deepresearch"] is True
    tool_registry_sanity = body["tool_registry_sanity"]
    assert tool_registry_sanity["available_tool_count"] >= 1
    assert tool_registry_sanity["has_write_tmp"] is True


def test_manual_run_stream_reaches_terminal(api_client: httpx.Client) -> None:
    session_id = create_session(api_client, session_id=new_session_id("session-manual"))
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="请初始化一个人工编排流程",
        execution_mode="manual",
    )

    events = stream_run_until_terminal(api_client, run_id=run_id)
    event_types = [str(event.get("event_type") or "") for event in events]

    assert "run_started" in event_types
    assert "awaiting_manual_action" in event_types
    assert event_types[-1] == "run_completed"


def test_ai_run_uses_fake_llm(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    before_response = httpx.get(
        f"{integration_env.fake_llm_admin_url}/metrics",
        timeout=5.0,
        trust_env=False,
    )
    before_response.raise_for_status()
    before_calls = int(before_response.json()["chat_completions_calls"])

    session_id = create_session(api_client, session_id=new_session_id("session-ai"))
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="输出一句简短确认",
        execution_mode="ai",
    )
    events = stream_run_until_terminal(api_client, run_id=run_id)
    event_types = [str(event.get("event_type") or "") for event in events]

    assert event_types[-1] == "run_completed"
    assert "run_failed" not in event_types

    after_response = httpx.get(
        f"{integration_env.fake_llm_admin_url}/metrics",
        timeout=5.0,
        trust_env=False,
    )
    after_response.raise_for_status()
    after_calls = int(after_response.json()["chat_completions_calls"])
    assert after_calls > before_calls


def test_task_dispatch_updates_round_task_maps(api_client: httpx.Client) -> None:
    session_id = create_session(api_client, session_id=new_session_id("session-task"))
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="创建两步时间查询流程",
        execution_mode="manual",
    )
    _ = stream_run_until_terminal(api_client, run_id=run_id)

    task_batch = create_task_batch(
        api_client,
        run_id=run_id,
        objective="time query chain",
    )
    tasks = task_batch.get("tasks")
    assert isinstance(tasks, list)
    coordinator_role_id = get_coordinator_role_id(api_client)
    task_ids = [
        str(item.get("task_id") or "") for item in tasks if isinstance(item, dict)
    ]
    assert len(task_ids) == 2
    assert all(task_ids)

    first_dispatch = dispatch_task(
        api_client,
        task_id=task_ids[0],
        role_id=coordinator_role_id,
    )
    second_dispatch = dispatch_task(
        api_client,
        task_id=task_ids[1],
        role_id=coordinator_role_id,
    )
    first_task = first_dispatch.get("task")
    second_task = second_dispatch.get("task")
    assert isinstance(first_task, dict)
    assert isinstance(second_task, dict)
    assert first_task.get("task_id") == task_ids[0]
    assert second_task.get("task_id") == task_ids[1]

    round_response = api_client.get(f"/api/sessions/{session_id}/rounds/{run_id}")
    round_response.raise_for_status()
    round_payload = round_response.json()

    task_instance_map = round_payload.get("task_instance_map")
    task_status_map = round_payload.get("task_status_map")
    assert isinstance(task_instance_map, dict)
    assert isinstance(task_status_map, dict)
    assert len(task_instance_map) >= 2
    assert len(set(str(value) for value in task_instance_map.values())) == 1
    assert "completed" in set(str(value) for value in task_status_map.values())

from __future__ import annotations

import json

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
    assert "write" in tool_registry_sanity["available_tool_names"]


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


def test_ai_run_continues_after_invalid_tool_args_validation_failure(
    api_client: httpx.Client,
) -> None:
    session_id = create_session(
        api_client,
        session_id=new_session_id("session-invalid-json"),
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[invalid-json-auto-recovery] 先执行一个工具，再从坏的工具参数 JSON 中自动恢复。",
        execution_mode="ai",
    )

    events = stream_run_until_terminal(api_client, run_id=run_id)
    event_types = [str(event.get("event_type") or "") for event in events]

    assert event_types[-1] == "run_completed"
    assert "run_paused" not in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "tool_input_validation_failed" in event_types

    validation_payloads = [
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event.get("event_type") or "") == "tool_input_validation_failed"
    ]
    assert any(
        payload.get("tool_name") == "read"
        and payload.get("reason") == "Input validation failed before tool execution."
        for payload in validation_payloads
    )


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


def test_ai_run_executes_builtin_computer_tools_with_fake_runtime(
    api_client: httpx.Client,
) -> None:
    original_response = api_client.get("/api/roles/configs/MainAgent")
    original_response.raise_for_status()
    original_record = original_response.json()
    assert isinstance(original_record, dict)

    updated_record = dict(original_record)
    updated_tools = {
        str(tool)
        for tool in updated_record.get("tools", [])
        if isinstance(tool, str) and tool
    }
    updated_tools.update({"capture_screen", "launch_app"})
    updated_record["tools"] = sorted(updated_tools)
    updated_record["execution_surface"] = "desktop"

    save_response = api_client.put(
        "/api/roles/configs/MainAgent",
        json=_role_draft_payload(updated_record),
    )
    save_response.raise_for_status()

    try:
        session_id = create_session(
            api_client,
            session_id=new_session_id("session-computer"),
        )
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent="[computer-validation] 通过内建电脑工具完成一次验证。",
            execution_mode="ai",
            yolo=True,
        )
        events = stream_run_until_terminal(api_client, run_id=run_id)
    finally:
        restore_response = api_client.put(
            "/api/roles/configs/MainAgent",
            json=_role_draft_payload(original_record),
        )
        restore_response.raise_for_status()

    events = _session_run_events(
        api_client,
        session_id=session_id,
        run_id=run_id,
    )
    tool_calls = [
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event.get("event_type") or "") == "tool_call"
    ]
    tool_results = [
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event.get("event_type") or "") == "tool_result"
    ]

    assert [payload["tool_name"] for payload in tool_calls] == [
        "capture_screen",
        "launch_app",
    ]
    assert [payload["tool_name"] for payload in tool_results] == [
        "capture_screen",
        "launch_app",
    ]

    capture_result = tool_results[0]["result"]
    assert capture_result["ok"] is True
    assert capture_result["data"]["computer"]["source"] == "tool"
    assert capture_result["data"]["computer"]["execution_surface"] == "desktop"
    assert capture_result["data"]["content"][0]["kind"] == "media_ref"

    launch_result = tool_results[1]["result"]
    assert launch_result["ok"] is True
    assert launch_result["data"]["computer"]["source"] == "tool"
    assert launch_result["data"]["computer"]["risk_level"] == "destructive"
    assert launch_result["data"]["observation"]["focused_window"] == "Calculator Window"


def test_ai_run_executes_real_computer_smoke_sequence_with_fake_runtime(
    api_client: httpx.Client,
) -> None:
    original_response = api_client.get("/api/roles/configs/MainAgent")
    original_response.raise_for_status()
    original_record = original_response.json()
    assert isinstance(original_record, dict)

    updated_record = dict(original_record)
    updated_tools = {
        str(tool)
        for tool in updated_record.get("tools", [])
        if isinstance(tool, str) and tool
    }
    updated_tools.update({"capture_screen", "launch_app", "wait_for_window"})
    updated_record["tools"] = sorted(updated_tools)
    updated_record["execution_surface"] = "desktop"

    save_response = api_client.put(
        "/api/roles/configs/MainAgent",
        json=_role_draft_payload(updated_record),
    )
    save_response.raise_for_status()

    try:
        session_id = create_session(
            api_client,
            session_id=new_session_id("session-real-computer"),
        )
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent="[computer-real-validation] 打开计算器，等待窗口出现，然后截图确认。",
            execution_mode="ai",
            yolo=True,
        )
        events = stream_run_until_terminal(api_client, run_id=run_id)
    finally:
        restore_response = api_client.put(
            "/api/roles/configs/MainAgent",
            json=_role_draft_payload(original_record),
        )
        restore_response.raise_for_status()

    events = _session_run_events(
        api_client,
        session_id=session_id,
        run_id=run_id,
    )
    tool_calls = [
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event.get("event_type") or "") == "tool_call"
    ]
    tool_results = [
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event.get("event_type") or "") == "tool_result"
    ]

    assert [payload["tool_name"] for payload in tool_calls] == [
        "launch_app",
        "wait_for_window",
        "capture_screen",
    ]
    assert [payload["tool_name"] for payload in tool_results] == [
        "launch_app",
        "wait_for_window",
        "capture_screen",
    ]

    launch_result = tool_results[0]["result"]
    assert launch_result["ok"] is True
    assert launch_result["data"]["computer"]["source"] == "tool"
    assert launch_result["data"]["computer"]["risk_level"] == "destructive"

    wait_result = tool_results[1]["result"]
    assert wait_result["ok"] is True
    assert wait_result["data"]["computer"]["action"] == "wait_for_window"
    assert wait_result["data"]["observation"]["focused_window"] == "Calculator Window"

    capture_result = tool_results[2]["result"]
    assert capture_result["ok"] is True
    assert capture_result["data"]["computer"]["action"] == "capture_screen"
    assert capture_result["data"]["content"][0]["kind"] == "media_ref"


def _role_draft_payload(record: dict[str, object]) -> dict[str, object]:
    return {
        "source_role_id": record.get("source_role_id"),
        "role_id": record["role_id"],
        "name": record["name"],
        "description": record["description"],
        "version": record["version"],
        "tools": record.get("tools", []),
        "mcp_servers": record.get("mcp_servers", []),
        "skills": record.get("skills", []),
        "model_profile": record["model_profile"],
        "bound_agent_id": record.get("bound_agent_id"),
        "execution_surface": record.get("execution_surface", "api"),
        "memory_profile": record["memory_profile"],
        "system_prompt": record["system_prompt"],
    }


def _session_run_events(
    api_client: httpx.Client,
    *,
    session_id: str,
    run_id: str,
) -> list[dict[str, object]]:
    response = api_client.get(f"/api/sessions/{session_id}/events")
    response.raise_for_status()
    payload = response.json()
    assert isinstance(payload, list)
    return [
        event
        for event in payload
        if isinstance(event, dict) and str(event.get("trace_id") or "") == run_id
    ]

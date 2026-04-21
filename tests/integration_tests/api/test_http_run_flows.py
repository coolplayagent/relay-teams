from __future__ import annotations

import json
import time

import httpx

from integration_tests.support.environment import IntegrationEnvironment
from integration_tests.support.api_helpers import (
    create_task_batch,
    create_run,
    create_session,
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


def test_ai_run_persists_todo_snapshot_and_projects_it(
    api_client: httpx.Client,
) -> None:
    session_id = create_session(
        api_client,
        session_id=new_session_id("session-todo"),
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[todo-validation] 维护当前 run 的 todo，并完成一次持久化校验。",
        execution_mode="ai",
        yolo=True,
    )

    events = stream_run_until_terminal(api_client, run_id=run_id)
    event_types = [str(event.get("event_type") or "") for event in events]

    assert event_types[-1] == "run_completed"
    assert "todo_updated" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types

    todo_response = api_client.get(f"/api/runs/{run_id}/todo")
    todo_response.raise_for_status()
    todo_payload = todo_response.json()
    todo = todo_payload.get("todo")
    assert isinstance(todo, dict)
    assert todo.get("run_id") == run_id
    assert todo.get("session_id") == session_id
    items = todo.get("items")
    assert isinstance(items, list)
    assert items == [
        {"content": "Inspect issue 399 requirements", "status": "completed"},
        {"content": "Implement run todo persistence", "status": "in_progress"},
        {"content": "Verify API and CLI output", "status": "pending"},
    ]

    round_response = api_client.get(f"/api/sessions/{session_id}/rounds/{run_id}")
    round_response.raise_for_status()
    round_payload = round_response.json()
    assert round_payload.get("todo") == todo


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
    tool_result_payloads = [
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event.get("event_type") or "") == "tool_result"
    ]
    assert any(
        payload.get("tool_name") == "read"
        and payload.get("error") is True
        and '"ok": false' in json.dumps(payload.get("result", {})).lower()
        for payload in tool_result_payloads
    )


def test_ai_run_retries_after_provider_rate_limit_once(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    before_calls = _get_fake_llm_call_count(integration_env)

    session_id = create_session(
        api_client,
        session_id=new_session_id("session-rate-limit"),
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[rate-limit-once] 请在一次限流后重试，并输出一句确认。",
        execution_mode="ai",
    )

    events = stream_run_until_terminal(api_client, run_id=run_id)
    event_types = [str(event.get("event_type") or "") for event in events]

    assert event_types[-1] == "run_completed"
    assert "run_paused" not in event_types
    retry_payloads = [
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event.get("event_type") or "") == "llm_retry_scheduled"
    ]
    if retry_payloads:
        assert any(payload.get("status_code") == 429 for payload in retry_payloads)
        assert any(
            payload.get("error_code") == "rate_limited" for payload in retry_payloads
        )

    after_calls = _get_fake_llm_call_count(integration_env)
    assert after_calls >= before_calls + 2


def test_ai_run_retries_after_stream_drop_once(api_client: httpx.Client) -> None:
    session_id = create_session(
        api_client,
        session_id=new_session_id("session-stream-drop"),
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[stream-drop-once] 请在一次流中断后重试，并输出一句确认。",
        execution_mode="ai",
    )

    events = stream_run_until_terminal(api_client, run_id=run_id)
    event_types = [str(event.get("event_type") or "") for event in events]

    assert event_types[-1] == "run_completed"
    assert "llm_retry_scheduled" in event_types
    assert "run_paused" not in event_types
    retry_payloads = [
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event.get("event_type") or "") == "llm_retry_scheduled"
    ]
    assert any(
        payload.get("error_code") in {"network_stream_interrupted", "network_error"}
        for payload in retry_payloads
    )


def test_ai_run_completes_over_slow_stream(api_client: httpx.Client) -> None:
    session_id = create_session(
        api_client,
        session_id=new_session_id("session-slow-stream"),
    )
    started_at = time.monotonic()
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[slow-stream] 请在慢响应链路下输出一句确认。",
        execution_mode="ai",
    )

    events = stream_run_until_terminal(api_client, run_id=run_id)
    elapsed = time.monotonic() - started_at
    event_types = [str(event.get("event_type") or "") for event in events]

    assert event_types[-1] == "run_completed"
    assert "llm_retry_scheduled" not in event_types
    assert "run_paused" not in event_types
    assert elapsed >= 0.45


def test_manual_task_dispatch_endpoint_is_not_exposed(api_client: httpx.Client) -> None:
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
    task_ids = [
        str(item.get("task_id") or "") for item in tasks if isinstance(item, dict)
    ]
    assert len(task_ids) == 2
    assert all(task_ids)

    response = api_client.post(
        f"/api/tasks/{task_ids[0]}/dispatch",
        json={"role_id": get_coordinator_role_id(api_client)},
    )

    assert response.status_code == 405


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
    _wait_for_role_tools(api_client, "MainAgent", {"capture_screen", "launch_app"})

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

    events = _wait_for_session_run_events(
        api_client,
        session_id=session_id,
        run_id=run_id,
        expected_event_counts={"tool_call": 2, "tool_result": 2},
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
    assert launch_result["data"]["observation"]["focused_window"] == "Notepad Window"


def test_ai_run_executes_builtin_mouse_computer_tools_with_fake_runtime(
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
    updated_tools.update({"click_at", "double_click_at", "drag_between", "scroll_view"})
    updated_record["tools"] = sorted(updated_tools)
    updated_record["execution_surface"] = "desktop"

    save_response = api_client.put(
        "/api/roles/configs/MainAgent",
        json=_role_draft_payload(updated_record),
    )
    save_response.raise_for_status()
    _wait_for_role_tools(
        api_client,
        "MainAgent",
        {"click_at", "double_click_at", "drag_between", "scroll_view"},
    )

    try:
        session_id = create_session(
            api_client,
            session_id=new_session_id("session-computer-mouse"),
        )
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent="[computer-mouse-validation] 通过内建鼠标工具完成一次验证。",
            execution_mode="ai",
            yolo=True,
        )
        _ = stream_run_until_terminal(api_client, run_id=run_id)
    finally:
        restore_response = api_client.put(
            "/api/roles/configs/MainAgent",
            json=_role_draft_payload(original_record),
        )
        restore_response.raise_for_status()

    events = _wait_for_session_run_events(
        api_client,
        session_id=session_id,
        run_id=run_id,
        expected_event_counts={"tool_call": 4, "tool_result": 4},
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
        "click_at",
        "double_click_at",
        "drag_between",
        "scroll_view",
    ]
    assert [payload["tool_name"] for payload in tool_results] == [
        "click_at",
        "double_click_at",
        "drag_between",
        "scroll_view",
    ]

    click_result = tool_results[0]["result"]
    assert click_result["ok"] is True
    assert click_result["data"]["computer"]["action"] == "click"
    assert click_result["data"]["computer"]["risk_level"] == "guarded"
    assert click_result["data"]["computer"]["target"]["x"] == 120
    assert click_result["data"]["computer"]["target"]["y"] == 240

    double_click_result = tool_results[1]["result"]
    assert double_click_result["ok"] is True
    assert double_click_result["data"]["computer"]["action"] == "double_click"
    assert double_click_result["data"]["computer"]["target"]["x"] == 120
    assert double_click_result["data"]["computer"]["target"]["y"] == 240

    drag_result = tool_results[2]["result"]
    assert drag_result["ok"] is True
    assert drag_result["data"]["computer"]["action"] == "drag"
    assert drag_result["data"]["computer"]["risk_level"] == "destructive"
    assert drag_result["data"]["computer"]["target"]["x"] == 120
    assert drag_result["data"]["computer"]["target"]["y"] == 240
    assert drag_result["data"]["computer"]["target"]["end_x"] == 360
    assert drag_result["data"]["computer"]["target"]["end_y"] == 420

    scroll_result = tool_results[3]["result"]
    assert scroll_result["ok"] is True
    assert scroll_result["data"]["computer"]["action"] == "scroll"
    assert scroll_result["data"]["computer"]["risk_level"] == "guarded"
    assert scroll_result["data"]["computer"]["target"]["amount"] == -3


def test_ai_run_executes_builtin_input_computer_tools_with_fake_runtime(
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
    updated_tools.update({"list_windows", "focus_window", "type_text", "hotkey"})
    updated_record["tools"] = sorted(updated_tools)
    updated_record["execution_surface"] = "desktop"

    save_response = api_client.put(
        "/api/roles/configs/MainAgent",
        json=_role_draft_payload(updated_record),
    )
    save_response.raise_for_status()
    _wait_for_role_tools(
        api_client,
        "MainAgent",
        {"list_windows", "focus_window", "type_text", "hotkey"},
    )

    try:
        session_id = create_session(
            api_client,
            session_id=new_session_id("session-computer-input"),
        )
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent="[computer-input-validation] 通过内建窗口和输入工具完成一次验证。",
            execution_mode="ai",
            yolo=True,
        )
        _ = stream_run_until_terminal(api_client, run_id=run_id)
    finally:
        restore_response = api_client.put(
            "/api/roles/configs/MainAgent",
            json=_role_draft_payload(original_record),
        )
        restore_response.raise_for_status()

    events = _wait_for_session_run_events(
        api_client,
        session_id=session_id,
        run_id=run_id,
        expected_event_counts={"tool_call": 4, "tool_result": 4},
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
        "focus_window",
        "list_windows",
        "type_text",
        "hotkey",
    ]
    assert [payload["tool_name"] for payload in tool_results] == [
        "focus_window",
        "list_windows",
        "type_text",
        "hotkey",
    ]

    focus_result = tool_results[0]["result"]
    assert focus_result["ok"] is True
    assert focus_result["data"]["computer"]["action"] == "focus_window"
    assert focus_result["data"]["computer"]["risk_level"] == "guarded"
    assert (
        focus_result["data"]["computer"]["target"]["window_title"] == "Agent Teams Demo"
    )
    assert focus_result["data"]["observation"]["focused_window"] == "Agent Teams Demo"

    list_result = tool_results[1]["result"]
    assert list_result["ok"] is True
    assert list_result["data"]["computer"]["action"] == "list_windows"
    assert list_result["data"]["computer"]["risk_level"] == "safe"
    assert list_result["data"]["observation"]["focused_window"] == "Agent Teams Demo"
    window_titles = [
        window["title"] for window in list_result["data"]["observation"]["windows"]
    ]
    assert "Agent Teams Demo" in window_titles

    type_result = tool_results[2]["result"]
    assert type_result["ok"] is True
    assert type_result["data"]["computer"]["action"] == "type_text"
    assert type_result["data"]["computer"]["risk_level"] == "guarded"
    assert type_result["data"]["computer"]["target"]["text"] == "hello from fake llm"

    hotkey_result = tool_results[3]["result"]
    assert hotkey_result["ok"] is True
    assert hotkey_result["data"]["computer"]["action"] == "hotkey"
    assert hotkey_result["data"]["computer"]["risk_level"] == "guarded"
    assert hotkey_result["data"]["computer"]["target"]["shortcut"] == "Ctrl+A"


def test_ai_run_executes_builtin_wait_for_window_computer_tools_with_fake_runtime(
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
    _wait_for_role_tools(
        api_client,
        "MainAgent",
        {"capture_screen", "launch_app", "wait_for_window"},
    )

    try:
        session_id = create_session(
            api_client,
            session_id=new_session_id("session-real-computer"),
        )
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent="[computer-real-validation] 打开记事本，等待窗口出现，然后截图确认。",
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

    events = _wait_for_session_run_events(
        api_client,
        session_id=session_id,
        run_id=run_id,
        expected_event_counts={"tool_call": 3, "tool_result": 3},
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
    assert wait_result["data"]["observation"]["focused_window"] == "Notepad Window"

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


def _wait_for_session_run_events(
    api_client: httpx.Client,
    *,
    session_id: str,
    run_id: str,
    expected_event_counts: dict[str, int],
    timeout_seconds: float = 20.0,
) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        events = _session_run_events(
            api_client,
            session_id=session_id,
            run_id=run_id,
        )
        observed_counts: dict[str, int] = {}
        for event in events:
            event_type = str(event.get("event_type") or "")
            if not event_type:
                continue
            observed_counts[event_type] = observed_counts.get(event_type, 0) + 1
        if all(
            observed_counts.get(event_type, 0) >= expected_count
            for event_type, expected_count in expected_event_counts.items()
        ):
            return events
        time.sleep(0.1)
    raise AssertionError(
        "Run events did not reach expected persisted counts within "
        f"{timeout_seconds}s for run {run_id}: expected {expected_event_counts}"
    )


def _wait_for_role_tools(
    client: httpx.Client,
    role_id: str,
    expected_tools: set[str],
    *,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/roles/configs/{role_id}")
        response.raise_for_status()
        payload = response.json()
        tools = {
            str(tool)
            for tool in payload.get("tools", [])
            if isinstance(tool, str) and tool
        }
        if expected_tools.issubset(tools):
            return
        time.sleep(0.1)
    raise AssertionError(
        f"Role {role_id} did not expose expected tools within {timeout_seconds}s: {sorted(expected_tools)}"
    )


def _get_fake_llm_call_count(integration_env: IntegrationEnvironment) -> int:
    response = httpx.get(
        f"{integration_env.fake_llm_admin_url}/metrics",
        timeout=5.0,
        trust_env=False,
    )
    response.raise_for_status()
    return int(response.json()["chat_completions_calls"])

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

import httpx

from integration_tests.support.api_helpers import (
    create_task_batch,
    create_run,
    create_session,
    new_session_id,
    stream_run_until_terminal,
)
from integration_tests.support.environment import IntegrationEnvironment


def test_user_prompt_hook_rewrites_prompt_and_persists_context_record(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
    tmp_path: Path,
) -> None:
    context_marker = "HookAdditionalContextMarker"
    script_path = _write_hook_script(
        tmp_path / "prompt_hook.py",
        f"""
import json
import sys

_ = json.load(sys.stdin)
print(json.dumps({{
    "decision": "updated_input",
    "updated_input": "rewritten from hook",
    "additional_context": ["{context_marker}"],
}}))
""",
    )
    _save_hooks(
        api_client,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ],
                    }
                ]
            }
        },
    )

    session_id = create_session(api_client, session_id=new_session_id("hook-prompt"))
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="original prompt should be replaced",
        execution_mode="ai",
    )
    events = stream_run_until_terminal(api_client, run_id=run_id)

    assert "[fake-llm] rewritten from hook" in _text_output(events)
    persisted_rows = _load_message_rows(
        integration_env.config_dir / "relay_teams.db", session_id
    )
    assert any(
        row[0] == "system" and context_marker in row[1] for row in persisted_rows
    )
    assert not any(
        row[0] == "user" and "original prompt should be replaced" in row[1]
        for row in persisted_rows
    )


def test_pre_tool_hook_rewrite_updates_real_tool_execution(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    script_path = _write_hook_script(
        tmp_path / "pretool_rewrite.py",
        """
import json
import sys

payload = json.load(sys.stdin)
if payload.get("tool_name") == "read":
    print(json.dumps({
        "decision": "updated_input",
        "updated_input": {"path": "README.md", "offset": 1, "limit": 20},
    }))
else:
    print(json.dumps({"decision": "allow"}))
""",
    )
    _save_hooks(
        api_client,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ],
                    }
                ]
            }
        },
    )

    session_id = create_session(api_client, session_id=new_session_id("hook-read"))
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[hook-read-rewrite] trigger one read tool call",
        execution_mode="ai",
    )
    events = stream_run_until_terminal(api_client, run_id=run_id)

    tool_result = _tool_result_payload(events, "read")
    assert tool_result is not None
    result_json = json.dumps(tool_result.get("result", {}), ensure_ascii=False)
    assert "README.md" in result_json
    assert any(
        str(event.get("event_type") or "") == "hook_decision_applied"
        for event in events
    )


def test_session_start_hook_env_reaches_shell_execution(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    script_path = _write_hook_script(
        tmp_path / "session_env.py",
        """
import json
import sys

_ = json.load(sys.stdin)
print(json.dumps({
    "decision": "set_env",
    "set_env": {"RT_HOOK_TEST": "from_hook_env"},
}))
""",
    )
    _save_hooks(
        api_client,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ],
                    }
                ]
            }
        },
    )

    session_id = create_session(
        api_client, session_id=new_session_id("hook-session-env")
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[hook-shell-env] run one shell command",
        execution_mode="ai",
        yolo=True,
    )
    events = stream_run_until_terminal(api_client, run_id=run_id)

    tool_result = _tool_result_payload(events, "shell")
    assert tool_result is not None
    result_json = json.dumps(tool_result.get("result", {}), ensure_ascii=False)
    assert "from_hook_env" in result_json


def test_post_tool_hook_deferred_action_triggers_followup_turn(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    script_path = _write_hook_script(
        tmp_path / "posttool_deferred.py",
        """
import json
import sys

payload = json.load(sys.stdin)
if payload.get("tool_name") == "read":
    print(json.dumps({
        "decision": "continue",
        "deferred_action": "Deferred follow-up instruction from hook",
    }))
else:
    print(json.dumps({"decision": "continue"}))
""",
    )
    _save_hooks(
        api_client,
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ],
                    }
                ]
            }
        },
    )

    session_id = create_session(api_client, session_id=new_session_id("hook-deferred"))
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[hook-deferred-followup] trigger deferred follow-up",
        execution_mode="ai",
    )
    events = stream_run_until_terminal(api_client, run_id=run_id)

    assert any(
        str(event.get("event_type") or "") == "hook_deferred" for event in events
    )
    assert "[fake-llm] deferred follow-up acknowledged" in _text_output(events)


def test_prompt_hook_handler_rewrites_prompt_via_llm_evaluation(
    api_client: httpx.Client,
) -> None:
    _save_hooks(
        api_client,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "prompt",
                                "prompt": "[hook-prompt-eval] $ARGUMENTS",
                            }
                        ],
                    }
                ]
            }
        },
    )

    session_id = create_session(
        api_client,
        session_id=new_session_id("hook-prompt-handler"),
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="this prompt should be rewritten by a prompt hook",
        execution_mode="ai",
    )
    events = stream_run_until_terminal(api_client, run_id=run_id)

    assert "[fake-llm] prompt rewrite target" in _text_output(events)
    assert any(
        _hook_payload_has(
            event,
            hook_event="UserPromptSubmit",
            event_type="hook_completed",
            decision="updated_input",
        )
        for event in events
    )


def test_agent_stop_hook_retries_run_with_followup_turn(
    api_client: httpx.Client,
) -> None:
    _save_hooks(
        api_client,
        {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "agent",
                                "role_id": "Explorer",
                                "prompt": "[hook-agent-stop] $ARGUMENTS",
                            }
                        ],
                    }
                ]
            }
        },
    )

    session_id = create_session(
        api_client,
        session_id=new_session_id("hook-agent-stop"),
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[hook-agent-stop-main] finish only after the stop hook asks for one more pass",
        execution_mode="ai",
    )
    events = stream_run_until_terminal(api_client, run_id=run_id)

    output = _text_output(events)
    assert "[fake-llm] first draft before stop hook retry" in output
    assert "[fake-llm] agent stop hook follow-up completed" in output
    assert any(
        _hook_payload_has(
            event,
            hook_event="Stop",
            event_type="hook_decision_applied",
            decision="retry",
        )
        for event in events
    )


def test_phase2_subagent_and_task_lifecycle_hooks_emit_run_events(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    script_path = _write_hook_script(
        tmp_path / "phase2_lifecycle.py",
        """
import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({
    "decision": "allow",
    "reason": payload.get("event_name", ""),
}))
""",
    )
    _save_hooks(
        api_client,
        {
            "hooks": {
                "TaskCreated": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ]
                    }
                ],
                "TaskCompleted": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ]
                    }
                ],
                "SubagentStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ]
                    }
                ],
                "SubagentStop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ]
                    }
                ],
            }
        },
    )

    session_id = create_session(
        api_client,
        session_id=new_session_id("hook-subagent-lifecycle"),
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[hook-subagent-lifecycle] spawn one synchronous subagent and finish",
        execution_mode="ai",
    )
    events = stream_run_until_terminal(api_client, run_id=run_id, timeout_seconds=80.0)
    persisted_events = _wait_for_run_events(
        api_client,
        session_id=session_id,
        run_id=run_id,
        required_event_type="hook_completed",
        required_hook_event="SubagentStop",
    )

    assert "[fake-llm] subagent lifecycle completed" in _text_output(events)
    for hook_event in ("SubagentStart", "SubagentStop"):
        assert any(
            _hook_payload_has(
                event,
                hook_event=hook_event,
                event_type="hook_completed",
            )
            for event in persisted_events
        ), f"missing hook event {hook_event}"


def test_phase2_explicit_task_created_hook_emits_run_events(
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    script_path = _write_hook_script(
        tmp_path / "phase2_explicit_task_lifecycle.py",
        """
import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({
    "decision": "allow",
    "reason": payload.get("event_name", ""),
}))
""",
    )
    _save_hooks(
        api_client,
        {
            "hooks": {
                "TaskCreated": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ]
                    }
                ],
                "TaskCompleted": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ]
                    }
                ],
            }
        },
    )

    session_id = create_session(
        api_client,
        session_id=new_session_id("hook-explicit-task-lifecycle"),
    )
    run_id = create_run(
        api_client,
        session_id=session_id,
        intent="[hook-subagent-lifecycle] spawn one synchronous subagent and finish",
        execution_mode="ai",
    )
    _ = stream_run_until_terminal(api_client, run_id=run_id, timeout_seconds=80.0)
    _ = create_task_batch(
        api_client,
        run_id=run_id,
        objective="Create explicit child tasks for lifecycle hook validation",
    )
    task_events = _wait_for_run_events(
        api_client,
        session_id=session_id,
        run_id=run_id,
        required_event_type="hook_completed",
        required_hook_event="TaskCreated",
    )

    assert any(
        _hook_payload_has(
            event,
            hook_event="TaskCreated",
            event_type="hook_completed",
        )
        for event in task_events
    )


def test_pre_and_post_compact_hooks_emit_events(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
    tmp_path: Path,
) -> None:
    script_path = _write_hook_script(
        tmp_path / "compact_hook.py",
        """
import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({
    "decision": "allow",
    "reason": payload.get("event_name", ""),
}))
""",
    )
    _save_hooks(
        api_client,
        {
            "hooks": {
                "PreCompact": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ]
                    }
                ],
                "PostCompact": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": _command_for_script(script_path),
                            }
                        ]
                    }
                ],
            }
        },
    )

    session_id = create_session(
        api_client,
        session_id=new_session_id("hook-compact"),
    )
    for phase in range(1, 6):
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent=_phase_prompt(phase=phase, line_count=260, block_count=4),
            execution_mode="ai",
            yolo=True,
        )
        _ = stream_run_until_terminal(api_client, run_id=run_id, timeout_seconds=80.0)

    recall_run_id = create_run(
        api_client,
        session_id=session_id,
        intent=_recall_prompt(max_phase=5),
        execution_mode="ai",
        yolo=True,
    )
    events = stream_run_until_terminal(
        api_client,
        run_id=recall_run_id,
        timeout_seconds=80.0,
    )

    database_path = integration_env.config_dir / "relay_teams.db"
    markers = _fetch_session_markers(database_path=database_path, session_id=session_id)
    assert markers
    assert any(
        _hook_payload_has(event, hook_event="PreCompact", event_type="hook_completed")
        for event in events
    )
    assert any(
        _hook_payload_has(event, hook_event="PostCompact", event_type="hook_completed")
        for event in events
    )


def test_skill_frontmatter_hook_rewrites_prompt_for_bound_role(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    skill_dir = integration_env.config_dir / "skills" / "hook-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    hook_script_path = skill_dir / "rewrite_hook.py"
    hook_script_path.write_text(
        """
import json
import sys

_ = json.load(sys.stdin)
print(json.dumps({
    "decision": "updated_input",
    "updated_input": "skill frontmatter rewrite",
}))
""".strip()
        + "\n",
        encoding="utf-8",
    )
    command_text = f'"{sys.executable}" "{hook_script_path.as_posix()}"'
    (skill_dir / "SKILL.md").write_text(
        f"""
---
name: hook-skill
description: integration test skill hook
hooks:
  UserPromptSubmit:
    - hooks:
        - type: command
          command: '{command_text}'
---
Use this skill only for integration tests.
""".strip()
        + "\n",
        encoding="utf-8",
    )

    reload_response = api_client.post("/api/system/configs/skills:reload")
    reload_response.raise_for_status()

    original_response = api_client.get("/api/roles/configs/MainAgent")
    original_response.raise_for_status()
    original_record = original_response.json()
    assert isinstance(original_record, dict)

    updated_record = dict(original_record)
    updated_skills = {
        str(skill)
        for skill in updated_record.get("skills", [])
        if isinstance(skill, str) and skill
    }

    try:
        if "*" not in updated_skills:
            updated_skills.add("hook-skill")
            updated_record["skills"] = sorted(updated_skills)
            save_response = api_client.put(
                "/api/roles/configs/MainAgent",
                json=_role_draft_payload(updated_record),
            )
            save_response.raise_for_status()
            _wait_for_role_skills(api_client, "MainAgent", {"hook-skill"})

        session_id = create_session(
            api_client,
            session_id=new_session_id("hook-skill-frontmatter"),
        )
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent="this should be rewritten by the skill frontmatter hook",
            execution_mode="ai",
        )
        events = stream_run_until_terminal(api_client, run_id=run_id)
    finally:
        restore_response = api_client.put(
            "/api/roles/configs/MainAgent",
            json=_role_draft_payload(original_record),
        )
        restore_response.raise_for_status()
        shutil.rmtree(skill_dir, ignore_errors=True)
        _ = api_client.post("/api/system/configs/skills:reload")

    assert "[fake-llm] skill frontmatter rewrite" in _text_output(events)
    assert any(
        _hook_payload_has(
            event,
            hook_event="UserPromptSubmit",
            event_type="hook_completed",
            decision="updated_input",
        )
        for event in events
    )


def test_role_frontmatter_hook_rewrites_prompt_for_bound_role(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
    tmp_path: Path,
) -> None:
    hook_script_path = _write_hook_script(
        tmp_path / "role_frontmatter_rewrite.py",
        """
import json
import sys

_ = json.load(sys.stdin)
print(json.dumps({
    "decision": "updated_input",
    "updated_input": "role frontmatter rewrite",
}))
""",
    )
    main_agent_path = integration_env.config_dir / "roles" / "MainAgent.md"
    original_content = (
        main_agent_path.read_text(encoding="utf-8")
        if main_agent_path.exists()
        else None
    )
    original_response = api_client.get("/api/roles/configs/MainAgent")
    original_response.raise_for_status()
    original_record = original_response.json()
    assert isinstance(original_record, dict)

    trigger_role_id = "role_hook_reload_trigger"
    command_text = f'"{sys.executable}" "{hook_script_path.as_posix()}"'
    main_agent_path.parent.mkdir(parents=True, exist_ok=True)
    main_agent_path.write_text(
        _role_markdown_content(
            original_record,
            hooks_block=(
                "hooks:\n"
                "  UserPromptSubmit:\n"
                "    - hooks:\n"
                "        - type: command\n"
                f"          command: {json.dumps(command_text, ensure_ascii=False)}\n"
            ),
        ),
        encoding="utf-8",
    )

    try:
        _create_role_reload_trigger(
            api_client,
            role_id=trigger_role_id,
            memory_profile=original_record["memory_profile"],
        )
        session_id = create_session(
            api_client,
            session_id=new_session_id("hook-role-frontmatter"),
        )
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent="this should be rewritten by the role frontmatter hook",
            execution_mode="ai",
        )
        events = stream_run_until_terminal(api_client, run_id=run_id)
    finally:
        if original_content is None:
            if main_agent_path.exists():
                main_agent_path.unlink()
        else:
            main_agent_path.write_text(original_content, encoding="utf-8")
        delete_response = api_client.delete(f"/api/roles/configs/{trigger_role_id}")
        if delete_response.status_code not in (200, 404):
            delete_response.raise_for_status()

    assert "[fake-llm] role frontmatter rewrite" in _text_output(events)
    assert any(
        _hook_payload_has(
            event,
            hook_event="UserPromptSubmit",
            event_type="hook_completed",
            decision="updated_input",
        )
        for event in events
    )


def _save_hooks(api_client: httpx.Client, payload: dict[str, object]) -> None:
    response = api_client.put("/api/system/configs/hooks", json=payload)
    response.raise_for_status()


def _write_hook_script(path: Path, body: str) -> Path:
    path.write_text(body.strip() + "\n", encoding="utf-8")
    return path


def _command_for_script(path: Path) -> str:
    return f'"{sys.executable}" "{path}"'


def _text_output(events: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for event in events:
        if str(event.get("event_type") or "") != "text_delta":
            continue
        payload = json.loads(str(event.get("payload_json") or "{}"))
        parts.append(str(payload.get("text") or ""))
    return "".join(parts)


def _tool_result_payload(
    events: list[dict[str, object]],
    tool_name: str,
) -> dict[str, object] | None:
    for event in events:
        if str(event.get("event_type") or "") != "tool_result":
            continue
        payload = json.loads(str(event.get("payload_json") or "{}"))
        if str(payload.get("tool_name") or "") == tool_name:
            return payload
    return None


def _load_message_rows(database_path: Path, session_id: str) -> list[tuple[str, str]]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT role, message_json FROM messages WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [(str(role), str(message_json)) for role, message_json in rows]


def _hook_payload_has(
    event: dict[str, object],
    *,
    hook_event: str,
    event_type: str,
    decision: str | None = None,
) -> bool:
    if str(event.get("event_type") or "") != event_type:
        return False
    payload = json.loads(str(event.get("payload_json") or "{}"))
    if str(payload.get("hook_event") or "") != hook_event:
        return False
    if decision is not None and str(payload.get("decision") or "") != decision:
        return False
    return True


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


def _create_role_reload_trigger(
    api_client: httpx.Client,
    *,
    role_id: str,
    memory_profile: object,
) -> None:
    response = api_client.put(
        f"/api/roles/configs/{role_id}",
        json={
            "source_role_id": None,
            "role_id": role_id,
            "name": "Role Hook Reload Trigger",
            "description": "Temporary role to force role registry reload during tests.",
            "version": "1.0.0",
            "tools": [],
            "mcp_servers": [],
            "skills": [],
            "model_profile": "default",
            "bound_agent_id": None,
            "execution_surface": "api",
            "memory_profile": memory_profile,
            "system_prompt": "Temporary role used only for integration tests.",
        },
    )
    response.raise_for_status()


def _role_markdown_content(
    record: dict[str, object],
    *,
    hooks_block: str,
) -> str:
    frontmatter_lines = [
        "---",
        f"role_id: {json.dumps(str(record['role_id']), ensure_ascii=False)}",
        f"name: {json.dumps(str(record['name']), ensure_ascii=False)}",
        f"description: {json.dumps(str(record['description']), ensure_ascii=False)}",
        f"version: {json.dumps(str(record['version']), ensure_ascii=False)}",
        f"tools: {json.dumps(record.get('tools', []), ensure_ascii=False)}",
        f"mcp_servers: {json.dumps(record.get('mcp_servers', []), ensure_ascii=False)}",
        f"skills: {json.dumps(record.get('skills', []), ensure_ascii=False)}",
        f"model_profile: {json.dumps(str(record['model_profile']), ensure_ascii=False)}",
        (
            f"bound_agent_id: {json.dumps(record.get('bound_agent_id'), ensure_ascii=False)}"
        ),
        (
            f"execution_surface: {json.dumps(str(record.get('execution_surface', 'api')), ensure_ascii=False)}"
        ),
        (f"memory_profile: {json.dumps(record['memory_profile'], ensure_ascii=False)}"),
        hooks_block.rstrip(),
        "---",
        str(record["system_prompt"]).rstrip(),
        "",
    ]
    return "\n".join(frontmatter_lines)


def _wait_for_role_skills(
    api_client: httpx.Client,
    role_id: str,
    required_skills: set[str],
    *,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = api_client.get(f"/api/roles/configs/{role_id}")
        response.raise_for_status()
        body = response.json()
        skills = {
            str(skill)
            for skill in body.get("skills", [])
            if isinstance(skill, str) and skill
        }
        if required_skills.issubset(skills):
            return
        time.sleep(0.1)
    raise AssertionError(
        f"Role {role_id} did not expose required skills: {sorted(required_skills)}"
    )


def _wait_for_run_events(
    api_client: httpx.Client,
    *,
    session_id: str,
    run_id: str,
    required_event_type: str,
    required_hook_event: str,
    timeout_seconds: float = 5.0,
) -> list[dict[str, object]]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = api_client.get(f"/api/sessions/{session_id}/events")
        response.raise_for_status()
        body = response.json()
        if isinstance(body, list):
            items = body
        else:
            items = body.get("items")
            if not isinstance(items, list):
                items = []
        matching_events = [
            event
            for event in items
            if isinstance(event, dict)
            and str(event.get("run_id") or event.get("trace_id") or "") == run_id
        ]
        if any(
            _hook_payload_has(
                event,
                hook_event=required_hook_event,
                event_type=required_event_type,
            )
            for event in matching_events
        ):
            return matching_events
        time.sleep(0.1)
    raise AssertionError(
        f"Run {run_id} did not publish {required_event_type} for {required_hook_event}"
    )


_GLOBAL_FACTS = {
    "codename": "ORBIT-LANTERN",
    "recovery phrase": "cyan maple 2719",
    "key file": "src/relay_teams/agents/execution/llm_session.py",
    "version tag": "2026-04-08-it",
}
_PHASE_ANCHORS = {
    1: "amber-delta-104",
    2: "cobalt-echo-205",
    3: "fossil-jade-306",
    4: "lunar-mint-407",
    5: "nylon-orbit-508",
}
_PHASE_CHECKSUMS = {
    1: "CHK-P1-AX4",
    2: "CHK-P2-BY5",
    3: "CHK-P3-CZ6",
    4: "CHK-P4-DQ7",
    5: "CHK-P5-ER8",
}


def _phase_prompt(*, phase: int, line_count: int, block_count: int) -> str:
    lines = [
        f"[rolling-summary-phase:{phase}]",
        f"line count: {line_count}",
        f"block count: {block_count}",
        "Preserve these exact facts for later recall.",
    ]
    for label, value in _GLOBAL_FACTS.items():
        lines.append(f"- {label}: {value}")
    lines.extend(
        [
            f"- phase-{phase} anchor: {_PHASE_ANCHORS[phase]}",
            f"- phase-{phase} checksum: {_PHASE_CHECKSUMS[phase]}",
            "Run the planned shell tool call and then reply exactly with phase-N-done.",
        ]
    )
    return "\n".join(lines)


def _recall_prompt(*, max_phase: int) -> str:
    lines = [
        "[rolling-summary-recall]",
        "Return exact remembered facts only.",
    ]
    for label in _GLOBAL_FACTS:
        lines.append(f"- {label}")
    for phase in range(1, max_phase + 1):
        lines.append(f"- phase-{phase} anchor")
        lines.append(f"- phase-{phase} checksum")
    return "\n".join(lines)


def _fetch_session_markers(
    *,
    database_path: Path,
    session_id: str,
) -> list[dict[str, object]]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT marker_id, marker_type, created_at, metadata_json
            FROM session_history_markers
            WHERE session_id=?
            ORDER BY created_at
            """,
            (session_id,),
        ).fetchall()
    result: list[dict[str, object]] = []
    for marker_id, marker_type, created_at, metadata_json in rows:
        result.append(
            {
                "marker_id": str(marker_id),
                "marker_type": str(marker_type),
                "created_at": str(created_at),
                "metadata": json.loads(str(metadata_json)),
            }
        )
    return result

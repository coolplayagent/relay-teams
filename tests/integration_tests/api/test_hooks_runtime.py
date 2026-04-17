from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import httpx

from integration_tests.support.api_helpers import (
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

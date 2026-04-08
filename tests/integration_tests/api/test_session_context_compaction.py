from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import httpx

from integration_tests.support.api_helpers import (
    create_run,
    create_session,
    new_session_id,
    stream_run_until_terminal,
)
from integration_tests.support.environment import IntegrationEnvironment

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


def test_short_history_microcompact_preserves_exact_recall_without_marker(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    session_id = create_session(
        api_client,
        session_id=new_session_id("session-microcompact-short"),
    )

    phase_run_id = create_run(
        api_client,
        session_id=session_id,
        intent=_phase_prompt(phase=1, line_count=120, block_count=1),
        execution_mode="ai",
        yolo=True,
    )
    phase_events = stream_run_until_terminal(
        api_client,
        run_id=phase_run_id,
        timeout_seconds=80.0,
    )
    assert str(phase_events[-1].get("event_type") or "") == "run_completed"

    recall_run_id = create_run(
        api_client,
        session_id=session_id,
        intent=_recall_prompt(max_phase=1),
        execution_mode="ai",
        yolo=True,
    )
    recall_events = stream_run_until_terminal(
        api_client,
        run_id=recall_run_id,
        timeout_seconds=80.0,
    )
    assert str(recall_events[-1].get("event_type") or "") == "run_completed"

    database_path = _database_path(integration_env)
    markers = _fetch_session_markers(database_path=database_path, session_id=session_id)
    recall_text = _latest_assistant_text(
        database_path=database_path,
        session_id=session_id,
    )
    recall_usage_response = api_client.get(
        f"/api/sessions/{session_id}/runs/{recall_run_id}/token-usage"
    )
    recall_usage_response.raise_for_status()
    recall_usage = recall_usage_response.json()
    rounds_response = api_client.get(f"/api/sessions/{session_id}/rounds")
    rounds_response.raise_for_status()
    rounds_payload = rounds_response.json()
    rounds_items = rounds_payload.get("items")

    assert markers == []
    assert recall_text == _expected_recall_text(max_phase=1)
    assert int(recall_usage["total_tool_calls"]) == 0
    assert isinstance(rounds_items, list)
    recall_round = next(
        item
        for item in rounds_items
        if isinstance(item, dict) and str(item.get("run_id") or "") == recall_run_id
    )
    assert recall_round.get("compaction_marker_before") is None
    microcompact = recall_round.get("microcompact")
    assert isinstance(microcompact, dict)
    assert microcompact.get("applied") is True
    assert int(microcompact.get("estimated_tokens_before") or 0) > int(
        microcompact.get("estimated_tokens_after") or 0
    )
    assert int(microcompact.get("compacted_message_count") or 0) >= 1
    assert int(microcompact.get("compacted_part_count") or 0) >= 1


def test_multiple_rolling_summary_rewrites_preserve_rounds_and_exact_recall(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    session_id = create_session(
        api_client,
        session_id=new_session_id("session-rolling-summary"),
    )

    phase_run_ids: list[str] = []
    for phase in range(1, 6):
        run_id = create_run(
            api_client,
            session_id=session_id,
            intent=_phase_prompt(phase=phase, line_count=260, block_count=4),
            execution_mode="ai",
            yolo=True,
        )
        phase_run_ids.append(run_id)
        phase_events = stream_run_until_terminal(
            api_client,
            run_id=run_id,
            timeout_seconds=80.0,
        )
        assert str(phase_events[-1].get("event_type") or "") == "run_completed"

    database_path = _database_path(integration_env)
    markers = _fetch_session_markers(database_path=database_path, session_id=session_id)
    hidden_count = _hidden_message_count(
        database_path=database_path,
        session_id=session_id,
    )
    assert len(markers) >= 2
    assert hidden_count > 0
    latest_marker = markers[-1]
    latest_metadata = latest_marker.get("metadata")
    assert isinstance(latest_metadata, dict)
    latest_summary = str(latest_metadata.get("summary_markdown") or "")
    assert "ORBIT-LANTERN" in latest_summary
    assert "phase-1 anchor" in latest_summary
    assert "phase-4 checksum" in latest_summary
    assert str(latest_metadata.get("compaction_strategy") or "") == "rolling_summary"
    assert str(latest_metadata.get("estimated_tokens_after_microcompact") or "")
    assert int(str(latest_metadata.get("protected_tail_messages") or "0")) <= 12

    recall_run_id = create_run(
        api_client,
        session_id=session_id,
        intent=_recall_prompt(max_phase=5),
        execution_mode="ai",
        yolo=True,
    )
    recall_events = stream_run_until_terminal(
        api_client,
        run_id=recall_run_id,
        timeout_seconds=80.0,
    )
    assert str(recall_events[-1].get("event_type") or "") == "run_completed"

    rounds_response = api_client.get(f"/api/sessions/{session_id}/rounds")
    rounds_response.raise_for_status()
    rounds_payload = rounds_response.json()
    rounds_items = rounds_payload.get("items")
    assert isinstance(rounds_items, list)
    assert any(
        isinstance(item, dict)
        and isinstance(item.get("compaction_marker_before"), dict)
        and str(item["compaction_marker_before"].get("label") or "")
        == "History compacted (rolling summary)"
        for item in rounds_items
    )
    assert any(
        isinstance(item, dict)
        and isinstance(item.get("microcompact"), dict)
        and item["microcompact"].get("applied") is True
        for item in rounds_items
    )

    recall_text = _latest_assistant_text(
        database_path=database_path,
        session_id=session_id,
    )
    assert recall_text == _expected_recall_text(max_phase=5)


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


def _expected_recall_text(*, max_phase: int) -> str:
    lines = [f"- {label}: {value}" for label, value in _GLOBAL_FACTS.items()]
    for phase in range(1, max_phase + 1):
        lines.append(f"- phase-{phase} anchor: {_PHASE_ANCHORS[phase]}")
        lines.append(f"- phase-{phase} checksum: {_PHASE_CHECKSUMS[phase]}")
    return "\n".join(lines)


def _database_path(integration_env: IntegrationEnvironment) -> Path:
    return integration_env.config_dir / "relay_teams.db"


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


def _hidden_message_count(*, database_path: Path, session_id: str) -> int:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE session_id=? AND hidden_from_context=1
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def _latest_assistant_text(*, database_path: Path, session_id: str) -> str:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT message_json
            FROM messages
            WHERE session_id=? AND role='assistant'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return ""
    payload = json.loads(str(row[0]))
    texts: list[str] = []
    for message in payload:
        if not isinstance(message, dict):
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            content = part.get("content")
            if part.get("part_kind") == "text" and isinstance(content, str):
                texts.append(content)
    return "\n".join(texts).strip()

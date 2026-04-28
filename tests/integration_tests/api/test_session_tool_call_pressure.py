from __future__ import annotations

import httpx
import pytest

from integration_tests.support.api_helpers import (
    create_session,
    new_session_id,
)
from integration_tests.support.environment import IntegrationEnvironment
from integration_tests.support.session_tool_pressure import (
    assert_backend_probes_stayed_responsive,
    run_pressure_scenario,
)


@pytest.mark.timeout(140)
def test_normal_mode_session_concurrent_tool_calls_do_not_starve_backend(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    session_ids = [
        create_session(
            api_client,
            session_id=new_session_id(f"normal-tool-pressure-{index:02d}"),
        )
        for index in range(10)
    ]

    result = run_pressure_scenario(
        integration_env=integration_env,
        session_ids=session_ids,
        intent_template=(
            "[normal-tool-pressure count=4 delay=320 tag=normal{index}] "
            "run concurrent shell pressure in normal mode."
        ),
        timeout_seconds=90.0,
    )

    assert {run.terminal_event_type for run in result.runs} == {"run_completed"}
    assert sum(run.event_counts.get("tool_call", 0) for run in result.runs) >= 40
    assert sum(run.event_counts.get("tool_result", 0) for run in result.runs) >= 40
    assert all(
        "[fake-llm] normal tool pressure completed" in run.output_text
        for run in result.runs
    )
    assert_backend_probes_stayed_responsive(result.probes)


@pytest.mark.timeout(180)
def test_orchestration_mode_session_concurrent_tool_calls_do_not_starve_backend(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    session_ids = [
        create_session(
            api_client,
            session_id=new_session_id(f"orch-tool-pressure-{index:02d}"),
        )
        for index in range(3)
    ]
    for session_id in session_ids:
        response = api_client.patch(
            f"/api/sessions/{session_id}/topology",
            json={"session_mode": "orchestration"},
        )
        response.raise_for_status()

    result = run_pressure_scenario(
        integration_env=integration_env,
        session_ids=session_ids,
        intent_template=(
            "[orch-tool-pressure count=4 tools=3 delay=260] "
            "dispatch tool-heavy workers in orchestrated mode."
        ),
        timeout_seconds=140.0,
    )

    assert {run.terminal_event_type for run in result.runs} == {"run_completed"}
    assert sum(run.event_counts.get("tool_call", 0) for run in result.runs) >= 12
    assert sum(run.event_counts.get("tool_result", 0) for run in result.runs) >= 12
    assert all(
        "[fake-llm] orchestration tool pressure completed" in run.output_text
        for run in result.runs
    )
    assert_backend_probes_stayed_responsive(result.probes)

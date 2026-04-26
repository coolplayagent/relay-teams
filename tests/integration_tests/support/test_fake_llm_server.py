# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

from integration_tests.support.fake_llm_server import plan_fake_response


def test_computer_validation_mode_uses_latest_user_prompt_only() -> None:
    response = plan_fake_response(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "[computer-real-validation] open notepad",
                },
                {
                    "role": "user",
                    "content": "runtime context appended after the original prompt",
                },
            ],
            "tools": [
                {"function": {"name": "launch_app"}},
                {"function": {"name": "wait_for_window"}},
                {"function": {"name": "capture_screen"}},
            ],
        }
    )

    assert response["kind"] == "text"
    content = cast(str, response["content"])
    assert "runtime context appended after the original prompt" in content


def test_computer_validation_mode_detects_marker_on_latest_user_prompt() -> None:
    response = plan_fake_response(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "[computer-real-validation] open notepad",
                },
            ],
            "tools": [
                {"function": {"name": "launch_app"}},
                {"function": {"name": "wait_for_window"}},
                {"function": {"name": "capture_screen"}},
            ],
        }
    )

    assert response["kind"] == "tool_call"
    assert response["tool_name"] == "launch_app"
    assert response["tool_call_id"] == "call-real-launch-app-1"

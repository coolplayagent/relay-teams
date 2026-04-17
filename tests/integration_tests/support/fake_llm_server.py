from __future__ import annotations

from collections.abc import Iterator
import json
import re
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Fake OpenAI-Compatible LLM")

_chat_completions_calls = 0
_scenario_attempts: dict[str, int] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> dict[str, object]:
    return {
        "chat_completions_calls": _chat_completions_calls,
        "scenario_attempts": dict(_scenario_attempts),
    }


@app.post("/admin/reset")
def reset() -> dict[str, str]:
    global _chat_completions_calls
    _chat_completions_calls = 0
    _scenario_attempts.clear()
    return {"status": "ok"}


@app.get("/v1/models")
def list_models() -> dict[str, object]:
    return {
        "object": "list",
        "data": [
            {
                "id": "fake-chat-model",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "integration-tests",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    global _chat_completions_calls
    _chat_completions_calls += 1
    payload = await request.json()
    model = str(payload.get("model") or "fake-chat-model")
    response_spec = plan_fake_response(payload)
    stream = bool(payload.get("stream"))
    if str(response_spec.get("kind") or "") == "error_status":
        _sleep_ms(response_spec.get("delay_before_ms"))
        return JSONResponse(
            status_code=_coerce_int(response_spec.get("status_code"), default=500),
            content=response_spec.get("body")
            or {"error": {"code": "fake_error", "message": "fake error"}},
            headers=_normalize_headers(response_spec.get("headers")),
        )

    if stream:
        return StreamingResponse(
            stream_chat_completions(model=model, response_spec=response_spec),
            media_type="text/event-stream",
        )

    return JSONResponse(
        build_chat_completion_response(model=model, response_spec=response_spec)
    )


def stream_chat_completions(
    *,
    model: str,
    response_spec: dict[str, object],
) -> Iterator[bytes]:
    created = int(time.time())
    completion_id = f"chatcmpl-{_chat_completions_calls}"
    _sleep_ms(response_spec.get("delay_before_ms"))

    response_kind = str(response_spec.get("kind") or "")
    if response_kind in {"tool_call", "invalid_tool_call"}:
        tool_name = str(response_spec.get("tool_name") or "")
        tool_call_id = str(response_spec.get("tool_call_id") or "")
        if response_kind == "invalid_tool_call":
            arguments = str(response_spec.get("arguments_text") or "")
        else:
            arguments = json.dumps(
                response_spec.get("arguments") or {},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": arguments,
                                },
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        _maybe_abort_stream(response_spec, emitted_chunk_count=1)
        _sleep_ms(response_spec.get("delay_between_chunks_ms"))
        final_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        }
        yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"
        return

    content = str(response_spec.get("content") or "")
    chunk_size = max(1, _coerce_int(response_spec.get("chunk_size"), default=12))
    chunks = split_text(content, size=chunk_size)

    for index, part in enumerate(chunks):
        delta: dict[str, str] = {"content": part}
        if index == 0:
            delta["role"] = "assistant"
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        _maybe_abort_stream(response_spec, emitted_chunk_count=index + 1)
        _sleep_ms(response_spec.get("delay_between_chunks_ms"))

    final_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


def build_chat_completion_response(
    *,
    model: str,
    response_spec: dict[str, object],
) -> dict[str, object]:
    message: dict[str, object]
    finish_reason = "stop"
    response_kind = str(response_spec.get("kind") or "")
    if response_kind in {"tool_call", "invalid_tool_call"}:
        finish_reason = "tool_calls"
        if response_kind == "invalid_tool_call":
            arguments = str(response_spec.get("arguments_text") or "")
        else:
            arguments = json.dumps(
                response_spec.get("arguments") or {},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": str(response_spec.get("tool_call_id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(response_spec.get("tool_name") or ""),
                        "arguments": arguments,
                    },
                }
            ],
        }
    else:
        message = {
            "role": "assistant",
            "content": str(response_spec.get("content") or ""),
        }
    return {
        "id": f"chatcmpl-{_chat_completions_calls}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 8,
            "total_tokens": 16,
        },
    }


def plan_fake_response(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {"kind": "text", "content": "fake-response"}
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return {"kind": "text", "content": "fake-response"}
    if _rolling_summary_compaction_mode(messages):
        return _plan_rolling_summary_compaction_response(messages)
    if _rolling_summary_phase_mode(messages):
        return _plan_rolling_summary_phase_response(payload, messages)
    if _rolling_summary_recall_mode(messages):
        return _plan_rolling_summary_recall_response(messages)
    if _invalid_json_auto_recovery_mode(messages):
        return _plan_invalid_json_auto_recovery_response(payload, messages)
    if _hook_read_rewrite_mode(messages):
        return _plan_hook_read_rewrite_response(payload, messages)
    if _hook_shell_env_mode(messages):
        return _plan_hook_shell_env_response(payload, messages)
    if _hook_deferred_followup_mode(messages):
        return _plan_hook_deferred_followup_response(payload, messages)
    if _rate_limit_retry_mode(messages):
        return _plan_rate_limit_retry_response(messages)
    if _stream_drop_retry_mode(messages):
        return _plan_stream_drop_retry_response(messages)
    if _slow_stream_mode(messages):
        return _plan_slow_stream_response()
    if _webfetch_approval_validation_mode(messages):
        return _plan_webfetch_approval_validation_response(payload, messages)
    computer_validation_mode = _computer_validation_mode(messages)
    if computer_validation_mode is not None:
        response_spec = _plan_computer_validation_response(
            payload,
            messages,
            mode=computer_validation_mode,
        )
        if response_spec is not None:
            return response_spec
    return {"kind": "text", "content": build_fake_response_text(payload)}


def _rolling_summary_compaction_mode(messages: list[object]) -> bool:
    return _messages_contain_text(
        messages,
        "You maintain a rolling compact summary for one ongoing agent conversation.",
    ) and _messages_contain_user_text(messages, "Transcript to absorb:")


def _plan_rolling_summary_compaction_response(
    messages: list[object],
) -> dict[str, object]:
    facts = _extract_exact_facts("\n".join(_iter_message_texts(messages)))
    return {
        "kind": "text",
        "content": _render_rolling_summary_markdown(facts),
        "chunk_size": 4096,
    }


def _rolling_summary_phase_mode(messages: list[object]) -> bool:
    return "[rolling-summary-phase:" in _extract_last_user_text(messages)


def _plan_rolling_summary_phase_response(
    payload: dict[str, object],
    messages: list[object],
) -> dict[str, object]:
    phase = _extract_rolling_summary_phase(_extract_last_user_text(messages))
    if phase is None:
        return {"kind": "text", "content": "[fake-llm] invalid rolling summary phase"}
    available_tools = _extract_available_tools(payload)
    if "shell" not in available_tools:
        return {
            "kind": "text",
            "content": "[fake-llm] shell is not available for this role.",
        }
    last_user_text = _extract_last_user_text(messages)
    attempt = _next_scenario_attempt(
        messages,
        marker=f"[rolling-summary-phase:{phase}]",
    )
    block_count = _extract_rolling_summary_block_count(last_user_text)
    if attempt <= block_count:
        facts = _extract_exact_facts(last_user_text)
        block_index = attempt - 1
        return {
            "kind": "tool_call",
            "tool_name": "shell",
            "tool_call_id": f"call-rolling-summary-phase-{phase}-{attempt}",
            "arguments": {
                "command": _build_rolling_summary_shell_command(
                    phase=phase,
                    facts=facts,
                    line_count=_extract_rolling_summary_line_count(last_user_text),
                    block_index=block_index,
                )
            },
        }
    return {
        "kind": "text",
        "content": f"phase-{phase}-done",
    }


def _rolling_summary_recall_mode(messages: list[object]) -> bool:
    return "[rolling-summary-recall]" in _extract_last_user_text(messages)


def _plan_rolling_summary_recall_response(messages: list[object]) -> dict[str, object]:
    facts = _extract_exact_facts("\n".join(_iter_message_texts(messages)))
    return {
        "kind": "text",
        "content": _render_rolling_summary_recall_text(facts),
        "chunk_size": 4096,
    }


def _invalid_json_auto_recovery_mode(messages: list[object]) -> bool:
    return _messages_contain_user_text(messages, "[invalid-json-auto-recovery]")


def _plan_invalid_json_auto_recovery_response(
    payload: dict[str, object],
    messages: list[object],
) -> dict[str, object]:
    available_tools = _extract_available_tools(payload)
    if _messages_contain_user_text(
        messages,
        "The previous tool call arguments were not valid JSON.",
    ):
        return {
            "kind": "text",
            "content": "[fake-llm] Recovered after invalid tool args JSON.",
        }

    last_tool_call_id = _extract_last_tool_call_id(messages)
    if last_tool_call_id is None:
        if "read" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] read is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "read",
            "tool_call_id": "call-read-1",
            "arguments": {
                "path": "README.md",
            },
        }

    if last_tool_call_id == "call-read-1":
        return {
            "kind": "invalid_tool_call",
            "tool_name": "read",
            "tool_call_id": "call-read-2",
            "arguments_text": "{bad json",
        }

    return {
        "kind": "text",
        "content": "[fake-llm] Invalid JSON auto-recovery scenario reached an unknown step.",
    }


def _rate_limit_retry_mode(messages: list[object]) -> bool:
    return _messages_contain_user_text(messages, "[rate-limit-once]")


def _hook_read_rewrite_mode(messages: list[object]) -> bool:
    return _messages_contain_user_text(messages, "[hook-read-rewrite]")


def _plan_hook_read_rewrite_response(
    payload: dict[str, object],
    messages: list[object],
) -> dict[str, object]:
    available_tools = _extract_available_tools(payload)
    if "read" not in available_tools:
        return {
            "kind": "text",
            "content": "[fake-llm] read is not available for this role.",
        }
    last_tool_call_id = _extract_last_tool_call_id(messages)
    if last_tool_call_id is None:
        return {
            "kind": "tool_call",
            "tool_name": "read",
            "tool_call_id": "call-hook-read-rewrite-1",
            "arguments": {"path": "missing-from-hook.txt", "offset": 1, "limit": 20},
        }
    return {"kind": "text", "content": "[fake-llm] hook read rewrite completed"}


def _hook_shell_env_mode(messages: list[object]) -> bool:
    return _messages_contain_user_text(messages, "[hook-shell-env]")


def _plan_hook_shell_env_response(
    payload: dict[str, object],
    messages: list[object],
) -> dict[str, object]:
    available_tools = _extract_available_tools(payload)
    if "shell" not in available_tools:
        return {
            "kind": "text",
            "content": "[fake-llm] shell is not available for this role.",
        }
    last_tool_call_id = _extract_last_tool_call_id(messages)
    if last_tool_call_id is None:
        command = (
            "Write-Output $env:RT_HOOK_TEST"
            if sys.platform.startswith("win")
            else f'"{sys.executable}" -c '
            "'"
            'import os; print(os.environ.get("RT_HOOK_TEST", "missing"))'
            "'"
            ""
        )
        return {
            "kind": "tool_call",
            "tool_name": "shell",
            "tool_call_id": "call-hook-shell-env-1",
            "arguments": {
                "command": command,
                "background": False,
            },
        }
    return {"kind": "text", "content": "[fake-llm] hook shell env completed"}


def _hook_deferred_followup_mode(messages: list[object]) -> bool:
    return _messages_contain_user_text(messages, "[hook-deferred-followup]")


def _plan_hook_deferred_followup_response(
    payload: dict[str, object],
    messages: list[object],
) -> dict[str, object]:
    if _messages_contain_user_text(
        messages, "Deferred follow-up instruction from hook"
    ):
        return {
            "kind": "text",
            "content": "[fake-llm] deferred follow-up acknowledged",
        }
    available_tools = _extract_available_tools(payload)
    if "read" not in available_tools:
        return {
            "kind": "text",
            "content": "[fake-llm] read is not available for this role.",
        }
    last_tool_call_id = _extract_last_tool_call_id(messages)
    if last_tool_call_id is None:
        return {
            "kind": "tool_call",
            "tool_name": "read",
            "tool_call_id": "call-hook-deferred-followup-1",
            "arguments": {"path": "README.md", "offset": 1, "limit": 20},
        }
    return {
        "kind": "text",
        "content": "[fake-llm] tool completed without deferred follow-up",
    }


def _plan_rate_limit_retry_response(messages: list[object]) -> dict[str, object]:
    attempt = _next_scenario_attempt(messages, marker="[rate-limit-once]")
    if attempt == 1:
        return {
            "kind": "error_status",
            "status_code": 429,
            "headers": {"retry-after": "1"},
            "body": {
                "error": {
                    "code": "rate_limited",
                    "message": "Provider rate limit reached",
                }
            },
        }
    return {
        "kind": "text",
        "content": "[fake-llm] Recovered after provider rate limit retry.",
    }


def _stream_drop_retry_mode(messages: list[object]) -> bool:
    return _messages_contain_user_text(messages, "[stream-drop-once]")


def _plan_stream_drop_retry_response(messages: list[object]) -> dict[str, object]:
    attempt = _next_scenario_attempt(messages, marker="[stream-drop-once]")
    if attempt == 1:
        return {
            "kind": "text",
            "content": "[fake-llm] partial stream before interruption",
            "drop_after_chunk_count": 1,
        }
    return {
        "kind": "text",
        "content": "[fake-llm] Recovered after dropped stream.",
    }


def _slow_stream_mode(messages: list[object]) -> bool:
    return _messages_contain_user_text(messages, "[slow-stream]")


def _plan_slow_stream_response() -> dict[str, object]:
    return {
        "kind": "text",
        "content": (
            "[fake-llm] Slow stream completed successfully after simulated "
            "latency across multiple chunks."
        ),
        "delay_before_ms": 200,
        "delay_between_chunks_ms": 120,
    }


def _webfetch_approval_validation_mode(messages: list[object]) -> bool:
    return _messages_contain_user_text(messages, "[webfetch-approval-validation]")


def _plan_webfetch_approval_validation_response(
    payload: dict[str, object],
    messages: list[object],
) -> dict[str, object]:
    available_tools = _extract_available_tools(payload)
    if "webfetch" not in available_tools:
        return {
            "kind": "text",
            "content": "[fake-llm] webfetch is not available for this role.",
        }

    last_tool_call_id = _extract_last_tool_call_id(messages)
    if last_tool_call_id is None:
        return {
            "kind": "tool_call",
            "tool_name": "webfetch",
            "tool_call_id": "call-webfetch-1",
            "arguments": {
                "url": "https://localhost/one",
                "format": "text",
            },
        }

    if last_tool_call_id == "call-webfetch-1":
        return {
            "kind": "tool_call",
            "tool_name": "webfetch",
            "tool_call_id": "call-webfetch-2",
            "arguments": {
                "url": "https://localhost/two",
                "format": "text",
            },
        }

    if last_tool_call_id == "call-webfetch-2":
        return {
            "kind": "text",
            "content": (
                "[fake-llm] Webfetch approval validation completed after one "
                "host-scoped approval."
            ),
        }

    return {
        "kind": "text",
        "content": ("[fake-llm] Webfetch approval validation reached an unknown step."),
    }


def build_fake_response_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return "fake-response"
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return "fake-response"

    last_user_text = ""
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            last_user_text = content
            break
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            if parts:
                last_user_text = " ".join(parts)
                break
    if not last_user_text.strip():
        return "fake-response"
    snippet = " ".join(last_user_text.split())[:96]
    return f"[fake-llm] {snippet}"


def _computer_validation_mode(messages: list[object]) -> str | None:
    last_user_text = _extract_last_user_text(messages)
    if "[computer-input-validation]" in last_user_text:
        return "input"
    if "[computer-mouse-validation]" in last_user_text:
        return "mouse"
    if "[computer-real-validation]" in last_user_text:
        return "real"
    if "[computer-validation]" in last_user_text:
        return "basic"
    return None


def _plan_computer_validation_response(
    payload: dict[str, object],
    messages: list[object],
    *,
    mode: str,
) -> dict[str, object] | None:
    available_tools = _extract_available_tools(payload)
    last_tool_call_id = _extract_last_tool_call_id(messages)

    if mode == "input":
        return _plan_input_computer_validation_response(
            available_tools=available_tools,
            last_tool_call_id=last_tool_call_id,
        )

    if mode == "mouse":
        return _plan_mouse_computer_validation_response(
            available_tools=available_tools,
            last_tool_call_id=last_tool_call_id,
        )

    if mode == "real":
        return _plan_real_computer_validation_response(
            available_tools=available_tools,
            last_tool_call_id=last_tool_call_id,
        )

    if last_tool_call_id is None:
        if "capture_screen" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] capture_screen is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "capture_screen",
            "tool_call_id": "call-capture-screen-1",
            "arguments": {},
        }

    if last_tool_call_id == "call-capture-screen-1":
        if "launch_app" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] launch_app is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "launch_app",
            "tool_call_id": "call-launch-app-1",
            "arguments": {"app_name": "Notepad"},
        }

    if last_tool_call_id == "call-launch-app-1":
        return {
            "kind": "text",
            "content": "[fake-llm] Computer validation finished after capture_screen and launch_app.",
        }

    return {
        "kind": "text",
        "content": "[fake-llm] Computer validation reached an unknown step.",
    }


def _plan_input_computer_validation_response(
    *,
    available_tools: set[str],
    last_tool_call_id: str | None,
) -> dict[str, object]:
    if last_tool_call_id is None:
        if "focus_window" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] focus_window is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "focus_window",
            "tool_call_id": "call-input-focus-window-1",
            "arguments": {"window_title": "Agent Teams"},
        }

    if last_tool_call_id == "call-input-focus-window-1":
        if "list_windows" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] list_windows is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "list_windows",
            "tool_call_id": "call-input-list-windows-1",
            "arguments": {},
        }

    if last_tool_call_id == "call-input-list-windows-1":
        if "type_text" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] type_text is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "type_text",
            "tool_call_id": "call-input-type-text-1",
            "arguments": {"text": "hello from fake llm"},
        }

    if last_tool_call_id == "call-input-type-text-1":
        if "hotkey" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] hotkey is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "hotkey",
            "tool_call_id": "call-input-hotkey-1",
            "arguments": {"shortcut": "Ctrl+A"},
        }

    if last_tool_call_id == "call-input-hotkey-1":
        return {
            "kind": "text",
            "content": (
                "[fake-llm] Input computer validation finished after focus_window, "
                "list_windows, type_text, and hotkey."
            ),
        }

    return {
        "kind": "text",
        "content": "[fake-llm] Input computer validation reached an unknown step.",
    }


def _plan_mouse_computer_validation_response(
    *,
    available_tools: set[str],
    last_tool_call_id: str | None,
) -> dict[str, object]:
    if last_tool_call_id is None:
        if "click_at" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] click_at is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "click_at",
            "tool_call_id": "call-mouse-click-1",
            "arguments": {"x": 120, "y": 240},
        }

    if last_tool_call_id == "call-mouse-click-1":
        if "double_click_at" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] double_click_at is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "double_click_at",
            "tool_call_id": "call-mouse-double-click-1",
            "arguments": {"x": 120, "y": 240},
        }

    if last_tool_call_id == "call-mouse-double-click-1":
        if "drag_between" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] drag_between is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "drag_between",
            "tool_call_id": "call-mouse-drag-1",
            "arguments": {
                "start_x": 120,
                "start_y": 240,
                "end_x": 360,
                "end_y": 420,
            },
        }

    if last_tool_call_id == "call-mouse-drag-1":
        if "scroll_view" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] scroll_view is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "scroll_view",
            "tool_call_id": "call-mouse-scroll-1",
            "arguments": {"amount": -3},
        }

    if last_tool_call_id == "call-mouse-scroll-1":
        return {
            "kind": "text",
            "content": (
                "[fake-llm] Mouse computer validation finished after click_at, "
                "double_click_at, drag_between, and scroll_view."
            ),
        }

    return {
        "kind": "text",
        "content": "[fake-llm] Mouse computer validation reached an unknown step.",
    }


def _plan_real_computer_validation_response(
    *,
    available_tools: set[str],
    last_tool_call_id: str | None,
) -> dict[str, object]:
    if last_tool_call_id is None:
        if "launch_app" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] launch_app is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "launch_app",
            "tool_call_id": "call-real-launch-app-1",
            "arguments": {"app_name": "Notepad"},
        }

    if last_tool_call_id == "call-real-launch-app-1":
        if "wait_for_window" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] wait_for_window is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "wait_for_window",
            "tool_call_id": "call-real-wait-window-1",
            "arguments": {"window_title": "Notepad"},
        }

    if last_tool_call_id == "call-real-wait-window-1":
        if "capture_screen" not in available_tools:
            return {
                "kind": "text",
                "content": "[fake-llm] capture_screen is not available for this role.",
            }
        return {
            "kind": "tool_call",
            "tool_name": "capture_screen",
            "tool_call_id": "call-real-capture-screen-1",
            "arguments": {},
        }

    if last_tool_call_id == "call-real-capture-screen-1":
        return {
            "kind": "text",
            "content": (
                "[fake-llm] Real computer validation finished after launch_app, "
                "wait_for_window, and capture_screen."
            ),
        }

    return {
        "kind": "text",
        "content": "[fake-llm] Real computer validation reached an unknown step.",
    }


def _extract_available_tools(payload: dict[str, object]) -> set[str]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return set()
    result: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name.strip():
            result.add(name.strip())
    return result


def _iter_message_texts(messages: list[object]) -> Iterator[str]:
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            yield content
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                yield text


def _extract_last_tool_call_id(messages: list[object]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") != "tool":
            continue
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id.strip():
            return tool_call_id.strip()
    return None


def _extract_last_user_text(messages: list[object]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            if parts:
                return " ".join(parts)
    return ""


def _messages_contain_user_text(messages: list[object], snippet: str) -> bool:
    normalized_snippet = snippet.strip()
    if not normalized_snippet:
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and normalized_snippet in content:
            return True
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and normalized_snippet in text:
                return True
    return False


def _messages_contain_text(messages: list[object], snippet: str) -> bool:
    normalized_snippet = snippet.strip()
    if not normalized_snippet:
        return False
    for text in _iter_message_texts(messages):
        if normalized_snippet in text:
            return True
    return False


def _extract_rolling_summary_phase(text: str) -> int | None:
    match = re.search(r"\[rolling-summary-phase:(\d+)\]", text)
    if match is None:
        return None
    return int(match.group(1))


def _extract_rolling_summary_line_count(text: str) -> int:
    match = re.search(r"line count:\s*(\d+)", text, flags=re.IGNORECASE)
    if match is None:
        return 260
    return max(20, min(int(match.group(1)), 600))


def _extract_rolling_summary_block_count(text: str) -> int:
    match = re.search(r"block count:\s*(\d+)", text, flags=re.IGNORECASE)
    if match is None:
        return 4
    return max(1, min(int(match.group(1)), 6))


def _normalize_fact_text(value: str) -> str:
    return value.replace("**", "").replace("`", "")


def _extract_exact_facts(text: str) -> dict[str, object]:
    normalized = _normalize_fact_text(text)
    global_facts: dict[str, str] = {}
    for label in ("codename", "recovery phrase", "key file", "version tag"):
        match = re.search(
            rf"{re.escape(label)}\s*[:=]\s*([^\n\r|]+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        global_facts[label] = match.group(1).strip()
    phase_anchors: dict[int, str] = {}
    for match in re.finditer(
        r"phase-(\d+)\s+anchor\s*[:=]\s*([^\n\r|]+)",
        normalized,
        flags=re.IGNORECASE,
    ):
        phase_anchors[int(match.group(1))] = match.group(2).strip()
    phase_checksums: dict[int, str] = {}
    for match in re.finditer(
        r"phase-(\d+)\s+checksum\s*[:=]\s*([^\n\r|]+)",
        normalized,
        flags=re.IGNORECASE,
    ):
        phase_checksums[int(match.group(1))] = match.group(2).strip()
    return {
        "global_facts": global_facts,
        "phase_anchors": phase_anchors,
        "phase_checksums": phase_checksums,
    }


def _render_rolling_summary_markdown(facts: dict[str, object]) -> str:
    global_facts = facts.get("global_facts")
    phase_anchors = facts.get("phase_anchors")
    phase_checksums = facts.get("phase_checksums")
    if not isinstance(global_facts, dict):
        global_facts = {}
    if not isinstance(phase_anchors, dict):
        phase_anchors = {}
    if not isinstance(phase_checksums, dict):
        phase_checksums = {}
    phase_numbers = sorted(
        {
            int(phase)
            for phase in phase_anchors.keys() | phase_checksums.keys()
            if isinstance(phase, int)
        }
    )
    if not phase_numbers:
        title = "# Rolling Summary"
    elif len(phase_numbers) == 1:
        title = f"# Rolling Summary - Phase {phase_numbers[0]}"
    else:
        title = f"# Rolling Summary - Phases {phase_numbers[0]}-{phase_numbers[-1]}"
    lines = [title, "", "## Global Facts (preserve exactly)"]
    for label in ("codename", "recovery phrase", "key file", "version tag"):
        value = global_facts.get(label)
        if not isinstance(value, str) or not value:
            continue
        lines.append(f"- **{label}**: {value}")
    for phase in phase_numbers:
        anchor = phase_anchors.get(phase)
        checksum = phase_checksums.get(phase)
        lines.extend(
            [
                "",
                f"## Phase-{phase} Exact Facts",
                f"- **phase-{phase} anchor**: {anchor or '(missing)'}",
                f"- **phase-{phase} checksum**: {checksum or '(missing)'}",
            ]
        )
    lines.extend(
        [
            "",
            "## Status",
            "- Rolling-summary integration test summary rewritten deterministically.",
        ]
    )
    return "\n".join(lines).strip()


def _render_rolling_summary_recall_text(facts: dict[str, object]) -> str:
    global_facts = facts.get("global_facts")
    phase_anchors = facts.get("phase_anchors")
    phase_checksums = facts.get("phase_checksums")
    if not isinstance(global_facts, dict):
        global_facts = {}
    if not isinstance(phase_anchors, dict):
        phase_anchors = {}
    if not isinstance(phase_checksums, dict):
        phase_checksums = {}
    lines: list[str] = []
    for label in ("codename", "recovery phrase", "key file", "version tag"):
        value = global_facts.get(label)
        if isinstance(value, str) and value:
            lines.append(f"- {label}: {value}")
    phase_numbers = sorted(
        {
            int(phase)
            for phase in phase_anchors.keys() | phase_checksums.keys()
            if isinstance(phase, int)
        }
    )
    for phase in phase_numbers:
        anchor = phase_anchors.get(phase)
        checksum = phase_checksums.get(phase)
        if isinstance(anchor, str) and anchor:
            lines.append(f"- phase-{phase} anchor: {anchor}")
        if isinstance(checksum, str) and checksum:
            lines.append(f"- phase-{phase} checksum: {checksum}")
    return "\n".join(lines).strip() or "[fake-llm] no rolling summary facts found"


def _build_rolling_summary_shell_command(
    *,
    phase: int,
    facts: dict[str, object],
    line_count: int,
    block_index: int,
) -> str:
    global_facts = facts.get("global_facts")
    phase_anchors = facts.get("phase_anchors")
    phase_checksums = facts.get("phase_checksums")
    if not isinstance(global_facts, dict):
        global_facts = {}
    if not isinstance(phase_anchors, dict):
        phase_anchors = {}
    if not isinstance(phase_checksums, dict):
        phase_checksums = {}
    codename = str(global_facts.get("codename") or "UNKNOWN-CODENAME")
    recovery_phrase = str(global_facts.get("recovery phrase") or "unknown recovery")
    key_file = str(global_facts.get("key file") or "missing/path")
    version_tag = str(global_facts.get("version tag") or "missing-version")
    anchor = str(phase_anchors.get(phase) or f"phase-{phase}-anchor-missing")
    checksum = str(phase_checksums.get(phase) or f"phase-{phase}-checksum-missing")
    escaped_recovery_phrase = recovery_phrase.replace('"', '\\"')
    escaped_key_file = key_file.replace('"', '\\"')
    escaped_anchor = anchor.replace('"', '\\"')
    escaped_checksum = checksum.replace('"', '\\"')
    escaped_codename = codename.replace('"', '\\"')
    escaped_version_tag = version_tag.replace('"', '\\"')
    block_label = chr(ord("A") + (phase - 1) * 4 + block_index)
    return (
        f"for i in $(seq 1 {line_count}); do "
        f'printf "ROLLING-SUMMARY-PHASE-{phase}-BLOCK-{block_label} | seq=%03d | codename={escaped_codename} | '
        f"recovery phrase={escaped_recovery_phrase} | "
        f"key file={escaped_key_file} | "
        f"version tag={escaped_version_tag} | "
        f"phase-{phase} anchor={escaped_anchor} | "
        f"phase-{phase} checksum={escaped_checksum} | "
        'payload=ROLLINGSUMMARYPAYLOADROLLINGSUMMARYPAYLOADROLLINGSUMMARYPAYLOADROLLINGSUMMARYPAYLOAD1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ\\n" '
        '"$i"; done'
    )


def _next_scenario_attempt(messages: list[object], *, marker: str) -> int:
    key = _scenario_key(messages, marker=marker)
    attempt = _scenario_attempts.get(key, 0) + 1
    _scenario_attempts[key] = attempt
    return attempt


def _scenario_key(messages: list[object], *, marker: str) -> str:
    return f"{marker}:{_extract_last_user_text(messages).strip()}"


def _normalize_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    headers: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(item, str):
            headers[key] = item
    return headers


def _sleep_ms(value: object) -> None:
    milliseconds = _coerce_int(value, default=0)
    if milliseconds <= 0:
        return
    time.sleep(milliseconds / 1000)


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _maybe_abort_stream(
    response_spec: dict[str, object],
    *,
    emitted_chunk_count: int,
) -> None:
    drop_after_chunk_count = response_spec.get("drop_after_chunk_count")
    if (
        isinstance(drop_after_chunk_count, int)
        and drop_after_chunk_count > 0
        and emitted_chunk_count >= drop_after_chunk_count
    ):
        raise RuntimeError("Simulated stream interruption")


def split_text(text: str, *, size: int) -> list[str]:
    if size <= 0:
        return [text]
    chunks: list[str] = []
    idx = 0
    while idx < len(text):
        chunks.append(text[idx : idx + size])
        idx += size
    return chunks if chunks else [""]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=18911)

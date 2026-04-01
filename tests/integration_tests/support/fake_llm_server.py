from __future__ import annotations

from collections.abc import Iterator
import json
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
    chunks = split_text(content, size=12)

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
    if _invalid_json_auto_recovery_mode(messages):
        return _plan_invalid_json_auto_recovery_response(payload, messages)
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
            "arguments": {"app_name": "Calculator"},
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
            "arguments": {"app_name": "Calculator"},
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
            "arguments": {"window_title": "Calculator"},
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

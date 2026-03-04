from __future__ import annotations

from collections.abc import Iterator
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Fake OpenAI-Compatible LLM")

_chat_completions_calls = 0


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> dict[str, int]:
    return {"chat_completions_calls": _chat_completions_calls}


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
    content = build_fake_response_text(payload)
    stream = bool(payload.get("stream"))

    if stream:
        return StreamingResponse(
            stream_chat_completions(model=model, content=content),
            media_type="text/event-stream",
        )

    response = {
        "id": f"chatcmpl-{_chat_completions_calls}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 8,
            "total_tokens": 16,
        },
    }
    return JSONResponse(response)


def stream_chat_completions(*, model: str, content: str) -> Iterator[bytes]:
    created = int(time.time())
    chunks = split_text(content, size=12)
    completion_id = f"chatcmpl-{_chat_completions_calls}"

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

    final_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


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

# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import signal
from types import FrameType
from typing import cast

import pytest

from relay_teams.interfaces.server import app as server_app


def test_register_signal_handlers_logs_and_chains_previous_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assigned_handlers: dict[int, server_app.SignalHandler] = {}
    previous_called_with: list[int] = []
    logged_signals: list[str] = []

    def previous_handler(sig: int, _frame: FrameType | None) -> None:
        previous_called_with.append(sig)

    def fake_getsignal(_sig: int) -> server_app.SignalHandler:
        return previous_handler

    def fake_signal(
        sig: int, handler: server_app.SignalHandler
    ) -> server_app.SignalHandler:
        assigned_handlers[sig] = handler
        return previous_handler

    def fake_log_event(*_args: object, **kwargs: object) -> None:
        payload = kwargs.get("payload")
        if isinstance(payload, dict):
            signal_name = payload.get("signal")
            if isinstance(signal_name, str):
                logged_signals.append(signal_name)

    monkeypatch.setattr(server_app.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(server_app.signal, "signal", fake_signal)
    monkeypatch.setattr(server_app, "log_event", fake_log_event)

    server_app._register_signal_handlers()

    assigned_handlers[signal.SIGINT](signal.SIGINT, None)

    assert previous_called_with == [signal.SIGINT]
    assert logged_signals == ["SIGINT"]


def test_register_signal_handlers_raises_keyboard_interrupt_on_default_sigint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assigned_handlers: dict[int, server_app.SignalHandler] = {}

    def fake_getsignal(_sig: int) -> int:
        return signal.SIG_DFL

    def fake_signal(sig: int, handler: server_app.SignalHandler) -> int:
        assigned_handlers[sig] = handler
        return signal.SIG_DFL

    def fake_log_event(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(server_app.signal, "getsignal", fake_getsignal)
    monkeypatch.setattr(server_app.signal, "signal", fake_signal)
    monkeypatch.setattr(server_app, "log_event", fake_log_event)

    server_app._register_signal_handlers()

    with pytest.raises(KeyboardInterrupt):
        assigned_handlers[signal.SIGINT](signal.SIGINT, None)


def test_resolve_request_log_level_suppresses_noisy_success_paths() -> None:
    assert (
        server_app._resolve_request_log_level(
            path="/api/system/health",
            status_code=200,
        )
        is None
    )
    assert (
        server_app._resolve_request_log_level(
            path="/api/sessions/session-1/recovery",
            status_code=200,
        )
        is None
    )
    assert (
        server_app._resolve_request_log_level(
            path="/api/sessions/session-1/runs/run-1/token-usage",
            status_code=200,
        )
        is None
    )
    assert (
        server_app._resolve_request_log_level(
            path="/.well-known/appspecific/com.chrome.devtools.json",
            status_code=404,
        )
        is None
    )


def test_resolve_request_log_level_downgrades_success_and_escalates_failures() -> None:
    assert (
        server_app._resolve_request_log_level(
            path="/api/runs",
            status_code=200,
        )
        == logging.DEBUG
    )
    assert (
        server_app._resolve_request_log_level(
            path="/api/runs",
            status_code=404,
        )
        == logging.WARNING
    )
    assert (
        server_app._resolve_request_log_level(
            path="/api/runs",
            status_code=500,
        )
        == logging.ERROR
    )


def test_should_ignore_asyncio_exception_for_windows_proactor_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app.sys, "platform", "win32")

    assert (
        server_app._should_ignore_asyncio_exception(
            {
                "message": (
                    "Exception in callback "
                    "_ProactorBasePipeTransport._call_connection_lost()"
                ),
                "exception": ConnectionResetError(
                    "[WinError 10054] remote host forcibly closed the connection"
                ),
            }
        )
        is True
    )


def test_should_not_ignore_asyncio_exception_for_non_matching_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app.sys, "platform", "win32")

    assert (
        server_app._should_ignore_asyncio_exception(
            {
                "message": "Exception in callback something_else()",
                "exception": ConnectionResetError(
                    "[WinError 10054] remote host forcibly closed the connection"
                ),
            }
        )
        is False
    )


def test_configure_asyncio_exception_handler_ignores_only_benign_windows_disconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app.sys, "platform", "win32")

    delegated_contexts: list[server_app.AsyncioExceptionContext] = []

    def previous_handler(
        _loop: asyncio.AbstractEventLoop,
        context: server_app.AsyncioExceptionContext,
    ) -> None:
        delegated_contexts.append(context)

    class FakeLoop:
        def __init__(self) -> None:
            self.handler: server_app.AsyncioExceptionHandler | None = None

        def get_exception_handler(
            self,
        ) -> server_app.AsyncioExceptionHandler | None:
            return previous_handler

        def set_exception_handler(
            self, handler: server_app.AsyncioExceptionHandler | None
        ) -> None:
            self.handler = handler

        def default_exception_handler(
            self, _context: server_app.AsyncioExceptionContext
        ) -> None:
            raise AssertionError("default handler should not be called")

    loop = FakeLoop()
    monkeypatch.setattr(
        server_app.asyncio,
        "get_running_loop",
        lambda: cast(asyncio.AbstractEventLoop, loop),
    )

    server_app._configure_asyncio_exception_handler()

    assert loop.handler is not None
    current_loop = cast(asyncio.AbstractEventLoop, loop)

    ignored_context: server_app.AsyncioExceptionContext = {
        "message": (
            "Exception in callback _ProactorBasePipeTransport._call_connection_lost()"
        ),
        "exception": ConnectionResetError(
            "[WinError 10054] remote host forcibly closed the connection"
        ),
    }
    loop.handler(current_loop, ignored_context)
    assert delegated_contexts == []

    forwarded_context: server_app.AsyncioExceptionContext = {
        "message": "Exception in callback another_callback()",
        "exception": RuntimeError("boom"),
    }
    loop.handler(current_loop, forwarded_context)
    assert delegated_contexts == [forwarded_context]

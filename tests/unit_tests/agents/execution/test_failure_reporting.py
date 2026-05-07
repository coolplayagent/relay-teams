# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic_ai.exceptions import ModelHTTPError

from relay_teams.agents.execution.failure_reporting import FailureHandlingService


class _BodyError(Exception):
    def __init__(self, message: str, *, body: object) -> None:
        super().__init__(message)
        self.body = body


def test_build_model_api_error_message_preserves_proxy_block_body() -> None:
    body = (
        "<!DOCTYPE html>\n"
        '<html><head><meta name="keywords" content="SWG,Proxy,NetentSec" />'
        "<title>HIS Proxy</title></head><body>blocked</body></html>"
    )
    service = object.__new__(FailureHandlingService)

    message = FailureHandlingService.build_model_api_error_message(
        service,
        ModelHTTPError(
            status_code=403,
            model_name="deepseek-v4-flash",
            body=body,
        ),
    )

    assert "blocked by an enterprise proxy" in message
    assert "status_code: 403" in message
    assert "model_name: deepseek-v4-flash" in message
    assert body in message


def test_enterprise_proxy_block_detection_scans_bytes_body() -> None:
    chain = (
        _BodyError(
            "Forbidden",
            body=b'<html><head><meta name="keywords" content="SWG,Proxy" />',
        ),
    )

    assert FailureHandlingService.is_enterprise_proxy_block_failure(chain) is True


def test_enterprise_proxy_block_detection_scans_object_body() -> None:
    chain = (
        _BodyError(
            "Forbidden",
            body={"title": "ProxyControlWarn", "message": "blocked"},
        ),
    )

    assert FailureHandlingService.is_enterprise_proxy_block_failure(chain) is True


def test_enterprise_proxy_block_detection_does_not_match_this_proxy_text() -> None:
    chain = (
        _BodyError(
            "Temporary failure",
            body="this proxy path returned a transient upstream error",
        ),
    )

    assert FailureHandlingService.is_enterprise_proxy_block_failure(chain) is False


def test_raw_error_body_text_serializes_bytes_and_objects() -> None:
    assert FailureHandlingService.raw_error_body_text(b"blocked") == "blocked"
    assert (
        FailureHandlingService.raw_error_body_text({"message": "blocked"})
        == '{"message": "blocked"}'
    )

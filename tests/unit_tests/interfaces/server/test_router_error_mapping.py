from __future__ import annotations

from fastapi import HTTPException
import pytest

from relay_teams.interfaces.server.router_error_mapping import http_exception_for


class _CustomError(Exception):
    pass


def test_http_exception_for_key_error_uses_override_detail() -> None:
    exc = http_exception_for(KeyError("missing"), key_error_detail="Session not found")

    assert isinstance(exc, HTTPException)
    assert exc.status_code == 404
    assert exc.detail == "Session not found"


def test_http_exception_for_uses_configured_mapping() -> None:
    exc = http_exception_for(ValueError("bad input"), mappings=((ValueError, 422),))

    assert exc.status_code == 422
    assert exc.detail == "bad input"


def test_http_exception_for_rejects_unmapped_exception() -> None:
    with pytest.raises(TypeError, match="Unsupported exception mapping"):
        http_exception_for(_CustomError("boom"))

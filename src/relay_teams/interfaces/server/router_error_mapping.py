from __future__ import annotations

from fastapi import HTTPException


def http_exception_for(
    exc: Exception,
    *,
    key_error_detail: str | None = None,
    mappings: tuple[tuple[type[Exception], int], ...] = (),
) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=key_error_detail or str(exc))
    for exc_type, status_code in mappings:
        if isinstance(exc, exc_type):
            return HTTPException(status_code=status_code, detail=str(exc))
    raise TypeError(f"Unsupported exception mapping: {type(exc).__name__}")

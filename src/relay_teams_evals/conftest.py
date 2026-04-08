from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def backend_url() -> str:
    return os.environ.get("EVAL_BACKEND_URL", "http://127.0.0.1:8000")

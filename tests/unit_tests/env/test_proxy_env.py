# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from agent_teams.env import apply_proxy_env_to_process_env, extract_proxy_env_vars


def test_extract_proxy_env_vars_normalizes_upper_and_lowercase_keys() -> None:
    proxy_env = extract_proxy_env_vars(
        {
            "HTTP_PROXY": "http://proxy.example:8080",
            "no_proxy": "localhost,127.0.0.1",
        }
    )

    assert proxy_env == {
        "HTTP_PROXY": "http://proxy.example:8080",
        "http_proxy": "http://proxy.example:8080",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }


def test_extract_proxy_env_vars_prefers_uppercase_when_both_exist() -> None:
    proxy_env = extract_proxy_env_vars(
        {
            "HTTP_PROXY": "http://upper.example:8080",
            "http_proxy": "http://lower.example:8080",
        }
    )

    assert proxy_env["HTTP_PROXY"] == "http://upper.example:8080"
    assert proxy_env["http_proxy"] == "http://upper.example:8080"


def test_apply_proxy_env_to_process_env_updates_os_environ(monkeypatch) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    applied = apply_proxy_env_to_process_env(
        {
            "HTTP_PROXY": "http://proxy.example:8080",
            "NO_PROXY": "localhost,127.0.0.1",
        }
    )

    assert applied["HTTP_PROXY"] == "http://proxy.example:8080"
    assert os.environ["HTTP_PROXY"] == "http://proxy.example:8080"
    assert os.environ["http_proxy"] == "http://proxy.example:8080"
    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1"
    assert os.environ["no_proxy"] == "localhost,127.0.0.1"

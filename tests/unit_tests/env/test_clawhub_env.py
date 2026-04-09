# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.env.clawhub_env import (
    CLAWHUB_REGISTRY_ENV_KEY,
    CLAWHUB_SITE_ENV_KEY,
    CLAWHUB_TOKEN_ENV_KEY,
    DEFAULT_CLAWHUB_CN_REGISTRY,
    DEFAULT_CLAWHUB_CN_SITE,
    build_clawhub_cli_env,
    build_clawhub_subprocess_env,
)
from relay_teams.env.proxy_env import ProxyEnvConfig


def test_build_clawhub_cli_env_uses_china_mirror_for_china_locale() -> None:
    env = build_clawhub_cli_env(
        "ch_secret",
        env_values={"LANG": "zh_CN.UTF-8"},
    )

    assert env[CLAWHUB_TOKEN_ENV_KEY] == "ch_secret"
    assert env[CLAWHUB_SITE_ENV_KEY] == DEFAULT_CLAWHUB_CN_SITE
    assert env[CLAWHUB_REGISTRY_ENV_KEY] == DEFAULT_CLAWHUB_CN_REGISTRY
    assert env["CLAWDHUB_SITE"] == DEFAULT_CLAWHUB_CN_SITE
    assert env["CLAWDHUB_REGISTRY"] == DEFAULT_CLAWHUB_CN_REGISTRY


def test_build_clawhub_cli_env_respects_explicit_site_override() -> None:
    env = build_clawhub_cli_env(
        None,
        env_values={
            CLAWHUB_SITE_ENV_KEY: "https://example.com",
            CLAWHUB_REGISTRY_ENV_KEY: "https://registry.example.com",
        },
    )

    assert env[CLAWHUB_SITE_ENV_KEY] == "https://example.com"
    assert env[CLAWHUB_REGISTRY_ENV_KEY] == "https://registry.example.com"
    assert env["CLAWDHUB_SITE"] == "https://example.com"
    assert env["CLAWDHUB_REGISTRY"] == "https://registry.example.com"


def test_build_clawhub_cli_env_omits_site_for_non_china_locale() -> None:
    env = build_clawhub_cli_env(
        None,
        env_values={"LANG": "en_US.UTF-8"},
    )

    assert env == {}


def test_build_clawhub_subprocess_env_includes_hydrated_proxy_values(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "relay_teams.env.clawhub_env.load_proxy_env_config",
        lambda **_kwargs: ProxyEnvConfig(
            http_proxy="http://alice:secret@proxy.example:8080",
            https_proxy=None,
            all_proxy=None,
            no_proxy="localhost",
            ssl_verify=None,
        ),
    )

    env = build_clawhub_subprocess_env(
        "ch_secret",
        base_env={"LANG": "zh_CN.UTF-8", "PATH": "/usr/bin"},
    )

    assert env[CLAWHUB_TOKEN_ENV_KEY] == "ch_secret"
    assert env["HTTP_PROXY"] == "http://alice:secret@proxy.example:8080"
    assert env["NO_PROXY"] == "localhost"

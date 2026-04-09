# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.env.clawhub_env import (
    CLAWHUB_SITE_ENV_KEY,
    CLAWHUB_TOKEN_ENV_KEY,
    DEFAULT_CLAWHUB_CN_SITE,
    build_clawhub_cli_env,
)


def test_build_clawhub_cli_env_uses_china_mirror_for_china_locale() -> None:
    env = build_clawhub_cli_env(
        "ch_secret",
        env_values={"LANG": "zh_CN.UTF-8"},
    )

    assert env[CLAWHUB_TOKEN_ENV_KEY] == "ch_secret"
    assert env[CLAWHUB_SITE_ENV_KEY] == DEFAULT_CLAWHUB_CN_SITE
    assert env["CLAWDHUB_SITE"] == DEFAULT_CLAWHUB_CN_SITE


def test_build_clawhub_cli_env_respects_explicit_site_override() -> None:
    env = build_clawhub_cli_env(
        None,
        env_values={CLAWHUB_SITE_ENV_KEY: "https://example.com"},
    )

    assert env[CLAWHUB_SITE_ENV_KEY] == "https://example.com"
    assert env["CLAWDHUB_SITE"] == "https://example.com"


def test_build_clawhub_cli_env_omits_site_for_non_china_locale() -> None:
    env = build_clawhub_cli_env(
        None,
        env_values={"LANG": "en_US.UTF-8"},
    )

    assert env == {}

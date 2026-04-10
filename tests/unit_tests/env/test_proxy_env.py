# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

from relay_teams.env import (
    ProxyEnvInput,
    apply_proxy_env_to_process_env,
    build_subprocess_env,
    extract_proxy_env_vars,
    load_proxy_env_config,
    parse_no_proxy_rules,
    mask_proxy_url,
    proxy_applies_to_url,
    resolve_proxy_env_config,
)
from relay_teams.env.proxy_env import resolve_ssl_verify


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


def test_apply_proxy_env_to_process_env_clears_stale_proxy_keys(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://stale.example:8080")
    monkeypatch.setenv("http_proxy", "http://stale.example:8080")
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setenv("no_proxy", "localhost")

    applied = apply_proxy_env_to_process_env({})

    assert applied == {}
    assert "HTTP_PROXY" not in os.environ
    assert "http_proxy" not in os.environ
    assert "NO_PROXY" not in os.environ
    assert "no_proxy" not in os.environ


def test_build_subprocess_env_uses_current_proxy_values(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    subprocess_env = build_subprocess_env(
        base_env={"PATH": "bin", "HTTP_PROXY": "http://stale.example:9999"},
        extra_env={"CUSTOM": "1"},
    )

    assert subprocess_env["PATH"] == "bin"
    assert subprocess_env["CUSTOM"] == "1"
    assert subprocess_env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert subprocess_env["NO_PROXY"] == "localhost,127.0.0.1"


def test_resolve_ssl_verify_defaults_to_disabled() -> None:
    assert resolve_ssl_verify() is False


def test_resolve_ssl_verify_prefers_explicit_override() -> None:
    assert (
        resolve_ssl_verify(
            proxy_config=resolve_proxy_env_config({"SSL_VERIFY": "false"}),
            explicit_ssl_verify=True,
        )
        is True
    )


def test_proxy_applies_to_url_respects_no_proxy() -> None:
    proxy_config = resolve_proxy_env_config(
        {
            "HTTP_PROXY": "http://proxy.example:8080",
            "NO_PROXY": "localhost,example.com",
        }
    )

    assert proxy_applies_to_url("https://api.other.test", proxy_config) is True
    assert proxy_applies_to_url("https://example.com/path", proxy_config) is False


def test_proxy_applies_to_url_supports_semicolon_wildcards_and_local_hosts() -> None:
    proxy_config = resolve_proxy_env_config(
        {
            "HTTPS_PROXY": "http://proxy.example:8443",
            "NO_PROXY": "localhost;127.*;192.168.*;10.*;172.16.*;<local>",
        }
    )

    assert proxy_applies_to_url("https://service.example.com", proxy_config) is True
    assert proxy_applies_to_url("https://127.0.0.1:8080/health", proxy_config) is False
    assert proxy_applies_to_url("https://192.168.31.10/app", proxy_config) is False
    assert proxy_applies_to_url("https://10.0.0.20/app", proxy_config) is False
    assert proxy_applies_to_url("https://172.16.8.2/app", proxy_config) is False
    assert proxy_applies_to_url("https://intranet/app", proxy_config) is False


def test_parse_no_proxy_rules_normalizes_semicolon_separated_entries() -> None:
    rules = parse_no_proxy_rules("localhost;127.*;192.168.*;<local>")

    assert [(rule.kind, rule.value) for rule in rules] == [
        ("domain", "localhost"),
        ("wildcard", "127.*"),
        ("wildcard", "192.168.*"),
        ("local", None),
    ]


def test_extract_proxy_env_vars_converts_semicolons_to_commas_for_no_proxy() -> None:
    proxy_env = extract_proxy_env_vars(
        {
            "HTTP_PROXY": "http://proxy.example:8080",
            "NO_PROXY": "localhost;127.*;<local>",
        }
    )

    assert proxy_env["NO_PROXY"] == "localhost,127.*,<local>"
    assert proxy_env["no_proxy"] == "localhost,127.*,<local>"


def test_extract_proxy_env_vars_strips_empty_no_proxy_tokens() -> None:
    proxy_env = extract_proxy_env_vars(
        {
            "HTTP_PROXY": "http://proxy.example:8080",
            "NO_PROXY": "a,,b; ;c",
        }
    )

    assert proxy_env["NO_PROXY"] == "a,b,c"


def test_extract_proxy_env_vars_omits_no_proxy_when_only_separators() -> None:
    proxy_env = extract_proxy_env_vars(
        {
            "HTTP_PROXY": "http://proxy.example:8080",
            "NO_PROXY": " ; , ",
        }
    )

    assert "NO_PROXY" not in proxy_env
    assert "no_proxy" not in proxy_env


def test_build_subprocess_env_keeps_username_only_proxy_url_unchanged(
    monkeypatch,
) -> None:
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://alice@proxy.example:8443")
    monkeypatch.setenv("ALL_PROXY", "http://alice@proxy.example:1080")

    subprocess_env = build_subprocess_env()

    assert subprocess_env["HTTPS_PROXY"] == "http://alice@proxy.example:8443"
    assert subprocess_env["https_proxy"] == "http://alice@proxy.example:8443"
    assert subprocess_env["ALL_PROXY"] == "http://alice@proxy.example:1080"
    assert subprocess_env["all_proxy"] == "http://alice@proxy.example:1080"


def test_build_subprocess_env_leaves_url_without_username_unchanged(
    monkeypatch,
) -> None:
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8443")

    subprocess_env = build_subprocess_env()

    assert subprocess_env["HTTPS_PROXY"] == "http://proxy.example:8443"


def test_build_subprocess_env_keeps_inline_password_when_already_present(
    monkeypatch,
) -> None:
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://alice:inline-pw@proxy.example:8443")

    subprocess_env = build_subprocess_env()

    assert subprocess_env["HTTPS_PROXY"] == "http://alice:inline-pw@proxy.example:8443"


def test_build_subprocess_env_exports_comma_separated_no_proxy(monkeypatch) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "localhost;127.*;example.com")

    subprocess_env = build_subprocess_env()

    assert subprocess_env["NO_PROXY"] == "localhost,127.*,example.com"
    assert subprocess_env["no_proxy"] == "localhost,127.*,example.com"


def test_proxy_env_input_splits_and_rebuilds_shared_proxy_credentials() -> None:
    config = resolve_proxy_env_config(
        {
            "HTTP_PROXY": "http://alice:secret@proxy.example:8080",
            "HTTPS_PROXY": "http://alice:secret@secure.example:8443",
        }
    )

    proxy_form = ProxyEnvInput.from_config(config)
    rebuilt_config = proxy_form.to_config()

    assert proxy_form == ProxyEnvInput(
        http_proxy="http://proxy.example:8080",
        https_proxy="http://secure.example:8443",
        all_proxy=None,
        no_proxy=None,
        proxy_username="alice",
        proxy_password="secret",
    )
    assert rebuilt_config == config


def test_proxy_env_input_encodes_special_characters_in_credentials() -> None:
    proxy_config = ProxyEnvInput(
        https_proxy="http://proxy.example:8443",
        proxy_username="alice@corp",
        proxy_password="sec!ret@value",
    ).to_config()

    assert (
        proxy_config.https_proxy
        == "http://alice%40corp:sec%21ret%40value@proxy.example:8443"
    )
    assert ProxyEnvInput.from_config(proxy_config) == ProxyEnvInput(
        http_proxy=None,
        https_proxy="http://proxy.example:8443",
        all_proxy=None,
        no_proxy=None,
        proxy_username="alice@corp",
        proxy_password="sec!ret@value",
    )


def test_load_proxy_env_config_applies_password_from_secret_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent_teams"
    config_dir.mkdir()
    env_file = config_dir / ".env"
    env_file.write_text(
        "HTTPS_PROXY=http://alice@proxy.example:8443\n", encoding="utf-8"
    )

    class _FakeSecretStore:
        def get_password(self, _config_dir) -> str | None:
            return "secret"

    monkeypatch.setattr(
        "relay_teams.env.proxy_env.get_proxy_secret_store",
        lambda: _FakeSecretStore(),
    )

    proxy_config = load_proxy_env_config(
        extra_env_files=(env_file,), include_process_env=False
    )

    assert proxy_config.https_proxy == "http://alice:secret@proxy.example:8443"


def test_load_proxy_env_config_keeps_password_from_env_when_user_forces_it(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent_teams"
    config_dir.mkdir()
    env_file = config_dir / ".env"
    env_file.write_text(
        "HTTPS_PROXY=http://alice:from-env@proxy.example:8443\n",
        encoding="utf-8",
    )

    class _FakeSecretStore:
        def get_password(self, _config_dir) -> str | None:
            return "from-keyring"

    monkeypatch.setattr(
        "relay_teams.env.proxy_env.get_proxy_secret_store",
        lambda: _FakeSecretStore(),
    )

    proxy_config = load_proxy_env_config(
        extra_env_files=(env_file,), include_process_env=False
    )

    assert proxy_config.https_proxy == "http://alice:from-env@proxy.example:8443"


def test_mask_proxy_url_hides_embedded_credentials() -> None:
    masked = mask_proxy_url("http://user:pass@proxy.example:8080")

    assert masked == "http://***:***@proxy.example:8080"


def test_mask_proxy_url_hides_embedded_credentials_without_scheme() -> None:
    masked = mask_proxy_url("user:pass@proxy.example:8080")

    assert masked == "***:***@proxy.example:8080"

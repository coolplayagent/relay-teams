# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import fnmatch
import ipaddress
import os
from pathlib import Path
from typing import Literal
from urllib.parse import SplitResult, quote, unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

from relay_teams.env.proxy_secret_store import get_proxy_secret_store
from relay_teams.env.runtime_env import get_project_env_file_path, load_merged_env_vars

_PROXY_ENV_KEY_GROUPS: tuple[tuple[str, str], ...] = (
    ("HTTP_PROXY", "http_proxy"),
    ("HTTPS_PROXY", "https_proxy"),
    ("ALL_PROXY", "all_proxy"),
    ("NO_PROXY", "no_proxy"),
)
_NODE_PROXY_ENV_KEY_GROUPS: tuple[tuple[str, str], ...] = (
    ("NPM_CONFIG_PROXY", "npm_config_proxy"),
    ("NPM_CONFIG_HTTPS_PROXY", "npm_config_https_proxy"),
    ("NPM_CONFIG_NOPROXY", "npm_config_noproxy"),
    ("NPM_CONFIG_STRICT_SSL", "npm_config_strict_ssl"),
)
_PROXY_ENV_KEYS: tuple[str, ...] = tuple(
    key for key_group in _PROXY_ENV_KEY_GROUPS for key in key_group
)
_SSL_VERIFY_ENV_KEYS: tuple[str, ...] = ("SSL_VERIFY",)
_PROCESS_ENV_KEYS: tuple[str, ...] = _PROXY_ENV_KEYS + _SSL_VERIFY_ENV_KEYS
_NODE_PROXY_ENV_KEYS: tuple[str, ...] = (
    "NODE_USE_ENV_PROXY",
    "NODE_TLS_REJECT_UNAUTHORIZED",
) + tuple(key for key_group in _NODE_PROXY_ENV_KEY_GROUPS for key in key_group)
_RUNTIME_ENV_KEYS: tuple[str, ...] = _PROCESS_ENV_KEYS + _NODE_PROXY_ENV_KEYS
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_DEFAULT_SSL_VERIFY = False


class ProxyEnvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    http_proxy: str | None = None
    https_proxy: str | None = None
    all_proxy: str | None = None
    no_proxy: str | None = None
    ssl_verify: bool | None = None

    def normalized_env(self) -> dict[str, str]:
        env_values: dict[str, str] = {}
        if self.http_proxy:
            env_values["HTTP_PROXY"] = self.http_proxy
            env_values["http_proxy"] = self.http_proxy
        if self.https_proxy:
            env_values["HTTPS_PROXY"] = self.https_proxy
            env_values["https_proxy"] = self.https_proxy
        if self.all_proxy:
            env_values["ALL_PROXY"] = self.all_proxy
            env_values["all_proxy"] = self.all_proxy
        if self.no_proxy:
            exported_no_proxy = _normalize_no_proxy_for_export(self.no_proxy)
            if exported_no_proxy:
                env_values["NO_PROXY"] = exported_no_proxy
                env_values["no_proxy"] = exported_no_proxy
        if self.ssl_verify is not None:
            env_values["SSL_VERIFY"] = "true" if self.ssl_verify else "false"
        return env_values

    @property
    def has_proxy(self) -> bool:
        return any((self.http_proxy, self.https_proxy, self.all_proxy))


class ProxyEnvInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    http_proxy: str | None = None
    https_proxy: str | None = None
    all_proxy: str | None = None
    no_proxy: str | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    ssl_verify: bool | None = None

    @classmethod
    def from_config(cls, config: ProxyEnvConfig) -> ProxyEnvInput:
        proxy_urls = (
            _extract_proxy_url_components(config.http_proxy),
            _extract_proxy_url_components(config.https_proxy),
            _extract_proxy_url_components(config.all_proxy),
        )
        shared_auth = _resolve_shared_proxy_auth(proxy_urls)
        return cls(
            http_proxy=_select_proxy_input_value(
                original_value=config.http_proxy,
                extracted_value=proxy_urls[0].sanitized_url,
                shared_auth=shared_auth,
                url_auth=proxy_urls[0].auth,
            ),
            https_proxy=_select_proxy_input_value(
                original_value=config.https_proxy,
                extracted_value=proxy_urls[1].sanitized_url,
                shared_auth=shared_auth,
                url_auth=proxy_urls[1].auth,
            ),
            all_proxy=_select_proxy_input_value(
                original_value=config.all_proxy,
                extracted_value=proxy_urls[2].sanitized_url,
                shared_auth=shared_auth,
                url_auth=proxy_urls[2].auth,
            ),
            no_proxy=config.no_proxy,
            proxy_username=shared_auth.username,
            proxy_password=shared_auth.password,
            ssl_verify=config.ssl_verify,
        )

    def to_config(self, *, ssl_verify: bool | None = None) -> ProxyEnvConfig:
        normalized_username = _normalize_proxy_value(self.proxy_username)
        normalized_password = _normalize_proxy_value(self.proxy_password)
        return ProxyEnvConfig(
            http_proxy=_compose_proxy_url_with_auth(
                self.http_proxy,
                username=normalized_username,
                password=normalized_password,
            ),
            https_proxy=_compose_proxy_url_with_auth(
                self.https_proxy,
                username=normalized_username,
                password=normalized_password,
            ),
            all_proxy=_compose_proxy_url_with_auth(
                self.all_proxy,
                username=normalized_username,
                password=normalized_password,
            ),
            no_proxy=_normalize_proxy_value(self.no_proxy),
            ssl_verify=self.ssl_verify if ssl_verify is None else ssl_verify,
        )


class ProxyAuthParts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    username: str | None = None
    password: str | None = None


class ProxyUrlComponents(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sanitized_url: str | None = None
    auth: ProxyAuthParts = ProxyAuthParts()


class NoProxyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["any", "local", "wildcard", "ip", "domain"]
    value: str | None = None

    def matches(self, host: str) -> bool:
        normalized_host = host.strip("[]").lower()
        if self.kind == "any":
            return True
        if self.kind == "local":
            return "." not in normalized_host and not _is_ip_literal(normalized_host)
        if self.value is None:
            return False
        if self.kind == "wildcard":
            return fnmatch.fnmatchcase(normalized_host, self.value)
        if self.kind == "ip":
            return normalized_host == self.value
        if self.kind == "domain":
            if normalized_host == self.value:
                return True
            if _is_ip_literal(normalized_host):
                return False
            return normalized_host.endswith(f".{self.value}")
        return False


def extract_proxy_env_vars(env_values: Mapping[str, str]) -> dict[str, str]:
    return _build_runtime_proxy_env(resolve_proxy_env_config(env_values))


def apply_proxy_env_to_process_env(env_values: Mapping[str, str]) -> dict[str, str]:
    return sync_proxy_env_to_process_env(resolve_proxy_env_config(env_values))


def resolve_proxy_env_config(env_values: Mapping[str, str]) -> ProxyEnvConfig:
    return ProxyEnvConfig(
        http_proxy=_resolve_env_value(env_values, "HTTP_PROXY", "http_proxy"),
        https_proxy=_resolve_env_value(env_values, "HTTPS_PROXY", "https_proxy"),
        all_proxy=_resolve_env_value(env_values, "ALL_PROXY", "all_proxy"),
        no_proxy=_resolve_env_value(env_values, "NO_PROXY", "no_proxy"),
        ssl_verify=_read_ssl_verify_env(env_values),
    )


def load_proxy_env_config(
    *,
    extra_env_files: tuple[Path, ...] = (),
    include_process_env: bool = True,
    user_home_dir: Path | None = None,
) -> ProxyEnvConfig:
    merged_env = load_merged_env_vars(
        extra_env_files=extra_env_files,
        include_process_env=include_process_env,
        user_home_dir=user_home_dir,
    )
    return hydrate_proxy_config_with_saved_password(
        resolve_proxy_env_config(merged_env),
        config_dir=_resolve_proxy_secret_config_dir(extra_env_files),
    )


def _build_runtime_proxy_env(proxy_config: ProxyEnvConfig) -> dict[str, str]:
    env_values = proxy_config.normalized_env()
    env_values.update(_build_node_runtime_proxy_env(proxy_config))
    return env_values


def sync_proxy_env_to_process_env(proxy_config: ProxyEnvConfig) -> dict[str, str]:
    normalized_env = _build_runtime_proxy_env(proxy_config)
    for key in _RUNTIME_ENV_KEYS:
        if key in normalized_env:
            os.environ[key] = normalized_env[key]
            continue
        os.environ.pop(key, None)
    return normalized_env


def build_subprocess_env(
    *,
    base_env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    resolved_env = dict(os.environ if base_env is None else base_env)
    for key in _RUNTIME_ENV_KEYS:
        resolved_env.pop(key, None)
    resolved_env.update(extract_proxy_env_vars(os.environ))
    if extra_env is not None:
        resolved_env.update(extra_env)
    return resolved_env


def resolve_ssl_verify(
    *,
    proxy_config: ProxyEnvConfig | None = None,
    explicit_ssl_verify: bool | None = None,
) -> bool:
    if explicit_ssl_verify is not None:
        return explicit_ssl_verify
    if proxy_config is not None and proxy_config.ssl_verify is not None:
        return proxy_config.ssl_verify
    return _DEFAULT_SSL_VERIFY


def mask_proxy_url(value: str | None) -> str | None:
    if value is None:
        return value

    normalized_value = value.strip()
    if not normalized_value:
        return None

    parsed, had_explicit_scheme = _split_proxy_url(normalized_value)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""

    masked_userinfo = ""
    if parsed.username is not None and parsed.password is not None:
        masked_userinfo = "***:***@"
    elif parsed.username is not None:
        masked_userinfo = "***@"

    masked = SplitResult(
        scheme=parsed.scheme,
        netloc=f"{masked_userinfo}{host}{port}",
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return _serialize_proxy_split(masked, had_explicit_scheme=had_explicit_scheme)


def proxy_applies_to_url(url: str, proxy_config: ProxyEnvConfig) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").strip().lower()
    scheme = parsed.scheme.strip().lower()
    if not host or scheme not in {"http", "https"}:
        return False

    if host_matches_no_proxy(host, proxy_config.no_proxy):
        return False

    if scheme == "http":
        return bool(proxy_config.http_proxy or proxy_config.all_proxy)
    return bool(
        proxy_config.https_proxy or proxy_config.http_proxy or proxy_config.all_proxy
    )


def _resolve_env_value(
    env_values: Mapping[str, str],
    uppercase_key: str,
    lowercase_key: str,
) -> str | None:
    uppercase_value = env_values.get(uppercase_key)
    if uppercase_value:
        return uppercase_value

    lowercase_value = env_values.get(lowercase_key)
    if lowercase_value:
        return lowercase_value

    return None


def _normalize_proxy_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _normalize_no_proxy_for_export(value: str | None) -> str | None:
    normalized = _normalize_proxy_value(value)
    if normalized is None:
        return None
    tokens: list[str] = []
    for raw_candidate in normalized.replace(";", ",").split(","):
        candidate = raw_candidate.strip()
        if candidate:
            tokens.append(candidate)
    if not tokens:
        return None
    return ",".join(tokens)


def _build_node_runtime_proxy_env(proxy_config: ProxyEnvConfig) -> dict[str, str]:
    env_values: dict[str, str] = {}
    if not proxy_config.has_proxy:
        return env_values

    env_values["NODE_USE_ENV_PROXY"] = "1"

    http_proxy = proxy_config.http_proxy or proxy_config.all_proxy
    if http_proxy is not None:
        _set_env_pair(
            env_values,
            uppercase_key="NPM_CONFIG_PROXY",
            lowercase_key="npm_config_proxy",
            value=http_proxy,
        )

    https_proxy = (
        proxy_config.https_proxy or proxy_config.http_proxy or proxy_config.all_proxy
    )
    if https_proxy is not None:
        _set_env_pair(
            env_values,
            uppercase_key="NPM_CONFIG_HTTPS_PROXY",
            lowercase_key="npm_config_https_proxy",
            value=https_proxy,
        )

    no_proxy = _normalize_no_proxy_for_export(proxy_config.no_proxy)
    if no_proxy is not None:
        _set_env_pair(
            env_values,
            uppercase_key="NPM_CONFIG_NOPROXY",
            lowercase_key="npm_config_noproxy",
            value=no_proxy,
        )

    if proxy_config.ssl_verify is False:
        _set_env_pair(
            env_values,
            uppercase_key="NPM_CONFIG_STRICT_SSL",
            lowercase_key="npm_config_strict_ssl",
            value="false",
        )
        env_values["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    elif proxy_config.ssl_verify is True:
        _set_env_pair(
            env_values,
            uppercase_key="NPM_CONFIG_STRICT_SSL",
            lowercase_key="npm_config_strict_ssl",
            value="true",
        )

    return env_values


def _set_env_pair(
    env_values: dict[str, str],
    *,
    uppercase_key: str,
    lowercase_key: str,
    value: str,
) -> None:
    env_values[uppercase_key] = value
    env_values[lowercase_key] = value


def host_matches_no_proxy(host: str, no_proxy: str | None) -> bool:
    normalized_host = host.strip("[]").lower()
    for rule in parse_no_proxy_rules(no_proxy):
        if rule.matches(normalized_host):
            return True
    return False


def parse_no_proxy_rules(no_proxy: str | None) -> tuple[NoProxyRule, ...]:
    normalized_no_proxy = _normalize_proxy_value(no_proxy)
    if normalized_no_proxy is None:
        return ()

    rules: list[NoProxyRule] = []
    for raw_candidate in normalized_no_proxy.replace(";", ",").split(","):
        candidate = raw_candidate.strip()
        if not candidate:
            continue
        if candidate == "*":
            return (NoProxyRule(kind="any"),)
        if "://" in candidate:
            parsed = urlsplit(candidate)
            candidate = parsed.hostname or parsed.netloc
        normalized_candidate = candidate.strip().strip("[]").lower()
        if not normalized_candidate:
            continue
        if normalized_candidate == "<local>":
            rules.append(NoProxyRule(kind="local"))
            continue
        if "*" in normalized_candidate:
            rules.append(NoProxyRule(kind="wildcard", value=normalized_candidate))
            continue
        if _is_ip_literal(normalized_candidate):
            rules.append(NoProxyRule(kind="ip", value=normalized_candidate))
            continue
        rules.append(
            NoProxyRule(
                kind="domain",
                value=normalized_candidate.lstrip("."),
            )
        )
    return tuple(rules)


def hydrate_proxy_config_with_saved_password(
    proxy_config: ProxyEnvConfig,
    *,
    config_dir: Path,
) -> ProxyEnvConfig:
    if proxy_config_contains_password(proxy_config):
        return proxy_config

    password = get_proxy_secret_store().get_password(config_dir)
    if password is None:
        return proxy_config

    return apply_proxy_password(proxy_config, password=password)


def apply_proxy_password(
    proxy_config: ProxyEnvConfig,
    *,
    password: str,
) -> ProxyEnvConfig:
    return ProxyEnvConfig(
        http_proxy=_apply_password_to_proxy_url(
            proxy_config.http_proxy, password=password
        ),
        https_proxy=_apply_password_to_proxy_url(
            proxy_config.https_proxy,
            password=password,
        ),
        all_proxy=_apply_password_to_proxy_url(
            proxy_config.all_proxy, password=password
        ),
        no_proxy=proxy_config.no_proxy,
        ssl_verify=proxy_config.ssl_verify,
    )


def sanitize_proxy_config_for_storage(proxy_config: ProxyEnvConfig) -> ProxyEnvConfig:
    return ProxyEnvConfig(
        http_proxy=_strip_password_from_proxy_url(proxy_config.http_proxy),
        https_proxy=_strip_password_from_proxy_url(proxy_config.https_proxy),
        all_proxy=_strip_password_from_proxy_url(proxy_config.all_proxy),
        no_proxy=proxy_config.no_proxy,
        ssl_verify=proxy_config.ssl_verify,
    )


def proxy_config_contains_password(proxy_config: ProxyEnvConfig) -> bool:
    return any(
        _proxy_url_has_password(proxy_url)
        for proxy_url in (
            proxy_config.http_proxy,
            proxy_config.https_proxy,
            proxy_config.all_proxy,
        )
    )


def _resolve_shared_proxy_auth(
    proxy_urls: tuple[ProxyUrlComponents, ProxyUrlComponents, ProxyUrlComponents],
) -> ProxyAuthParts:
    non_empty_auths = [
        proxy_url.auth
        for proxy_url in proxy_urls
        if proxy_url.auth.username is not None or proxy_url.auth.password is not None
    ]
    if not non_empty_auths:
        return ProxyAuthParts()

    first_auth = non_empty_auths[0]
    for auth in non_empty_auths[1:]:
        if auth != first_auth:
            return ProxyAuthParts()
    return first_auth


def _select_proxy_input_value(
    *,
    original_value: str | None,
    extracted_value: str | None,
    shared_auth: ProxyAuthParts,
    url_auth: ProxyAuthParts,
) -> str | None:
    if (
        shared_auth.username is None
        and shared_auth.password is None
        and (url_auth.username is not None or url_auth.password is not None)
    ):
        return original_value
    return extracted_value


def _extract_proxy_url_components(proxy_url: str | None) -> ProxyUrlComponents:
    normalized_proxy_url = _normalize_proxy_value(proxy_url)
    if normalized_proxy_url is None:
        return ProxyUrlComponents()

    parsed, had_explicit_scheme = _split_proxy_url(normalized_proxy_url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    sanitized = SplitResult(
        scheme=parsed.scheme,
        netloc=f"{hostname}{port}",
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return ProxyUrlComponents(
        sanitized_url=_serialize_proxy_split(
            sanitized,
            had_explicit_scheme=had_explicit_scheme,
        ),
        auth=ProxyAuthParts(
            username=(None if parsed.username is None else unquote(parsed.username)),
            password=(None if parsed.password is None else unquote(parsed.password)),
        ),
    )


def _apply_password_to_proxy_url(proxy_url: str | None, *, password: str) -> str | None:
    normalized_proxy_url = _normalize_proxy_value(proxy_url)
    if normalized_proxy_url is None:
        return None

    parsed, had_explicit_scheme = _split_proxy_url(normalized_proxy_url)
    if parsed.username is None or parsed.password is not None:
        return normalized_proxy_url

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    rebuilt = SplitResult(
        scheme=parsed.scheme,
        netloc=(
            f"{quote(unquote(parsed.username), safe='')}:{quote(password, safe='')}@"
            f"{hostname}{port}"
        ),
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return _serialize_proxy_split(rebuilt, had_explicit_scheme=had_explicit_scheme)


def _strip_password_from_proxy_url(proxy_url: str | None) -> str | None:
    normalized_proxy_url = _normalize_proxy_value(proxy_url)
    if normalized_proxy_url is None:
        return None

    parsed, had_explicit_scheme = _split_proxy_url(normalized_proxy_url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    userinfo = ""
    if parsed.username is not None:
        userinfo = f"{quote(unquote(parsed.username), safe='')}@"
    rebuilt = SplitResult(
        scheme=parsed.scheme,
        netloc=f"{userinfo}{hostname}{port}",
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return _serialize_proxy_split(rebuilt, had_explicit_scheme=had_explicit_scheme)


def _compose_proxy_url_with_auth(
    proxy_url: str | None,
    *,
    username: str | None,
    password: str | None,
) -> str | None:
    normalized_proxy_url = _normalize_proxy_value(proxy_url)
    if normalized_proxy_url is None:
        return None

    parsed, had_explicit_scheme = _split_proxy_url(normalized_proxy_url)
    if parsed.username is not None:
        return normalized_proxy_url

    if username is None:
        return normalized_proxy_url

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    userinfo = username
    if password is not None:
        userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    else:
        userinfo = quote(username, safe="")
    composed = SplitResult(
        scheme=parsed.scheme,
        netloc=f"{userinfo}@{hostname}{port}",
        path=parsed.path,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return _serialize_proxy_split(composed, had_explicit_scheme=had_explicit_scheme)


def _split_proxy_url(proxy_url: str) -> tuple[SplitResult, bool]:
    if "://" in proxy_url:
        return urlsplit(proxy_url), True
    return urlsplit(f"http://{proxy_url}"), False


def _serialize_proxy_split(
    parsed: SplitResult,
    *,
    had_explicit_scheme: bool,
) -> str:
    serialized = urlunsplit(parsed)
    if had_explicit_scheme:
        return serialized
    return serialized.removeprefix("http://")


def _proxy_url_has_password(proxy_url: str | None) -> bool:
    normalized_proxy_url = _normalize_proxy_value(proxy_url)
    if normalized_proxy_url is None:
        return False

    parsed, _had_explicit_scheme = _split_proxy_url(normalized_proxy_url)
    return parsed.password is not None


def _resolve_proxy_secret_config_dir(extra_env_files: tuple[Path, ...]) -> Path:
    if extra_env_files:
        return extra_env_files[0].expanduser().resolve().parent
    return get_project_env_file_path().parent


def _read_ssl_verify_env(env_values: Mapping[str, str]) -> bool | None:
    raw_value = None
    for key in _SSL_VERIFY_ENV_KEYS:
        candidate = env_values.get(key)
        if candidate is not None:
            raw_value = candidate
            break
    if raw_value is None:
        return None

    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        "Invalid SSL_VERIFY value. Use one of: true/false, yes/no, on/off, 1/0."
    )


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True

# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from relay_teams.env.clawhub_auth import (
        ClawHubCliLoginResult,
        build_clawhub_managed_subprocess_env,
        clear_clawhub_runtime_home,
        ensure_clawhub_cli_login,
        get_clawhub_runtime_config_path,
        get_clawhub_runtime_home,
    )
    from relay_teams.env.clawhub_cli import (
        CLAWHUB_NPM_PACKAGE_NAME,
        CLAWHUB_PREFERRED_NPM_REGISTRY,
        ClawHubCliInstallResult,
        clear_clawhub_path_cache,
        install_clawhub_via_npm,
        resolve_existing_clawhub_path,
    )
    from relay_teams.env.clawhub_config_models import ClawHubConfig
    from relay_teams.env.clawhub_config_service import ClawHubConfigService
    from relay_teams.env.clawhub_env import (
        CLAWHUB_REGISTRY_ENV_KEY,
        CLAWHUB_SITE_ENV_KEY,
        CLAWHUB_TOKEN_ENV_KEY,
        DEFAULT_CLAWHUB_CN_REGISTRY,
        DEFAULT_CLAWHUB_CN_SITE,
        build_clawhub_cli_env,
        build_clawhub_subprocess_env,
        clawhub_env_keys,
        normalize_clawhub_registry,
        normalize_clawhub_site,
        normalize_clawhub_token,
        resolve_clawhub_registry_from_env,
        resolve_clawhub_site_from_env,
        resolve_clawhub_token_from_env,
        resolve_default_clawhub_registry,
        resolve_default_clawhub_site,
        strip_clawhub_endpoint_overrides,
    )
    from relay_teams.env.environment_variable_models import (
        EnvironmentVariableCatalog,
        EnvironmentVariableRecord,
        EnvironmentVariableSaveRequest,
        EnvironmentVariableScope,
        EnvironmentVariableValueKind,
    )
    from relay_teams.env.environment_variable_service import EnvironmentVariableService
    from relay_teams.env.github_config_models import GitHubConfig
    from relay_teams.env.github_config_service import GitHubConfigService
    from relay_teams.env.github_env import (
        GH_NO_EXTENSION_UPDATE_NOTIFIER_ENV_KEY,
        GH_NO_UPDATE_NOTIFIER_ENV_KEY,
        GH_PROMPT_DISABLED_ENV_KEY,
        GITHUB_TOKEN_ENV_KEY,
        GH_TOKEN_ENV_KEY,
        build_github_cli_env,
        github_env_keys,
        normalize_github_token,
        resolve_github_token_from_env,
    )
    from relay_teams.env.proxy_config_service import ProxyConfigService
    from relay_teams.env.proxy_env import (
        ProxyEnvConfig,
        ProxyEnvInput,
        apply_proxy_env_to_process_env,
        build_subprocess_env,
        extract_proxy_env_vars,
        host_matches_no_proxy,
        load_proxy_env_config,
        mask_proxy_url,
        parse_no_proxy_rules,
        proxy_applies_to_url,
        resolve_proxy_env_config,
        sync_proxy_env_to_process_env,
    )
    from relay_teams.env.runtime_env import (
        get_app_env_file_path,
        get_env_var,
        get_project_env_file_path,
        get_user_env_file_path,
        load_env_file,
        load_merged_env_vars,
        sync_app_env_to_process_env,
    )
    from relay_teams.env.web_config_models import (
        WebConfig,
        WebFallbackProvider,
        WebProvider,
    )
    from relay_teams.env.web_config_service import WebConfigService

__all__ = [
    "EnvironmentVariableCatalog",
    "EnvironmentVariableRecord",
    "EnvironmentVariableSaveRequest",
    "EnvironmentVariableScope",
    "EnvironmentVariableService",
    "EnvironmentVariableValueKind",
    "CLAWHUB_NPM_PACKAGE_NAME",
    "CLAWHUB_PREFERRED_NPM_REGISTRY",
    "CLAWHUB_REGISTRY_ENV_KEY",
    "CLAWHUB_SITE_ENV_KEY",
    "CLAWHUB_TOKEN_ENV_KEY",
    "ClawHubCliInstallResult",
    "ClawHubCliLoginResult",
    "ClawHubConfig",
    "ClawHubConfigService",
    "GitHubConfig",
    "GitHubConfigService",
    "GITHUB_TOKEN_ENV_KEY",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER_ENV_KEY",
    "GH_NO_UPDATE_NOTIFIER_ENV_KEY",
    "GH_PROMPT_DISABLED_ENV_KEY",
    "GH_TOKEN_ENV_KEY",
    "ProxyEnvConfig",
    "ProxyEnvInput",
    "ProxyConfigService",
    "apply_proxy_env_to_process_env",
    "build_subprocess_env",
    "build_clawhub_cli_env",
    "build_clawhub_managed_subprocess_env",
    "build_clawhub_subprocess_env",
    "extract_proxy_env_vars",
    "build_github_cli_env",
    "clawhub_env_keys",
    "clear_clawhub_path_cache",
    "clear_clawhub_runtime_home",
    "install_clawhub_via_npm",
    "ensure_clawhub_cli_login",
    "get_app_env_file_path",
    "get_env_var",
    "get_project_env_file_path",
    "get_clawhub_runtime_config_path",
    "get_clawhub_runtime_home",
    "get_user_env_file_path",
    "github_env_keys",
    "host_matches_no_proxy",
    "load_proxy_env_config",
    "load_env_file",
    "load_merged_env_vars",
    "normalize_clawhub_site",
    "normalize_clawhub_token",
    "sync_app_env_to_process_env",
    "mask_proxy_url",
    "parse_no_proxy_rules",
    "proxy_applies_to_url",
    "resolve_proxy_env_config",
    "resolve_clawhub_registry_from_env",
    "resolve_clawhub_site_from_env",
    "resolve_default_clawhub_registry",
    "resolve_default_clawhub_site",
    "resolve_existing_clawhub_path",
    "resolve_clawhub_token_from_env",
    "strip_clawhub_endpoint_overrides",
    "normalize_github_token",
    "resolve_github_token_from_env",
    "sync_proxy_env_to_process_env",
    "WebConfig",
    "WebConfigService",
    "WebFallbackProvider",
    "WebProvider",
    "DEFAULT_CLAWHUB_CN_REGISTRY",
    "DEFAULT_CLAWHUB_CN_SITE",
    "normalize_clawhub_registry",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "EnvironmentVariableCatalog": (
        "relay_teams.env.environment_variable_models",
        "EnvironmentVariableCatalog",
    ),
    "EnvironmentVariableRecord": (
        "relay_teams.env.environment_variable_models",
        "EnvironmentVariableRecord",
    ),
    "EnvironmentVariableSaveRequest": (
        "relay_teams.env.environment_variable_models",
        "EnvironmentVariableSaveRequest",
    ),
    "EnvironmentVariableScope": (
        "relay_teams.env.environment_variable_models",
        "EnvironmentVariableScope",
    ),
    "EnvironmentVariableService": (
        "relay_teams.env.environment_variable_service",
        "EnvironmentVariableService",
    ),
    "EnvironmentVariableValueKind": (
        "relay_teams.env.environment_variable_models",
        "EnvironmentVariableValueKind",
    ),
    "CLAWHUB_NPM_PACKAGE_NAME": (
        "relay_teams.env.clawhub_cli",
        "CLAWHUB_NPM_PACKAGE_NAME",
    ),
    "CLAWHUB_PREFERRED_NPM_REGISTRY": (
        "relay_teams.env.clawhub_cli",
        "CLAWHUB_PREFERRED_NPM_REGISTRY",
    ),
    "CLAWHUB_REGISTRY_ENV_KEY": (
        "relay_teams.env.clawhub_env",
        "CLAWHUB_REGISTRY_ENV_KEY",
    ),
    "CLAWHUB_SITE_ENV_KEY": ("relay_teams.env.clawhub_env", "CLAWHUB_SITE_ENV_KEY"),
    "CLAWHUB_TOKEN_ENV_KEY": (
        "relay_teams.env.clawhub_env",
        "CLAWHUB_TOKEN_ENV_KEY",
    ),
    "ClawHubCliInstallResult": (
        "relay_teams.env.clawhub_cli",
        "ClawHubCliInstallResult",
    ),
    "ClawHubCliLoginResult": (
        "relay_teams.env.clawhub_auth",
        "ClawHubCliLoginResult",
    ),
    "ClawHubConfig": ("relay_teams.env.clawhub_config_models", "ClawHubConfig"),
    "ClawHubConfigService": (
        "relay_teams.env.clawhub_config_service",
        "ClawHubConfigService",
    ),
    "GitHubConfig": ("relay_teams.env.github_config_models", "GitHubConfig"),
    "GitHubConfigService": (
        "relay_teams.env.github_config_service",
        "GitHubConfigService",
    ),
    "GITHUB_TOKEN_ENV_KEY": ("relay_teams.env.github_env", "GITHUB_TOKEN_ENV_KEY"),
    "GH_NO_EXTENSION_UPDATE_NOTIFIER_ENV_KEY": (
        "relay_teams.env.github_env",
        "GH_NO_EXTENSION_UPDATE_NOTIFIER_ENV_KEY",
    ),
    "GH_NO_UPDATE_NOTIFIER_ENV_KEY": (
        "relay_teams.env.github_env",
        "GH_NO_UPDATE_NOTIFIER_ENV_KEY",
    ),
    "GH_PROMPT_DISABLED_ENV_KEY": (
        "relay_teams.env.github_env",
        "GH_PROMPT_DISABLED_ENV_KEY",
    ),
    "GH_TOKEN_ENV_KEY": ("relay_teams.env.github_env", "GH_TOKEN_ENV_KEY"),
    "ProxyEnvConfig": ("relay_teams.env.proxy_env", "ProxyEnvConfig"),
    "ProxyEnvInput": ("relay_teams.env.proxy_env", "ProxyEnvInput"),
    "ProxyConfigService": (
        "relay_teams.env.proxy_config_service",
        "ProxyConfigService",
    ),
    "apply_proxy_env_to_process_env": (
        "relay_teams.env.proxy_env",
        "apply_proxy_env_to_process_env",
    ),
    "build_subprocess_env": ("relay_teams.env.proxy_env", "build_subprocess_env"),
    "build_clawhub_cli_env": (
        "relay_teams.env.clawhub_env",
        "build_clawhub_cli_env",
    ),
    "build_clawhub_managed_subprocess_env": (
        "relay_teams.env.clawhub_auth",
        "build_clawhub_managed_subprocess_env",
    ),
    "build_clawhub_subprocess_env": (
        "relay_teams.env.clawhub_env",
        "build_clawhub_subprocess_env",
    ),
    "extract_proxy_env_vars": ("relay_teams.env.proxy_env", "extract_proxy_env_vars"),
    "build_github_cli_env": ("relay_teams.env.github_env", "build_github_cli_env"),
    "clawhub_env_keys": ("relay_teams.env.clawhub_env", "clawhub_env_keys"),
    "clear_clawhub_path_cache": (
        "relay_teams.env.clawhub_cli",
        "clear_clawhub_path_cache",
    ),
    "clear_clawhub_runtime_home": (
        "relay_teams.env.clawhub_auth",
        "clear_clawhub_runtime_home",
    ),
    "install_clawhub_via_npm": (
        "relay_teams.env.clawhub_cli",
        "install_clawhub_via_npm",
    ),
    "ensure_clawhub_cli_login": (
        "relay_teams.env.clawhub_auth",
        "ensure_clawhub_cli_login",
    ),
    "get_app_env_file_path": ("relay_teams.env.runtime_env", "get_app_env_file_path"),
    "get_env_var": ("relay_teams.env.runtime_env", "get_env_var"),
    "get_project_env_file_path": (
        "relay_teams.env.runtime_env",
        "get_project_env_file_path",
    ),
    "get_clawhub_runtime_config_path": (
        "relay_teams.env.clawhub_auth",
        "get_clawhub_runtime_config_path",
    ),
    "get_clawhub_runtime_home": (
        "relay_teams.env.clawhub_auth",
        "get_clawhub_runtime_home",
    ),
    "get_user_env_file_path": (
        "relay_teams.env.runtime_env",
        "get_user_env_file_path",
    ),
    "github_env_keys": ("relay_teams.env.github_env", "github_env_keys"),
    "host_matches_no_proxy": ("relay_teams.env.proxy_env", "host_matches_no_proxy"),
    "load_proxy_env_config": ("relay_teams.env.proxy_env", "load_proxy_env_config"),
    "load_env_file": ("relay_teams.env.runtime_env", "load_env_file"),
    "load_merged_env_vars": ("relay_teams.env.runtime_env", "load_merged_env_vars"),
    "normalize_clawhub_site": (
        "relay_teams.env.clawhub_env",
        "normalize_clawhub_site",
    ),
    "normalize_clawhub_token": (
        "relay_teams.env.clawhub_env",
        "normalize_clawhub_token",
    ),
    "sync_app_env_to_process_env": (
        "relay_teams.env.runtime_env",
        "sync_app_env_to_process_env",
    ),
    "mask_proxy_url": ("relay_teams.env.proxy_env", "mask_proxy_url"),
    "parse_no_proxy_rules": ("relay_teams.env.proxy_env", "parse_no_proxy_rules"),
    "proxy_applies_to_url": ("relay_teams.env.proxy_env", "proxy_applies_to_url"),
    "resolve_proxy_env_config": (
        "relay_teams.env.proxy_env",
        "resolve_proxy_env_config",
    ),
    "resolve_clawhub_registry_from_env": (
        "relay_teams.env.clawhub_env",
        "resolve_clawhub_registry_from_env",
    ),
    "resolve_clawhub_site_from_env": (
        "relay_teams.env.clawhub_env",
        "resolve_clawhub_site_from_env",
    ),
    "resolve_default_clawhub_registry": (
        "relay_teams.env.clawhub_env",
        "resolve_default_clawhub_registry",
    ),
    "resolve_default_clawhub_site": (
        "relay_teams.env.clawhub_env",
        "resolve_default_clawhub_site",
    ),
    "resolve_existing_clawhub_path": (
        "relay_teams.env.clawhub_cli",
        "resolve_existing_clawhub_path",
    ),
    "resolve_clawhub_token_from_env": (
        "relay_teams.env.clawhub_env",
        "resolve_clawhub_token_from_env",
    ),
    "strip_clawhub_endpoint_overrides": (
        "relay_teams.env.clawhub_env",
        "strip_clawhub_endpoint_overrides",
    ),
    "normalize_github_token": (
        "relay_teams.env.github_env",
        "normalize_github_token",
    ),
    "resolve_github_token_from_env": (
        "relay_teams.env.github_env",
        "resolve_github_token_from_env",
    ),
    "sync_proxy_env_to_process_env": (
        "relay_teams.env.proxy_env",
        "sync_proxy_env_to_process_env",
    ),
    "WebConfig": ("relay_teams.env.web_config_models", "WebConfig"),
    "WebConfigService": ("relay_teams.env.web_config_service", "WebConfigService"),
    "WebFallbackProvider": (
        "relay_teams.env.web_config_models",
        "WebFallbackProvider",
    ),
    "WebProvider": ("relay_teams.env.web_config_models", "WebProvider"),
    "DEFAULT_CLAWHUB_CN_REGISTRY": (
        "relay_teams.env.clawhub_env",
        "DEFAULT_CLAWHUB_CN_REGISTRY",
    ),
    "DEFAULT_CLAWHUB_CN_SITE": (
        "relay_teams.env.clawhub_env",
        "DEFAULT_CLAWHUB_CN_SITE",
    ),
    "normalize_clawhub_registry": (
        "relay_teams.env.clawhub_env",
        "normalize_clawhub_registry",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)

# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.env.environment_variable_models import (
    EnvironmentVariableCatalog,
    EnvironmentVariableRecord,
    EnvironmentVariableSaveRequest,
    EnvironmentVariableScope,
    EnvironmentVariableValueKind,
)
from agent_teams.env.environment_variable_service import EnvironmentVariableService
from agent_teams.env.runtime_env import (
    get_app_env_file_path,
    get_env_var,
    get_project_env_file_path,
    get_user_env_file_path,
    load_env_file,
    load_merged_env_vars,
    sync_app_env_to_process_env,
)
from agent_teams.env.proxy_env import (
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
from agent_teams.env.github_config_models import GitHubConfig
from agent_teams.env.github_config_service import GitHubConfigService
from agent_teams.env.github_connectivity import (
    GitHubConnectivityProbeDiagnostics,
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeResult,
    GitHubConnectivityProbeService,
)
from agent_teams.env.python_env import (
    AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY,
    bind_subprocess_python_env,
    resolve_subprocess_python_executable,
)
from agent_teams.env.github_env import (
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
from agent_teams.env.proxy_config_service import ProxyConfigService
from agent_teams.env.web_config_models import (
    WebConfig,
    WebFallbackProvider,
    WebProvider,
)
from agent_teams.env.web_config_service import WebConfigService
from agent_teams.env.web_connectivity import (
    WebConnectivityProbeDiagnostics,
    WebConnectivityProbeRequest,
    WebConnectivityProbeResult,
    WebConnectivityProbeService,
)

__all__ = [
    "EnvironmentVariableCatalog",
    "EnvironmentVariableRecord",
    "EnvironmentVariableSaveRequest",
    "EnvironmentVariableScope",
    "EnvironmentVariableService",
    "EnvironmentVariableValueKind",
    "GitHubConfig",
    "GitHubConfigService",
    "GitHubConnectivityProbeDiagnostics",
    "GitHubConnectivityProbeRequest",
    "GitHubConnectivityProbeResult",
    "GitHubConnectivityProbeService",
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
    "bind_subprocess_python_env",
    "extract_proxy_env_vars",
    "build_github_cli_env",
    "get_app_env_file_path",
    "get_env_var",
    "get_project_env_file_path",
    "get_user_env_file_path",
    "github_env_keys",
    "host_matches_no_proxy",
    "load_proxy_env_config",
    "load_env_file",
    "load_merged_env_vars",
    "sync_app_env_to_process_env",
    "mask_proxy_url",
    "parse_no_proxy_rules",
    "proxy_applies_to_url",
    "resolve_proxy_env_config",
    "resolve_subprocess_python_executable",
    "normalize_github_token",
    "resolve_github_token_from_env",
    "sync_proxy_env_to_process_env",
    "AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY",
    "WebConnectivityProbeDiagnostics",
    "WebConnectivityProbeRequest",
    "WebConnectivityProbeResult",
    "WebConnectivityProbeService",
    "WebConfig",
    "WebConfigService",
    "WebFallbackProvider",
    "WebProvider",
]

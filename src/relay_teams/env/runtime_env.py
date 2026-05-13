# -*- coding: utf-8 -*-
from __future__ import annotations

import sys

import relay_teams.runtime_env as _runtime_env

PROCESS_ENV_BASELINE = _runtime_env.PROCESS_ENV_BASELINE
SYNCED_APP_ENV_KEYS = _runtime_env.SYNCED_APP_ENV_KEYS
get_app_env_file_path = _runtime_env.get_app_env_file_path
get_env_var = _runtime_env.get_env_var
get_project_env_file_path = _runtime_env.get_project_env_file_path
get_user_env_file_path = _runtime_env.get_user_env_file_path
load_env_file = _runtime_env.load_env_file
load_merged_env_vars = _runtime_env.load_merged_env_vars
load_secret_env_vars = _runtime_env.load_secret_env_vars
os = _runtime_env.os
sync_app_env_to_process_env = _runtime_env.sync_app_env_to_process_env

__all__ = [
    "PROCESS_ENV_BASELINE",
    "SYNCED_APP_ENV_KEYS",
    "get_app_env_file_path",
    "get_env_var",
    "get_project_env_file_path",
    "get_user_env_file_path",
    "load_env_file",
    "load_merged_env_vars",
    "load_secret_env_vars",
    "os",
    "sync_app_env_to_process_env",
]

sys.modules[__name__] = _runtime_env

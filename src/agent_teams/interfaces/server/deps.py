# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import Request

from agent_teams.coordination.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.env.environment_variable_service import EnvironmentVariableService
from agent_teams.env.proxy_config_service import ProxyConfigService
from agent_teams.interfaces.server.container import ServerContainer
from agent_teams.interfaces.server.config_status_service import ConfigStatusService
from agent_teams.mcp.config_reload_service import McpConfigReloadService
from agent_teams.mcp.service import McpService
from agent_teams.notifications.settings_service import NotificationSettingsService
from agent_teams.reflection.service import ReflectionService
from agent_teams.providers.model_config_service import ModelConfigService
from agent_teams.roles import RoleRegistry
from agent_teams.roles.settings_service import RoleSettingsService
from agent_teams.runs.manager import RunManager
from agent_teams.sessions import SessionService
from agent_teams.skills.config_reload_service import SkillsConfigReloadService
from agent_teams.skills.registry import SkillRegistry
from agent_teams.state.task_repo import TaskRepository
from agent_teams.tools.registry import ToolRegistry
from agent_teams.triggers import TriggerService


def get_container(request: Request) -> ServerContainer:
    return request.app.state.container


def get_run_service(request: Request) -> RunManager:
    return get_container(request).run_service


def get_session_service(request: Request) -> SessionService:
    return get_container(request).session_service


def get_task_service(request: Request) -> TaskOrchestrationService:
    return get_container(request).task_service


def get_reflection_service(request: Request) -> ReflectionService:
    return get_container(request).reflection_service


def get_trigger_service(request: Request) -> TriggerService:
    return get_container(request).trigger_service


def get_config_status_service(request: Request) -> ConfigStatusService:
    return get_container(request).config_status_service


def get_model_config_service(request: Request) -> ModelConfigService:
    return get_container(request).model_config_service


def get_notification_settings_service(request: Request) -> NotificationSettingsService:
    return get_container(request).notification_settings_service


def get_mcp_config_reload_service(request: Request) -> McpConfigReloadService:
    return get_container(request).mcp_config_reload_service


def get_skills_config_reload_service(request: Request) -> SkillsConfigReloadService:
    return get_container(request).skills_config_reload_service


def get_mcp_service(request: Request) -> McpService:
    return get_container(request).mcp_service


def get_proxy_config_service(request: Request) -> ProxyConfigService:
    return get_container(request).proxy_config_service


def get_environment_variable_service(request: Request) -> EnvironmentVariableService:
    return get_container(request).environment_variable_service


def get_task_repo(request: Request) -> TaskRepository:
    return get_container(request).task_repo


def get_role_registry(request: Request) -> RoleRegistry:
    return get_container(request).role_registry


def get_role_settings_service(request: Request) -> RoleSettingsService:
    return get_container(request).role_settings_service


def get_tool_registry(request: Request) -> ToolRegistry:
    return get_container(request).tool_registry


def get_skill_registry(request: Request) -> SkillRegistry:
    return get_container(request).skill_registry

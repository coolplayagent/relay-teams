# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import Request

from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from agent_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.env.environment_variable_service import EnvironmentVariableService
from agent_teams.env.proxy_config_service import ProxyConfigService
from agent_teams.env.web_config_service import WebConfigService
from agent_teams.interfaces.server.config_status_service import ConfigStatusService
from agent_teams.interfaces.server.container import ServerContainer
from agent_teams.interfaces.server.ui_language_service import UiLanguageSettingsService
from agent_teams.feishu import (
    FeishuSubscriptionService,
    FeishuTriggerConfigService,
    FeishuTriggerHandler,
)
from agent_teams.mcp.config_reload_service import McpConfigReloadService
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.mcp.mcp_service import McpService
from agent_teams.metrics import MetricsService
from agent_teams.notifications.notification_settings_service import (
    NotificationSettingsService,
)
from agent_teams.providers.model_config_service import ModelConfigService
from agent_teams.roles import RoleMemoryService, RoleRegistry
from agent_teams.roles.settings_service import RoleSettingsService
from agent_teams.sessions import SessionService
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.skills.config_reload_service import SkillsConfigReloadService
from agent_teams.skills.skill_registry import SkillRegistry
from agent_teams.tools.registry import ToolRegistry
from agent_teams.triggers import TriggerService
from agent_teams.workspace import WorkspaceManager, WorkspaceService


def get_container(request: Request) -> ServerContainer:
    return request.app.state.container


def get_run_service(request: Request) -> RunManager:
    return get_container(request).run_service


def get_session_service(request: Request) -> SessionService:
    return get_container(request).session_service


def get_task_service(request: Request) -> TaskOrchestrationService:
    return get_container(request).task_service


def get_trigger_service(request: Request) -> TriggerService:
    return get_container(request).trigger_service


def get_feishu_trigger_handler(request: Request) -> FeishuTriggerHandler:
    return get_container(request).feishu_trigger_handler


def get_feishu_trigger_config_service(request: Request) -> FeishuTriggerConfigService:
    return get_container(request).feishu_trigger_config_service


def get_feishu_subscription_service(request: Request) -> FeishuSubscriptionService:
    return get_container(request).feishu_subscription_service


def get_config_status_service(request: Request) -> ConfigStatusService:
    return get_container(request).config_status_service


def get_model_config_service(request: Request) -> ModelConfigService:
    return get_container(request).model_config_service


def get_notification_settings_service(request: Request) -> NotificationSettingsService:
    return get_container(request).notification_settings_service


def get_orchestration_settings_service(
    request: Request,
) -> OrchestrationSettingsService:
    return get_container(request).orchestration_settings_service


def get_mcp_config_reload_service(request: Request) -> McpConfigReloadService:
    return get_container(request).mcp_config_reload_service


def get_skills_config_reload_service(request: Request) -> SkillsConfigReloadService:
    return get_container(request).skills_config_reload_service


def get_mcp_service(request: Request) -> McpService:
    return get_container(request).mcp_service


def get_mcp_registry(request: Request) -> McpRegistry:
    return get_container(request).mcp_registry


def get_proxy_config_service(request: Request) -> ProxyConfigService:
    return get_container(request).proxy_config_service


def get_environment_variable_service(request: Request) -> EnvironmentVariableService:
    return get_container(request).environment_variable_service


def get_web_config_service(request: Request) -> WebConfigService:
    return get_container(request).web_config_service


def get_ui_language_settings_service(request: Request) -> UiLanguageSettingsService:
    return get_container(request).ui_language_settings_service


def get_task_repo(request: Request) -> TaskRepository:
    return get_container(request).task_repo


def get_role_registry(request: Request) -> RoleRegistry:
    return get_container(request).role_registry


def get_role_settings_service(request: Request) -> RoleSettingsService:
    return get_container(request).role_settings_service


def get_role_memory_service(request: Request) -> RoleMemoryService:
    return get_container(request).role_memory_service


def get_workspace_service(request: Request) -> WorkspaceService:
    return get_container(request).workspace_service


def get_workspace_manager(request: Request) -> WorkspaceManager:
    return get_container(request).workspace_manager


def get_tool_registry(request: Request) -> ToolRegistry:
    return get_container(request).tool_registry


def get_skill_registry(request: Request) -> SkillRegistry:
    return get_container(request).skill_registry


def get_metrics_service(request: Request) -> MetricsService:
    return get_container(request).metrics_service

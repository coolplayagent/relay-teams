# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import Request

from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.env.clawhub_config_service import ClawHubConfigService
from relay_teams.env.environment_variable_service import EnvironmentVariableService
from relay_teams.env.github_config_service import GitHubConfigService
from relay_teams.env.localhost_run_tunnel_service import LocalhostRunTunnelService
from relay_teams.external_agents import ExternalAgentConfigService
from relay_teams.automation import AutomationService
from relay_teams.env.proxy_config_service import ProxyConfigService
from relay_teams.env.web_config_service import WebConfigService
from relay_teams.interfaces.server.config_status_service import ConfigStatusService
from relay_teams.interfaces.server.container import ServerContainer
from relay_teams.interfaces.server.ui_language_service import UiLanguageSettingsService
from relay_teams.gateway.feishu import (
    FeishuGatewayService,
    FeishuSubscriptionService,
    FeishuTriggerHandler,
)
from relay_teams.mcp.config_reload_service import McpConfigReloadService
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.mcp.mcp_service import McpService
from relay_teams.media import MediaAssetService
from relay_teams.metrics import MetricsService
from relay_teams.notifications.notification_settings_service import (
    NotificationSettingsService,
)
from relay_teams.providers.model_config_service import ModelConfigService
from relay_teams.roles import RoleMemoryService, RoleRegistry
from relay_teams.roles.settings_service import RoleSettingsService
from relay_teams.sessions import SessionService
from relay_teams.sessions.runs.run_manager import RunManager
from relay_teams.skills.config_reload_service import SkillsConfigReloadService
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.skills.skill_routing_service import SkillRuntimeService
from relay_teams.tools.registry import ToolRegistry
from relay_teams.triggers import GitHubTriggerService
from relay_teams.gateway.wechat import WeChatGatewayService
from relay_teams.workspace import WorkspaceManager, WorkspaceService
from relay_teams.hooks import HookService


def get_container(request: Request) -> ServerContainer:
    return request.app.state.container


def get_run_service(request: Request) -> RunManager:
    return get_container(request).run_service


def get_session_service(request: Request) -> SessionService:
    return get_container(request).session_service


def get_task_service(request: Request) -> TaskOrchestrationService:
    return get_container(request).task_service


def get_automation_service(request: Request) -> AutomationService:
    return get_container(request).automation_service


def get_feishu_gateway_service(request: Request) -> FeishuGatewayService:
    return get_container(request).feishu_gateway_service


def get_feishu_trigger_handler(request: Request) -> FeishuTriggerHandler:
    return get_container(request).feishu_trigger_handler


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


def get_external_agent_config_service(request: Request) -> ExternalAgentConfigService:
    return get_container(request).external_agent_config_service


def get_clawhub_config_service(request: Request) -> ClawHubConfigService:
    return get_container(request).clawhub_config_service


def get_github_config_service(request: Request) -> GitHubConfigService:
    return get_container(request).github_config_service


def get_localhost_run_tunnel_service(request: Request) -> LocalhostRunTunnelService:
    return get_container(request).localhost_run_tunnel_service


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


def get_media_asset_service(request: Request) -> MediaAssetService:
    return get_container(request).media_asset_service


def get_tool_registry(request: Request) -> ToolRegistry:
    return get_container(request).tool_registry


def get_skill_registry(request: Request) -> SkillRegistry:
    return get_container(request).skill_registry


def get_skill_runtime_service(request: Request) -> SkillRuntimeService:
    return get_container(request).skill_runtime_service


def get_clawhub_skill_service(request: Request) -> ClawHubSkillService:
    return get_container(request).clawhub_skill_service


def get_metrics_service(request: Request) -> MetricsService:
    return get_container(request).metrics_service


def get_wechat_gateway_service(request: Request) -> WeChatGatewayService:
    return get_container(request).wechat_gateway_service


def get_github_trigger_service(request: Request) -> GitHubTriggerService:
    return get_container(request).github_trigger_service


def get_hook_service(request: Request) -> HookService:
    return get_container(request).hook_service

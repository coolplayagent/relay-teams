# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

from pydantic import JsonValue
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.env.proxy_env import ProxyEnvInput
from relay_teams.external_agents import (
    ExternalAgentConfig,
    ExternalAgentSummary,
    ExternalAgentTestResult,
    StdioTransportConfig,
)
from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.net.clawhub_connectivity import (
    ClawHubConnectivityProbeRequest,
    ClawHubConnectivityProbeResult,
)
from relay_teams.env.github_config_models import (
    GitHubConfig,
    GitHubConfigUpdate,
    GitHubConfigView,
)
from relay_teams.net.github_connectivity import (
    GitHubConnectivityProbeRequest,
    GitHubConnectivityProbeResult,
    GitHubWebhookConnectivityProbeRequest,
    GitHubWebhookConnectivityProbeResult,
)
from relay_teams.env.localhost_run_tunnel_service import (
    LocalhostRunTunnelStartRequest,
    LocalhostRunTunnelStatus,
)
from relay_teams.env.web_config_models import (
    DEFAULT_SEARXNG_INSTANCE_SEEDS,
    DEFAULT_SEARXNG_INSTANCE_URL,
    WebConfig,
    WebFallbackProvider,
    WebProvider,
)
from relay_teams.net.web_connectivity import (
    WebConnectivityProbeRequest,
    WebConnectivityProbeResult,
)
from relay_teams.media import MediaModality
from relay_teams.interfaces.server.deps import (
    get_clawhub_connectivity_probe_service,
    get_clawhub_config_service,
    get_clawhub_skill_service,
    get_config_status_service,
    get_environment_variable_service,
    get_external_agent_config_service,
    get_github_connectivity_probe_service,
    get_github_config_service,
    get_github_webhook_connectivity_probe_service,
    get_localhost_run_tunnel_service,
    get_github_trigger_service,
    get_mcp_config_reload_service,
    get_model_config_service,
    get_notification_settings_service,
    get_orchestration_settings_service,
    get_proxy_config_service,
    get_ssh_profile_service,
    get_skills_config_reload_service,
    get_ui_language_settings_service,
    get_web_config_service,
    get_web_connectivity_probe_service,
)
from relay_teams.interfaces.server.ui_language_models import (
    UiLanguage,
    UiLanguageSettings,
)
from relay_teams.interfaces.server.routers import system
from relay_teams.providers.model_connectivity import (
    ModelConnectivityProbeRequest,
    ModelConnectivityProbeResult,
    ModelDiscoveryResult,
)
from relay_teams.providers.model_config import (
    DEFAULT_MAAS_BASE_URL,
    ModelConfigPayload,
    ModelFallbackConfig,
    ModelFallbackPolicy,
    ModelFallbackStrategy,
    ProviderModelInfo,
    ProviderType,
)
from relay_teams.skills.clawhub_models import (
    ClawHubSkillDetail,
    ClawHubSkillSummary,
    ClawHubSkillWriteRequest,
)
from relay_teams.skills.skill_models import SkillSource
from relay_teams.notifications.models import NotificationConfig
from relay_teams.agents.orchestration.settings_models import OrchestrationSettings
from relay_teams.workspace import (
    SshProfileConfig,
    SshProfileConnectivityProbeRequest,
    SshProfileConnectivityProbeResult,
    SshProfilePasswordRevealView,
    SshProfileRecord,
)


class _FakeSystemService:
    def __init__(self) -> None:
        self.saved_notification_config: dict[str, object] | None = None
        self.saved_orchestration_config: dict[str, object] | None = None
        self.saved_model_config: dict[str, object] | None = None
        self.saved_model_fallback_config: dict[str, object] | None = None
        self.saved_model_profile: tuple[str, dict[str, object], str | None] | None = (
            None
        )
        self.model_profile_error: Exception | None = None
        self.model_profile_delete_error: Exception | None = None
        self.model_config_error: Exception | None = None
        self.saved_proxy_config: dict[str, object] | None = None
        self.saved_web_config: dict[str, object] | None = None
        self.saved_clawhub_config: dict[str, object] | None = None
        self.saved_github_config: dict[str, object] | None = None
        self.current_github_token: str | None = None
        self.current_github_webhook_base_url: str | None = None
        self.refreshed_github_callback_previous_base_url: str | None = None
        self.tunnel_status = LocalhostRunTunnelStatus()
        self.started_tunnel_request: dict[str, object] | None = None
        self.stopped_tunnel_request: dict[str, object] | None = None
        self.saved_ui_language_settings: dict[str, object] | None = None
        self.proxy_save_error: RuntimeError | None = None
        self.model_reload_error: Exception | None = None
        self.proxy_reload_error: Exception | None = None
        self.mcp_reload_error: Exception | None = None
        self.skills_reload_error: Exception | None = None
        self.external_agents: dict[str, ExternalAgentConfig] = {
            "codex_local": ExternalAgentConfig(
                agent_id="codex_local",
                name="Codex Local",
                description="Runs Codex via stdio",
                transport=StdioTransportConfig(command="codex", args=("--serve",)),
            )
        }
        self.clawhub_skills: dict[str, ClawHubSkillDetail] = {
            "skill-creator-2": ClawHubSkillDetail(
                skill_id="skill-creator-2",
                runtime_name="skill-creator",
                description="Create Codex skills.",
                ref="skill-creator",
                source=SkillSource.USER_RELAY_TEAMS,
                directory="/tmp/.relay-teams/skills/skill-creator-2",
                manifest_path="/tmp/.relay-teams/skills/skill-creator-2/SKILL.md",
                valid=True,
                error=None,
                instructions="Create skills safely.",
                manifest_content="---\nname: skill-creator\n---\nCreate skills safely.\n",
                files=(),
            )
        }
        self.ssh_profiles: dict[str, SshProfileRecord] = {
            "prod": SshProfileRecord(
                ssh_profile_id="prod",
                host="prod-alias",
                username="deploy",
                port=22,
                has_password=True,
                has_private_key=True,
                private_key_name="id_ed25519",
            )
        }
        self.ssh_profile_passwords: dict[str, str] = {
            "prod": "relay-secret",
        }

    def get_config_status(self) -> dict[str, object]:
        return {"model": {"loaded": True}}

    def get_ui_language_settings(self) -> UiLanguageSettings:
        return UiLanguageSettings(language=UiLanguage.ZH_CN)

    def save_ui_language_settings(
        self,
        settings: UiLanguageSettings,
    ) -> UiLanguageSettings:
        self.saved_ui_language_settings = settings.model_dump(mode="json")
        return settings

    def get_model_config(self) -> dict[str, object]:
        return {
            "default": {
                "provider": "openai_compatible",
                "model": "gpt-4o-mini",
                "base_url": "https://example.test/v1",
                "api_key": "secret",
                "headers": [],
                "temperature": 0.2,
                "top_p": 1.0,
                "max_tokens": 2048,
                "context_window": 128000,
                "connect_timeout_seconds": 25.0,
                "is_default": True,
                "maas_auth": None,
                "ssl_verify": None,
            }
        }

    def get_model_profiles(self) -> dict[str, object]:
        return {
            "default": {
                "provider": "openai_compatible",
                "model": "gpt-4o-mini",
                "base_url": "https://example.test/v1",
                "api_key": "secret",
                "has_api_key": True,
                "headers": [],
                "is_default": True,
                "context_window": 128000,
                "fallback_policy_id": "same_provider_then_other_provider",
                "fallback_priority": 3,
                "capabilities": {
                    "input": {
                        "text": True,
                        "image": True,
                        "audio": False,
                        "video": False,
                        "pdf": False,
                    },
                    "output": {
                        "text": True,
                        "image": False,
                        "audio": False,
                        "video": False,
                        "pdf": False,
                    },
                },
                "input_modalities": ["image"],
            }
        }

    def get_model_fallback_config(self) -> ModelFallbackConfig:
        return ModelFallbackConfig(
            policies=(
                ModelFallbackPolicy(
                    policy_id="same_provider_then_other_provider",
                    name="Same Provider Then Other Provider",
                    strategy=(ModelFallbackStrategy.SAME_PROVIDER_THEN_OTHER_PROVIDER),
                ),
            )
        )

    def save_model_profile(
        self,
        name: str,
        profile: dict[str, object],
        *,
        source_name: str | None = None,
    ) -> None:
        if self.model_profile_error is not None:
            raise self.model_profile_error
        self.saved_model_profile = (name, profile, source_name)

    def delete_model_profile(self, _name: str) -> None:
        if self.model_profile_delete_error is not None:
            raise self.model_profile_delete_error
        return None

    def save_model_config(self, config: ModelConfigPayload) -> None:
        if self.model_config_error is not None:
            raise self.model_config_error
        self.saved_model_config = config.model_dump(mode="json")

    def save_model_fallback_config(self, config: ModelFallbackConfig) -> None:
        self.saved_model_fallback_config = config.model_dump(mode="json")

    def reload_model_config(self) -> None:
        if self.model_reload_error is not None:
            raise self.model_reload_error
        return None

    def reload_proxy_config(self) -> None:
        if self.proxy_reload_error is not None:
            raise self.proxy_reload_error
        return None

    def get_saved_proxy_config(self) -> dict[str, object]:
        return {
            "http_proxy": "http://proxy.example:8080",
            "https_proxy": None,
            "all_proxy": None,
            "no_proxy": "localhost,127.0.0.1",
            "proxy_username": "alice",
            "proxy_password": "secret",
            "ssl_verify": None,
        }

    def save_proxy_config(self, config: ProxyEnvInput) -> None:
        if self.proxy_save_error is not None:
            raise self.proxy_save_error
        self.saved_proxy_config = config.model_dump(mode="json")

    def get_web_config(self) -> WebConfig:
        return WebConfig(
            provider=WebProvider.EXA,
            exa_api_key=None,
            fallback_provider=WebFallbackProvider.SEARXNG,
            searxng_instance_url=DEFAULT_SEARXNG_INSTANCE_URL,
        )

    def list_agents(self) -> tuple[ExternalAgentSummary, ...]:
        return tuple(
            ExternalAgentSummary(
                agent_id=agent.agent_id,
                name=agent.name,
                description=agent.description,
                transport=agent.transport.transport,
            )
            for agent in self.external_agents.values()
        )

    def get_agent(self, agent_id: str) -> ExternalAgentConfig:
        return self.external_agents[agent_id]

    def save_agent(
        self,
        agent_id: str,
        config: ExternalAgentConfig,
    ) -> ExternalAgentConfig:
        self.external_agents[agent_id] = config
        return config

    def delete_agent(self, agent_id: str) -> None:
        self.external_agents.pop(agent_id)

    def resolve_runtime_agent(self, agent_id: str) -> ExternalAgentConfig:
        return self.external_agents[agent_id]

    def save_web_config(self, config: WebConfig) -> None:
        self.saved_web_config = config.model_dump(
            mode="json",
            exclude={"searxng_instance_seeds"},
        )

    def get_github_config(self) -> GitHubConfig:
        return GitHubConfig(
            token=self.current_github_token,
            webhook_base_url=self.current_github_webhook_base_url,
        )

    def get_github_config_view(self) -> GitHubConfigView:
        return GitHubConfigView(
            token_configured=self.current_github_token is not None,
            webhook_base_url=self.current_github_webhook_base_url,
        )

    def reveal_github_token(self) -> dict[str, str | None]:
        return {"token": self.current_github_token}

    def update_github_config(self, config: GitHubConfigUpdate) -> GitHubConfig:
        self.saved_github_config = config.model_dump(mode="json")
        if "token" in config.model_fields_set and config.token is not None:
            self.current_github_token = config.token
        if "webhook_base_url" in config.model_fields_set:
            self.current_github_webhook_base_url = config.webhook_base_url
        return GitHubConfig(
            token=self.current_github_token,
            webhook_base_url=self.current_github_webhook_base_url,
        )

    def get_status(self) -> LocalhostRunTunnelStatus:
        return self.tunnel_status

    def start(
        self, request: LocalhostRunTunnelStartRequest
    ) -> LocalhostRunTunnelStatus:
        self.started_tunnel_request = request.model_dump(mode="json")
        self.tunnel_status = LocalhostRunTunnelStatus(
            status="active",
            public_url="https://demo-tunnel.lhr.life",
            address="demo-tunnel.lhr.life",
            local_host=request.local_host or "127.0.0.1",
            local_port=request.local_port or 8000,
            pid=4321,
            started_at="2026-04-14T03:00:00Z",
            last_event="tcpip-forward",
            last_message="demo-tunnel.lhr.life tunneled with tls termination",
        )
        return self.tunnel_status

    def stop(self) -> LocalhostRunTunnelStatus:
        self.stopped_tunnel_request = {}
        self.tunnel_status = self.tunnel_status.model_copy(
            update={
                "status": "stopped",
                "stopped_at": "2026-04-14T03:05:00Z",
                "last_event": "stopped",
            }
        )
        return self.tunnel_status

    def refresh_repo_callback_urls_from_system_config(
        self,
        *,
        previous_webhook_base_url: str | None = None,
    ) -> tuple[object, ...]:
        self.refreshed_github_callback_previous_base_url = previous_webhook_base_url
        return ()

    def get_clawhub_config(self) -> ClawHubConfig:
        return ClawHubConfig(token=None)

    def save_clawhub_config(self, config: ClawHubConfig) -> None:
        self.saved_clawhub_config = config.model_dump(mode="json")

    def list_skills(self) -> tuple[ClawHubSkillSummary, ...]:
        return tuple(
            ClawHubSkillSummary(
                skill_id=skill.skill_id,
                runtime_name=skill.runtime_name,
                description=skill.description,
                ref=skill.ref,
                source=skill.source,
                directory=skill.directory,
                manifest_path=skill.manifest_path,
                valid=skill.valid,
                error=skill.error,
            )
            for skill in self.clawhub_skills.values()
        )

    def get_skill(self, skill_id: str) -> ClawHubSkillDetail:
        return self.clawhub_skills[skill_id]

    def save_skill(
        self,
        skill_id: str,
        request: ClawHubSkillWriteRequest,
    ) -> ClawHubSkillDetail:
        skill = ClawHubSkillDetail(
            skill_id=skill_id,
            runtime_name=request.runtime_name,
            description=request.description,
            ref=request.runtime_name,
            source=SkillSource.USER_RELAY_TEAMS,
            directory=f"/tmp/.relay-teams/skills/{skill_id}",
            manifest_path=f"/tmp/.relay-teams/skills/{skill_id}/SKILL.md",
            valid=True,
            error=None,
            instructions=request.instructions,
            manifest_content=None,
            files=request.files,
        )
        self.clawhub_skills[skill_id] = skill
        return skill

    def delete_skill(self, skill_id: str) -> None:
        self.clawhub_skills.pop(skill_id)

    def reload_mcp_config(self) -> None:
        if self.mcp_reload_error is not None:
            raise self.mcp_reload_error
        return None

    def reload_skills_config(self) -> None:
        if self.skills_reload_error is not None:
            raise self.skills_reload_error
        return None

    def get_notification_config(self) -> NotificationConfig:
        return NotificationConfig.model_validate(
            {
                "tool_approval_requested": {
                    "enabled": True,
                    "channels": ["browser", "toast"],
                    "feishu_format": "text",
                },
                "run_completed": {
                    "enabled": False,
                    "channels": ["toast"],
                    "feishu_format": "text",
                },
                "run_failed": {
                    "enabled": True,
                    "channels": ["browser", "toast"],
                    "feishu_format": "text",
                },
                "run_stopped": {
                    "enabled": False,
                    "channels": ["toast"],
                    "feishu_format": "text",
                },
            }
        )

    def save_notification_config(self, config: NotificationConfig) -> None:
        self.saved_notification_config = config.model_dump(mode="json")

    def get_orchestration_config(self) -> OrchestrationSettings:
        return OrchestrationSettings.model_validate(
            {
                "default_orchestration_preset_id": "default",
                "presets": [
                    {
                        "preset_id": "default",
                        "name": "Default",
                        "description": "General delegation flow.",
                        "role_ids": ["writer", "reviewer"],
                        "orchestration_prompt": "Delegate by capability and keep the final answer concise.",
                    }
                ],
            }
        )

    def save_orchestration_config(self, config: OrchestrationSettings) -> None:
        self.saved_orchestration_config = config.model_dump(mode="json")

    def get_provider_models(
        self,
        *,
        provider: ProviderType | None = None,
    ) -> tuple[ProviderModelInfo, ...]:
        models = (
            ProviderModelInfo(
                profile="default",
                provider=ProviderType.OPENAI_COMPATIBLE,
                model="gpt-4o-mini",
                base_url="https://example.com/v1",
                input_modalities=(MediaModality.IMAGE,),
            ),
            ProviderModelInfo(
                profile="glm",
                provider=ProviderType.BIGMODEL,
                model="glm-4.5",
                base_url="https://open.bigmodel.cn/api/coding/paas/v4",
                input_modalities=(MediaModality.IMAGE,),
            ),
            ProviderModelInfo(
                profile="echo",
                provider=ProviderType.ECHO,
                model="echo",
                base_url="http://localhost",
            ),
        )
        if provider is None:
            return models
        return tuple(model for model in models if model.provider == provider)

    def probe_connectivity(
        self,
        request: object,
    ) -> (
        ModelConnectivityProbeResult
        | GitHubConnectivityProbeResult
        | ClawHubConnectivityProbeResult
        | SshProfileConnectivityProbeResult
    ):
        if isinstance(request, SshProfileConnectivityProbeRequest):
            ssh_profile_id = request.ssh_profile_id or "draft"
            record = self.ssh_profiles.get(ssh_profile_id)
            return SshProfileConnectivityProbeResult.model_validate(
                {
                    "ok": True,
                    "ssh_profile_id": request.ssh_profile_id,
                    "host": (
                        request.override.host
                        if request.override is not None
                        else record.host
                        if record is not None
                        else "draft-host"
                    ),
                    "port": (
                        request.override.port
                        if request.override is not None
                        else record.port
                        if record is not None
                        else None
                    ),
                    "username": (
                        request.override.username
                        if request.override is not None
                        else record.username
                        if record is not None
                        else None
                    ),
                    "latency_ms": 44,
                    "checked_at": "2026-04-21T00:00:00Z",
                    "diagnostics": {
                        "binary_available": True,
                        "host_reachable": True,
                        "used_password": False,
                        "used_private_key": False,
                        "used_system_config": True,
                        "exit_code": 0,
                    },
                    "retryable": False,
                }
            )
        if isinstance(request, ClawHubConnectivityProbeRequest):
            return ClawHubConnectivityProbeResult.model_validate(
                {
                    "ok": True,
                    "clawhub_path": "/usr/bin/clawhub",
                    "clawhub_version": "clawhub 0.4.2",
                    "exit_code": 0,
                    "latency_ms": 37,
                    "checked_at": "2026-04-09T08:00:00Z",
                    "diagnostics": {
                        "binary_available": True,
                        "token_configured": True,
                    },
                    "retryable": False,
                }
            )
        if isinstance(request, GitHubConnectivityProbeRequest):
            return GitHubConnectivityProbeResult.model_validate(
                {
                    "ok": True,
                    "username": "octocat",
                    "host": "github.com",
                    "gh_path": "/tmp/gh",
                    "gh_version": "2.88.1",
                    "status_code": 200,
                    "exit_code": 0,
                    "latency_ms": 51,
                    "checked_at": "2026-03-12T00:00:00Z",
                    "diagnostics": {
                        "binary_available": True,
                        "auth_valid": True,
                        "used_proxy": False,
                        "bundled_binary": True,
                    },
                    "retryable": False,
                }
            )
        assert isinstance(request, ModelConnectivityProbeRequest)
        return ModelConnectivityProbeResult.model_validate(
            {
                "ok": True,
                "provider": ProviderType.OPENAI_COMPATIBLE.value,
                "model": "gpt-4o-mini",
                "latency_ms": 123,
                "checked_at": "2026-03-10T00:00:00Z",
                "diagnostics": {
                    "endpoint_reachable": True,
                    "auth_valid": True,
                    "rate_limited": False,
                },
                "token_usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 1,
                    "total_tokens": 9,
                },
                "retryable": False,
            }
        )

    def probe(
        self,
        request: object,
    ) -> (
        ModelConnectivityProbeResult
        | GitHubConnectivityProbeResult
        | GitHubWebhookConnectivityProbeResult
        | ClawHubConnectivityProbeResult
        | WebConnectivityProbeResult
        | SshProfileConnectivityProbeResult
    ):
        if isinstance(request, WebConnectivityProbeRequest):
            return self.probe_web_connectivity(request)
        if isinstance(request, GitHubWebhookConnectivityProbeRequest):
            return self.probe_webhook_connectivity(request)
        return self.probe_connectivity(request)

    def discover_models(
        self,
        _request: object,
    ) -> ModelDiscoveryResult:
        return ModelDiscoveryResult.model_validate(
            {
                "ok": True,
                "provider": ProviderType.OPENAI_COMPATIBLE.value,
                "base_url": "https://example.test/v1",
                "latency_ms": 37,
                "checked_at": "2026-03-10T00:00:00Z",
                "diagnostics": {
                    "endpoint_reachable": True,
                    "auth_valid": True,
                    "rate_limited": False,
                },
                "models": ["fake-chat-model", "reasoning-model"],
                "retryable": False,
            }
        )

    def probe_webhook_connectivity(
        self,
        request: GitHubWebhookConnectivityProbeRequest,
    ) -> GitHubWebhookConnectivityProbeResult:
        return GitHubWebhookConnectivityProbeResult.model_validate(
            {
                "ok": True,
                "webhook_base_url": request.webhook_base_url,
                "callback_url": "https://agent-teams.example.com/api/triggers/github/deliveries",
                "health_url": "https://agent-teams.example.com/api/system/health",
                "final_url": "https://agent-teams.example.com/api/system/health",
                "status_code": 200,
                "latency_ms": 44,
                "checked_at": "2026-04-13T08:00:00Z",
                "diagnostics": {
                    "endpoint_reachable": True,
                    "used_proxy": False,
                    "redirected": False,
                },
                "retryable": False,
                "error_code": None,
                "error_message": None,
            }
        )

    def probe_web_connectivity(
        self,
        _request: object,
    ) -> WebConnectivityProbeResult:
        return WebConnectivityProbeResult.model_validate(
            {
                "ok": True,
                "url": "https://example.com",
                "final_url": "https://example.com",
                "status_code": 200,
                "latency_ms": 88,
                "checked_at": "2026-03-12T00:00:00Z",
                "used_method": "HEAD",
                "diagnostics": {
                    "endpoint_reachable": True,
                    "used_proxy": True,
                    "redirected": False,
                },
                "retryable": False,
            }
        )

    def list_profiles(self) -> tuple[SshProfileRecord, ...]:
        return tuple(self.ssh_profiles.values())

    def get_profile(self, ssh_profile_id: str) -> SshProfileRecord:
        return self.ssh_profiles[ssh_profile_id]

    def reveal_password(self, ssh_profile_id: str) -> SshProfilePasswordRevealView:
        if ssh_profile_id not in self.ssh_profiles:
            raise KeyError(ssh_profile_id)
        return SshProfilePasswordRevealView(
            password=self.ssh_profile_passwords.get(ssh_profile_id)
        )

    def save_profile(
        self,
        *,
        ssh_profile_id: str,
        config: SshProfileConfig,
    ) -> SshProfileRecord:
        existing = self.ssh_profiles.get(ssh_profile_id)
        has_password = config.password is not None or (
            existing.has_password if existing is not None else False
        )
        has_private_key = config.private_key is not None or (
            existing.has_private_key if existing is not None else False
        )
        record = SshProfileRecord(
            ssh_profile_id=ssh_profile_id,
            host=config.host,
            username=config.username,
            port=config.port,
            remote_shell=config.remote_shell,
            connect_timeout_seconds=config.connect_timeout_seconds,
            has_password=has_password,
            has_private_key=has_private_key,
            private_key_name=(
                config.private_key_name
                if config.private_key is not None
                else (
                    existing.private_key_name
                    if existing is not None and has_private_key
                    else None
                )
            ),
            created_at=(
                existing.created_at
                if existing is not None
                else SshProfileRecord(
                    ssh_profile_id=ssh_profile_id,
                    host=config.host,
                ).created_at
            ),
        )
        self.ssh_profiles[ssh_profile_id] = record
        if config.password is not None:
            self.ssh_profile_passwords[ssh_profile_id] = config.password
        return record

    def delete_profile(self, ssh_profile_id: str) -> None:
        if ssh_profile_id not in self.ssh_profiles:
            raise KeyError(ssh_profile_id)
        del self.ssh_profiles[ssh_profile_id]
        self.ssh_profile_passwords.pop(ssh_profile_id, None)


class _AsyncWebProbeAdapter:
    def __init__(self, delegate: _FakeSystemService) -> None:
        self._delegate = delegate

    async def probe(
        self, request: WebConnectivityProbeRequest
    ) -> WebConnectivityProbeResult:
        result = self._delegate.probe(request)
        assert isinstance(result, WebConnectivityProbeResult)
        return result


def _create_test_client(fake_service: object) -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    app.dependency_overrides[get_config_status_service] = lambda: fake_service
    app.dependency_overrides[get_model_config_service] = lambda: fake_service
    app.dependency_overrides[get_notification_settings_service] = lambda: fake_service
    app.dependency_overrides[get_orchestration_settings_service] = lambda: fake_service
    app.dependency_overrides[get_mcp_config_reload_service] = lambda: fake_service
    app.dependency_overrides[get_skills_config_reload_service] = lambda: fake_service
    app.dependency_overrides[get_proxy_config_service] = lambda: fake_service
    app.dependency_overrides[get_web_connectivity_probe_service] = lambda: (
        _AsyncWebProbeAdapter(fake_service)
        if isinstance(fake_service, _FakeSystemService)
        else fake_service
    )
    app.dependency_overrides[get_github_connectivity_probe_service] = lambda: (
        fake_service
    )
    app.dependency_overrides[get_github_webhook_connectivity_probe_service] = lambda: (
        fake_service
    )
    app.dependency_overrides[get_clawhub_connectivity_probe_service] = lambda: (
        fake_service
    )
    app.dependency_overrides[get_ssh_profile_service] = lambda: fake_service
    app.dependency_overrides[get_ui_language_settings_service] = lambda: fake_service
    app.dependency_overrides[get_web_config_service] = lambda: fake_service
    app.dependency_overrides[get_clawhub_config_service] = lambda: fake_service
    app.dependency_overrides[get_clawhub_skill_service] = lambda: fake_service
    app.dependency_overrides[get_github_config_service] = lambda: fake_service
    app.dependency_overrides[get_localhost_run_tunnel_service] = lambda: fake_service
    app.dependency_overrides[get_github_trigger_service] = lambda: fake_service
    app.dependency_overrides[get_external_agent_config_service] = lambda: fake_service
    return TestClient(app)


def test_health_check_returns_runtime_identity_and_skill_sanity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == "0.1.0"
    assert payload["python_executable"]
    assert payload["package_root"]
    assert payload["config_dir"]
    assert payload["builtin_roles_dir"]
    assert payload["builtin_skills_dir"]
    role_registry_sanity = payload["role_registry_sanity"]
    assert role_registry_sanity["builtin_role_count"] >= 1
    assert role_registry_sanity["has_builtin_coordinator"] is True
    assert role_registry_sanity["has_builtin_main_agent"] is True
    skill_registry_sanity = payload["skill_registry_sanity"]
    assert skill_registry_sanity["builtin_skill_count"] >= 1
    assert "deepresearch" in skill_registry_sanity["builtin_skill_names"]
    assert skill_registry_sanity["has_builtin_deepresearch"] is True
    tool_registry_sanity = payload["tool_registry_sanity"]
    assert tool_registry_sanity["available_tool_count"] >= 1
    assert "write" in tool_registry_sanity["available_tool_names"]


def test_get_notification_config() -> None:
    client = _create_test_client(_FakeSystemService())
    response = client.get("/api/system/configs/notifications")
    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_approval_requested"]["enabled"] is True
    assert payload["run_completed"]["channels"] == ["toast"]


def test_get_ui_language_settings() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/ui-language")

    assert response.status_code == 200
    assert response.json() == {"language": "zh-CN"}


def test_list_and_get_ssh_profiles() -> None:
    client = _create_test_client(_FakeSystemService())

    list_response = client.get("/api/system/configs/workspace/ssh-profiles")
    get_response = client.get("/api/system/configs/workspace/ssh-profiles/prod")

    assert list_response.status_code == 200
    assert list_response.json()[0]["ssh_profile_id"] == "prod"
    assert list_response.json()[0]["host"] == "prod-alias"
    assert list_response.json()[0]["has_password"] is True
    assert get_response.status_code == 200
    assert get_response.json()["username"] == "deploy"
    assert get_response.json()["has_private_key"] is True
    assert get_response.json()["private_key_name"] == "id_ed25519"


def test_reveal_ssh_profile_password() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/workspace/ssh-profiles/prod:reveal-password"
    )

    assert response.status_code == 200
    assert response.json() == {"password": "relay-secret"}


def test_probe_ssh_profile_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/workspace/ssh-profiles:probe",
        json={"ssh_profile_id": "prod"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ssh_profile_id"] == "prod"
    assert payload["host"] == "prod-alias"
    assert payload["latency_ms"] == 44


def test_save_and_delete_ssh_profile() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    save_response = client.put(
        "/api/system/configs/workspace/ssh-profiles/staging",
        json={
            "config": {
                "host": "staging-alias",
                "username": "ops",
                "password": "relay-secret",
                "port": 2222,
                "remote_shell": "/bin/bash",
                "connect_timeout_seconds": 15,
                "private_key": "-----BEGIN KEY-----\ncontent\n-----END KEY-----",
                "private_key_name": "id_rsa",
            }
        },
    )

    assert save_response.status_code == 200
    assert save_response.json()["ssh_profile_id"] == "staging"
    assert save_response.json()["has_password"] is True
    assert save_response.json()["has_private_key"] is True
    assert save_response.json()["private_key_name"] == "id_rsa"
    assert service.ssh_profiles["staging"].remote_shell == "/bin/bash"

    delete_response = client.delete(
        "/api/system/configs/workspace/ssh-profiles/staging"
    )

    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "ok"}
    assert "staging" not in service.ssh_profiles


def test_save_ui_language_settings() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/ui-language",
        json={"language": "en-US"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_ui_language_settings == {"language": "en-US"}


def test_save_model_profile_includes_connect_timeout_seconds() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": 2048,
            "context_window": 128000,
            "fallback_policy_id": "same_provider_then_other_provider",
            "fallback_priority": 5,
            "connect_timeout_seconds": 25.0,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_profile is not None
    _, saved_profile, source_name = service.saved_model_profile
    assert saved_profile["connect_timeout_seconds"] == 25.0
    assert saved_profile["context_window"] == 128000
    assert saved_profile["fallback_policy_id"] == ("same_provider_then_other_provider")
    assert saved_profile["fallback_priority"] == 5
    assert source_name is None


def test_save_model_profile_omits_fallback_settings_when_not_provided() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "temperature": 0.2,
            "top_p": 1.0,
            "context_window": 128000,
            "connect_timeout_seconds": 25.0,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert "fallback_policy_id" not in saved_profile
    assert "fallback_priority" not in saved_profile


def test_get_model_fallback_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/model-fallback")

    assert response.status_code == 200
    payload = response.json()
    policies = cast(list[dict[str, object]], payload["policies"])
    assert policies[0]["policy_id"] == "same_provider_then_other_provider"


def test_save_model_fallback_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model-fallback",
        json={
            "policies": [
                {
                    "policy_id": "same_provider_then_other_provider",
                    "name": "Same Provider Then Other Provider",
                    "enabled": True,
                    "trigger": "rate_limit_after_retries",
                    "strategy": "same_provider_then_other_provider",
                    "max_hops": 4,
                    "cooldown_seconds": 90,
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_fallback_config is not None
    saved_policies = cast(
        list[dict[str, object]],
        service.saved_model_fallback_config["policies"],
    )
    assert saved_policies[0]["max_hops"] == 4


def test_save_notification_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)
    request_payload = {
        "tool_approval_requested": {
            "enabled": True,
            "channels": ["browser", "toast"],
            "feishu_format": "text",
        },
        "run_completed": {
            "enabled": True,
            "channels": ["toast", "feishu"],
            "feishu_format": "card",
        },
        "run_failed": {
            "enabled": True,
            "channels": ["browser", "toast"],
            "feishu_format": "text",
        },
        "run_stopped": {
            "enabled": True,
            "channels": ["toast"],
            "feishu_format": "text",
        },
    }
    response = client.put("/api/system/configs/notifications", json=request_payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_notification_config is not None
    run_completed = service.saved_notification_config["run_completed"]
    assert isinstance(run_completed, dict)
    assert run_completed["enabled"] is True
    assert run_completed["feishu_format"] == "card"


def test_get_orchestration_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/orchestration")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_orchestration_preset_id"] == "default"
    assert payload["presets"][0]["role_ids"] == ["writer", "reviewer"]


def test_save_github_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/github",
        json={
            "token": "ghp_secret",
            "webhook_base_url": "https://agent-teams.example.com",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_github_config == {
        "token": "ghp_secret",
        "webhook_base_url": "https://agent-teams.example.com",
    }
    assert service.refreshed_github_callback_previous_base_url is None


def test_save_github_config_rejects_removed_clear_token_field() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.put(
        "/api/system/configs/github",
        json={"clear_token": True},
    )

    assert response.status_code == 422


def test_get_github_config_hides_token_value() -> None:
    service = _FakeSystemService()
    service.current_github_token = "ghp_saved"
    client = _create_test_client(service)

    response = client.get("/api/system/configs/github")

    assert response.status_code == 200
    assert response.json() == {
        "token_configured": True,
        "webhook_base_url": None,
    }


def test_reveal_github_token() -> None:
    service = _FakeSystemService()
    service.current_github_token = "ghp_saved"
    client = _create_test_client(service)

    response = client.post("/api/system/configs/github:reveal")

    assert response.status_code == 200
    assert response.json() == {"token": "ghp_saved"}


def test_probe_github_webhook_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/github/webhook:probe",
        json={"webhook_base_url": "https://agent-teams.example.com"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "webhook_base_url": "https://agent-teams.example.com",
        "callback_url": "https://agent-teams.example.com/api/triggers/github/deliveries",
        "health_url": "https://agent-teams.example.com/api/system/health",
        "final_url": "https://agent-teams.example.com/api/system/health",
        "status_code": 200,
        "latency_ms": 44,
        "checked_at": "2026-04-13T08:00:00Z",
        "diagnostics": {
            "endpoint_reachable": True,
            "used_proxy": False,
            "redirected": False,
        },
        "retryable": False,
        "error_code": None,
        "error_message": None,
    }


def test_get_github_webhook_tunnel_status() -> None:
    service = _FakeSystemService()
    service.tunnel_status = LocalhostRunTunnelStatus(
        status="active",
        public_url="https://demo-tunnel.lhr.life",
        address="demo-tunnel.lhr.life",
        local_host="127.0.0.1",
        local_port=8000,
        pid=4321,
        started_at="2026-04-14T03:00:00Z",
        last_event="tcpip-forward",
        last_message="demo-tunnel.lhr.life tunneled with tls termination",
    )
    client = _create_test_client(service)

    response = client.get("/api/system/configs/github/webhook/tunnel")

    assert response.status_code == 200
    assert response.json()["public_url"] == "https://demo-tunnel.lhr.life"
    assert response.json()["status"] == "active"


def test_start_github_webhook_tunnel_saves_webhook_base_url() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.post(
        "/api/system/configs/github/webhook/tunnel:start",
        json={"auto_save_webhook_base_url": True},
    )

    assert response.status_code == 200
    assert response.json()["public_url"] == "https://demo-tunnel.lhr.life"
    assert service.started_tunnel_request == {
        "local_host": None,
        "local_port": 8000,
        "wait_timeout_ms": 15000,
        "auto_save_webhook_base_url": True,
    }
    assert service.saved_github_config == {
        "token": None,
        "webhook_base_url": "https://demo-tunnel.lhr.life",
    }
    assert service.current_github_webhook_base_url == "https://demo-tunnel.lhr.life"
    assert service.refreshed_github_callback_previous_base_url is None


def test_stop_github_webhook_tunnel_clears_matching_webhook_base_url() -> None:
    service = _FakeSystemService()
    service.current_github_webhook_base_url = "https://demo-tunnel.lhr.life"
    service.tunnel_status = LocalhostRunTunnelStatus(
        status="active",
        public_url="https://demo-tunnel.lhr.life",
        address="demo-tunnel.lhr.life",
        local_host="127.0.0.1",
        local_port=8000,
        pid=4321,
        started_at="2026-04-14T03:00:00Z",
    )
    client = _create_test_client(service)

    response = client.post(
        "/api/system/configs/github/webhook/tunnel:stop",
        json={"clear_webhook_base_url_if_matching": True},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert service.saved_github_config == {"token": None, "webhook_base_url": None}
    assert service.current_github_webhook_base_url is None
    assert (
        service.refreshed_github_callback_previous_base_url
        == "https://demo-tunnel.lhr.life"
    )


def test_get_clawhub_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/clawhub")

    assert response.status_code == 200
    assert response.json() == {"token": None}


def test_save_clawhub_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/clawhub",
        json={"token": "ch_secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_clawhub_config == {"token": "ch_secret"}


def test_probe_clawhub_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/clawhub:probe",
        json={"token": "ch_secret", "timeout_ms": 2500},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["clawhub_version"] == "clawhub 0.4.2"


def test_probe_github_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/github:probe",
        json={"token": "ghp_secret", "timeout_ms": 2500},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["username"] == "octocat"


def test_list_clawhub_skills() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/clawhub/skills")

    assert response.status_code == 200
    assert response.json() == [
        {
            "skill_id": "skill-creator-2",
            "runtime_name": "skill-creator",
            "description": "Create Codex skills.",
            "ref": "skill-creator",
            "source": "user_relay_teams",
            "directory": "/tmp/.relay-teams/skills/skill-creator-2",
            "manifest_path": "/tmp/.relay-teams/skills/skill-creator-2/SKILL.md",
            "valid": True,
            "error": None,
        }
    ]


def test_save_and_delete_clawhub_skill() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    save_response = client.put(
        "/api/system/configs/clawhub/skills/demo-skill",
        json={
            "runtime_name": "demo-skill",
            "description": "Demo skill",
            "instructions": "Use with care.",
            "files": [
                {
                    "path": "scripts/run.py",
                    "content": "print('ok')\n",
                    "encoding": "utf-8",
                }
            ],
        },
    )
    delete_response = client.delete("/api/system/configs/clawhub/skills/demo-skill")

    assert save_response.status_code == 200
    assert save_response.json()["skill_id"] == "demo-skill"
    assert save_response.json()["runtime_name"] == "demo-skill"
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "ok"}


def test_save_orchestration_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/orchestration",
        json={
            "default_orchestration_preset_id": "shipping",
            "presets": [
                {
                    "preset_id": "shipping",
                    "name": "Shipping",
                    "description": "Release work.",
                    "role_ids": ["writer"],
                    "orchestration_prompt": "Use writer for outward-facing updates.",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_orchestration_config is not None
    assert service.saved_orchestration_config["default_orchestration_preset_id"] == (
        "shipping"
    )


def test_save_orchestration_config_accepts_legacy_wrapper_payload() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/orchestration",
        json={
            "config": {
                "default_orchestration_preset_id": "shipping",
                "presets": [
                    {
                        "preset_id": "shipping",
                        "name": "Shipping",
                        "description": "Release work.",
                        "role_ids": ["writer"],
                        "orchestration_prompt": "Use writer for outward-facing updates.",
                    }
                ],
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_orchestration_config is not None
    assert service.saved_orchestration_config["default_orchestration_preset_id"] == (
        "shipping"
    )


def test_get_provider_models() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/model/providers/models")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 3
    assert payload[0]["profile"] == "default"
    assert payload[0]["input_modalities"] == ["image"]
    assert payload[0]["capabilities"]["input"]["image"] is True


def test_get_model_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/model")

    assert response.status_code == 200
    assert response.json() == {
        "default": {
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "headers": [],
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": 2048,
            "context_window": 128000,
            "connect_timeout_seconds": 25.0,
            "is_default": True,
            "maas_auth": None,
            "ssl_verify": None,
        }
    }


def test_save_model_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model",
        json={
            "default": {
                "provider": "openai_compatible",
                "model": "gpt-4o-mini",
                "base_url": "https://example.test/v1",
                "api_key": "secret",
                "headers": [],
                "temperature": 0.2,
                "top_p": 1.0,
                "max_tokens": 2048,
                "context_window": 128000,
                "connect_timeout_seconds": 25.0,
                "is_default": True,
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_config is not None
    saved_default = cast(dict[str, object], service.saved_model_config["default"])
    assert saved_default["provider"] == "openai_compatible"
    assert saved_default["model"] == "gpt-4o-mini"
    assert saved_default["base_url"] == "https://example.test/v1"
    assert saved_default["api_key"] == "secret"
    assert saved_default["headers"] == []
    assert saved_default["temperature"] == 0.2
    assert saved_default["top_p"] == 1.0
    assert saved_default["max_tokens"] == 2048
    assert saved_default["context_window"] == 128000
    assert saved_default["connect_timeout_seconds"] == 25.0
    assert saved_default["is_default"] is True
    assert saved_default["fallback_policy_id"] is None
    assert saved_default["fallback_priority"] == 0
    assert saved_default["maas_auth"] is None
    assert saved_default["ssl_verify"] is None


def test_save_model_config_accepts_legacy_wrapper_payload() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)
    response = client.put(
        "/api/system/configs/model",
        json={
            "config": {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret",
                }
            }
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_config is not None
    saved_default = cast(dict[str, object], service.saved_model_config["default"])
    assert saved_default["model"] == "gpt-4o-mini"


def test_save_model_config_rejects_unknown_profile_field() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.put(
        "/api/system/configs/model",
        json={
            "default": {
                "provider": "openai_compatible",
                "model": "gpt-4o-mini",
                "base_url": "https://example.test/v1",
                "api_key": "secret",
                "temperature": 0.2,
                "top_p": 1.0,
                "unexpected": True,
            }
        },
    )

    assert response.status_code == 422


def test_get_model_profiles_returns_api_key() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/model/profiles")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default"]["api_key"] == "secret"
    assert payload["default"]["has_api_key"] is True
    assert payload["default"]["is_default"] is True
    assert payload["default"]["context_window"] == 128000
    assert payload["default"]["fallback_policy_id"] == (
        "same_provider_then_other_provider"
    )
    assert payload["default"]["fallback_priority"] == 3
    assert payload["default"]["input_modalities"] == ["image"]
    assert payload["default"]["capabilities"]["input"]["image"] is True


def test_get_model_profiles_returns_maas_password() -> None:
    class _FakeMaaSSystemService(_FakeSystemService):
        def get_model_profiles(self) -> dict[str, object]:
            return {
                "maas": {
                    "provider": ProviderType.MAAS.value,
                    "model": "maas-chat",
                    "base_url": DEFAULT_MAAS_BASE_URL,
                    "api_key": "",
                    "has_api_key": False,
                    "headers": [],
                    "maas_auth": {
                        "username": "relay-user",
                        "password": "relay-password",
                        "has_password": True,
                    },
                    "is_default": True,
                    "capabilities": {
                        "input": {
                            "text": True,
                            "image": None,
                            "audio": None,
                            "video": None,
                            "pdf": None,
                        },
                        "output": {
                            "text": True,
                            "image": None,
                            "audio": None,
                            "video": None,
                            "pdf": None,
                        },
                    },
                    "input_modalities": [],
                }
            }

    client = _create_test_client(_FakeMaaSSystemService())

    response = client.get("/api/system/configs/model/profiles")

    assert response.status_code == 200
    payload = response.json()
    assert payload["maas"]["maas_auth"]["username"] == "relay-user"
    assert payload["maas"]["maas_auth"]["password"] == "relay-password"
    assert payload["maas"]["maas_auth"]["has_password"] is True
    assert payload["maas"]["capabilities"]["input"]["image"] is None


def test_get_provider_models_with_filter() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get(
        "/api/system/configs/model/providers/models",
        params={"provider": ProviderType.ECHO.value},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["provider"] == ProviderType.ECHO.value


def test_probe_model_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/model:probe",
        json={"profile_name": "default"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["latency_ms"] == 123
    assert payload["token_usage"]["total_tokens"] == 9


def test_discover_model_catalog() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/model:discover",
        json={
            "override": {
                "base_url": "https://example.test/v1",
                "api_key": "secret",
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["latency_ms"] == 37
    assert payload["models"] == ["fake-chat-model", "reasoning-model"]


def test_reload_model_config_returns_bad_request_for_invalid_config() -> None:
    service = _FakeSystemService()
    service.model_reload_error = ValueError("Invalid model config")
    client = _create_test_client(service)

    response = client.post("/api/system/configs/model:reload")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid model config"}


def test_reload_proxy_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post("/api/system/configs/proxy:reload")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_proxy_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/proxy")

    assert response.status_code == 200
    assert response.json() == {
        "http_proxy": "http://proxy.example:8080",
        "https_proxy": None,
        "all_proxy": None,
        "no_proxy": "localhost,127.0.0.1",
        "proxy_username": "alice",
        "proxy_password": "secret",
        "ssl_verify": None,
    }


def test_get_web_config() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/web")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "exa",
        "exa_api_key": None,
        "fallback_provider": "searxng",
        "searxng_instance_url": DEFAULT_SEARXNG_INSTANCE_URL,
        "searxng_instance_seeds": list(DEFAULT_SEARXNG_INSTANCE_SEEDS),
    }


def test_save_proxy_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/proxy",
        json={
            "http_proxy": "http://proxy.example:8080",
            "https_proxy": "http://proxy.example:8443",
            "all_proxy": "",
            "no_proxy": "localhost,127.0.0.1",
            "proxy_username": "alice",
            "proxy_password": "secret",
            "ssl_verify": None,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_proxy_config == {
        "http_proxy": "http://proxy.example:8080",
        "https_proxy": "http://proxy.example:8443",
        "all_proxy": "",
        "no_proxy": "localhost,127.0.0.1",
        "proxy_username": "alice",
        "proxy_password": "secret",
        "ssl_verify": None,
    }


def test_save_web_config() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/web",
        json={
            "provider": "exa",
            "exa_api_key": "secret",
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.example.test/",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_web_config == {
        "provider": "exa",
        "exa_api_key": "secret",
        "fallback_provider": "searxng",
        "searxng_instance_url": "https://search.example.test/",
    }


def test_save_web_config_rejects_searxng_primary_provider() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.put(
        "/api/system/configs/web",
        json={
            "provider": "searxng",
            "exa_api_key": None,
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.example.test/",
        },
    )

    assert response.status_code == 422


def test_save_web_config_accepts_disabled_fallback_provider() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/web",
        json={
            "provider": "exa",
            "exa_api_key": "secret",
            "fallback_provider": "disabled",
            "searxng_instance_url": DEFAULT_SEARXNG_INSTANCE_URL,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_web_config == {
        "provider": "exa",
        "exa_api_key": "secret",
        "fallback_provider": "disabled",
        "searxng_instance_url": DEFAULT_SEARXNG_INSTANCE_URL,
    }


def test_list_external_agents() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/agents")

    assert response.status_code == 200
    assert response.json() == [
        {
            "agent_id": "codex_local",
            "name": "Codex Local",
            "description": "Runs Codex via stdio",
            "transport": "stdio",
        }
    ]


def test_get_external_agent_omits_stdio_working_directory() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.get("/api/system/configs/agents/codex_local")

    assert response.status_code == 200
    assert response.json() == {
        "agent_id": "codex_local",
        "name": "Codex Local",
        "description": "Runs Codex via stdio",
        "transport": {
            "transport": "stdio",
            "command": "codex",
            "args": ["--serve"],
            "env": [],
        },
    }


def test_save_external_agent() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/agents/claude_http",
        json={
            "agent_id": "claude_http",
            "name": "Claude HTTP",
            "description": "Runs Claude over HTTP",
            "transport": {
                "transport": "streamable_http",
                "url": "http://127.0.0.1:4100/acp",
                "headers": [],
                "ssl_verify": True,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["agent_id"] == "claude_http"
    assert (
        service.external_agents["claude_http"].transport.transport == "streamable_http"
    )


def test_test_external_agent(monkeypatch) -> None:
    async def fake_probe(_config: ExternalAgentConfig) -> ExternalAgentTestResult:
        return ExternalAgentTestResult(
            ok=True,
            message="Connected",
            agent_name="Codex",
            agent_version="1.0.0",
            protocol_version=1,
        )

    monkeypatch.setattr(system, "probe_acp_agent", fake_probe)
    client = _create_test_client(_FakeSystemService())

    response = client.post("/api/system/configs/agents/codex_local:test")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "message": "Connected",
        "agent_name": "Codex",
        "agent_version": "1.0.0",
        "protocol_version": 1,
    }


def test_save_proxy_config_returns_user_error_for_missing_keyring() -> None:
    service = _FakeSystemService()
    service.proxy_save_error = RuntimeError(
        "Proxy password persistence requires a usable system keyring backend."
    )
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/proxy",
        json={
            "https_proxy": "http://proxy.example:8443",
            "proxy_username": "alice",
            "proxy_password": "secret",
        },
    )

    assert response.status_code == 400
    assert "system keyring backend" in response.json()["detail"]


def test_reload_proxy_config_returns_bad_request_for_invalid_config() -> None:
    service = _FakeSystemService()
    service.proxy_reload_error = RuntimeError("Invalid proxy config")
    client = _create_test_client(service)

    response = client.post("/api/system/configs/proxy:reload")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid proxy config"}


def test_reload_mcp_config_returns_bad_request_for_invalid_config() -> None:
    service = _FakeSystemService()
    service.mcp_reload_error = ValueError("Invalid MCP config")
    client = _create_test_client(service)

    response = client.post("/api/system/configs/mcp:reload")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid MCP config"}


def test_reload_skills_config_returns_bad_request_for_invalid_config() -> None:
    service = _FakeSystemService()
    service.skills_reload_error = RuntimeError("Invalid skills config")
    client = _create_test_client(service)

    response = client.post("/api/system/configs/skills:reload")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid skills config"}


def test_probe_web_connectivity() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/web:probe",
        json={"url": "https://example.com"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["used_method"] == "HEAD"
    assert payload["diagnostics"]["used_proxy"] is True


def test_probe_web_connectivity_accepts_proxy_override() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.post(
        "/api/system/configs/web:probe",
        json={
            "url": "https://example.com",
            "proxy_override": {
                "https_proxy": "http://proxy.example:8443",
                "no_proxy": "",
                "proxy_username": "alice",
                "proxy_password": "secret",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True


def test_save_model_profile_returns_not_found_for_missing_source_name() -> None:
    service = _FakeSystemService()
    service.model_profile_error = KeyError("Model profile not found: default")
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/renamed",
        json={
            "source_name": "default",
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 404
    assert "Model profile not found" in response.json()["detail"]


def test_save_model_profile_returns_bad_request_for_service_validation_error() -> None:
    service = _FakeSystemService()
    service.model_profile_error = ValueError("Header 'Authorization' requires a value.")
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 400
    assert "requires a value" in response.json()["detail"]


def test_delete_model_profile_returns_not_found_when_missing() -> None:
    service = _FakeSystemService()
    service.model_profile_delete_error = KeyError("Model profile not found: default")
    client = _create_test_client(service)

    response = client.delete("/api/system/configs/model/profiles/default")

    assert response.status_code == 404
    assert "Model profile not found" in response.json()["detail"]


def test_save_model_config_returns_bad_request_for_service_validation_error() -> None:
    service = _FakeSystemService()
    service.model_config_error = ValueError(
        "MAAS model profile requires maas_auth configuration."
    )
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model",
        json={
            "maas": {
                "provider": "maas",
                "model": "maas-chat",
                "base_url": "https://maas.example/api/v2",
                "temperature": 0.2,
                "top_p": 1.0,
            }
        },
    )

    assert response.status_code == 400
    assert "maas_auth" in response.json()["detail"]


def test_save_model_profile_allows_missing_api_key_for_edit() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_profile is not None
    saved_name, saved_profile, source_name = service.saved_model_profile
    assert saved_name == "default"
    assert "api_key" not in saved_profile
    assert saved_profile["top_p"] == 0.95
    assert source_name is None


def test_save_model_profile_omits_max_tokens_when_not_provided() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert "max_tokens" not in saved_profile


def test_save_model_profile_allows_clearing_max_tokens_with_null() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": None,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert "max_tokens" in saved_profile
    assert saved_profile["max_tokens"] is None


def test_save_model_profile_accepts_bigmodel_provider() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/glm",
        json={
            "provider": ProviderType.BIGMODEL.value,
            "model": "glm-4.5",
            "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
            "api_key": "secret",
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert saved_profile["provider"] == ProviderType.BIGMODEL.value


def test_save_model_profile_accepts_minimax_provider() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/minimax",
        json={
            "provider": ProviderType.MINIMAX.value,
            "model": "MiniMax-M1-80k",
            "base_url": "https://api.minimaxi.com/v1",
            "api_key": "secret",
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert saved_profile["provider"] == ProviderType.MINIMAX.value


def test_save_model_profile_accepts_maas_provider() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/maas",
        json={
            "provider": ProviderType.MAAS.value,
            "model": "maas-chat",
            "base_url": "https://maas.example/api/v2",
            "maas_auth": {
                "username": "relay-user",
                "password": "relay-password",
            },
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert saved_profile["provider"] == ProviderType.MAAS.value
    assert saved_profile["base_url"] == DEFAULT_MAAS_BASE_URL
    assert saved_profile["maas_auth"] == {
        "username": "relay-user",
        "password": "relay-password",
    }


def test_save_model_profile_accepts_source_name_for_rename() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/renamed",
        json={
            "source_name": "default",
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert service.saved_model_profile is not None
    saved_name, saved_profile, source_name = service.saved_model_profile
    assert saved_name == "renamed"
    assert saved_profile["model"] == "kimi-k2.5"
    assert source_name == "default"


def test_save_model_profile_includes_default_flag_when_present() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/kimi",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "kimi-k2.5",
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "secret",
            "is_default": True,
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    assert saved_profile["is_default"] is True


def test_save_model_profile_forwards_headers() -> None:
    service = _FakeSystemService()
    client = _create_test_client(service)

    response = client.put(
        "/api/system/configs/model/profiles/default",
        json={
            "provider": ProviderType.OPENAI_COMPATIBLE.value,
            "model": "claude-proxy",
            "base_url": "https://example.test/v1",
            "headers": [
                {
                    "name": "Authorization",
                    "value": "Bearer from-header",
                    "secret": True,
                }
            ],
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": 2048,
        },
    )

    assert response.status_code == 200
    assert service.saved_model_profile is not None
    _, saved_profile, _ = service.saved_model_profile
    saved_headers = saved_profile["headers"]
    assert isinstance(saved_headers, list)
    first_header = saved_headers[0]
    assert isinstance(first_header, dict)
    first_header_payload = first_header
    assert cast(dict[str, JsonValue], first_header_payload)["name"] == "Authorization"


class _FakeEnvironmentVariableService:
    def __init__(self) -> None:
        self.saved_payload: dict[str, str] | None = None
        self.deleted_key: tuple[str, str] | None = None
        self.permission_error: PermissionError | None = None

    def list_environment_variables(self) -> dict[str, object]:
        return {
            "system": [
                {
                    "key": "ComSpec",
                    "value": r"%SystemRoot%\\system32\\cmd.exe",
                    "scope": "system",
                    "value_kind": "expandable",
                }
            ],
            "app": [
                {
                    "key": "OPENAI_API_KEY",
                    "value": "secret",
                    "scope": "app",
                    "value_kind": "string",
                }
            ],
        }

    def save_environment_variable(
        self,
        *,
        scope: object,
        key: str,
        request: object,
    ) -> dict[str, str]:
        if self.permission_error is not None:
            raise self.permission_error
        source_key = getattr(request, "source_key")
        value = getattr(request, "value")
        self.saved_payload = {
            "scope": str(getattr(scope, "value", scope)),
            "key": key,
            "source_key": "" if source_key is None else str(source_key),
            "value": value,
        }
        return {
            "key": key,
            "value": value,
            "scope": str(getattr(scope, "value", scope)),
            "value_kind": "string",
        }

    def delete_environment_variable(self, *, scope: object, key: str) -> None:
        if self.permission_error is not None:
            raise self.permission_error
        self.deleted_key = (str(getattr(scope, "value", scope)), key)


def _create_env_test_client(fake_service: object) -> TestClient:
    app = FastAPI()
    app.include_router(system.router, prefix="/api")
    app.dependency_overrides[get_environment_variable_service] = lambda: fake_service
    return TestClient(app)


def test_get_environment_variables() -> None:
    client = _create_env_test_client(_FakeEnvironmentVariableService())

    response = client.get("/api/system/configs/environment-variables")

    assert response.status_code == 200
    payload = response.json()
    assert payload["system"][0]["key"] == "ComSpec"
    assert payload["app"][0]["scope"] == "app"


def test_save_environment_variable() -> None:
    service = _FakeEnvironmentVariableService()
    client = _create_env_test_client(service)

    response = client.put(
        "/api/system/configs/environment-variables/app/OPENAI_API_KEY",
        json={
            "source_key": "OPENAI_KEY",
            "value": "updated-secret",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "key": "OPENAI_API_KEY",
        "value": "updated-secret",
        "scope": "app",
        "value_kind": "string",
    }
    assert service.saved_payload == {
        "scope": "app",
        "key": "OPENAI_API_KEY",
        "source_key": "OPENAI_KEY",
        "value": "updated-secret",
    }


def test_delete_environment_variable_returns_forbidden_on_permission_error() -> None:
    service = _FakeEnvironmentVariableService()
    service.permission_error = PermissionError(
        "System-level environment access denied."
    )
    client = _create_env_test_client(service)

    response = client.delete(
        "/api/system/configs/environment-variables/system/Path",
    )

    assert response.status_code == 403
    assert "access denied" in response.json()["detail"].lower()


def test_save_notification_config_rejects_unknown_top_level_field() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.put(
        "/api/system/configs/notifications",
        json={
            "tool_approval_requested": {
                "enabled": True,
                "channels": ["browser", "toast"],
                "feishu_format": "text",
            },
            "run_completed": {
                "enabled": True,
                "channels": ["toast", "feishu"],
                "feishu_format": "card",
            },
            "run_failed": {
                "enabled": True,
                "channels": ["browser", "toast"],
                "feishu_format": "text",
            },
            "run_stopped": {
                "enabled": True,
                "channels": ["toast"],
                "feishu_format": "text",
            },
            "unexpected": {},
        },
    )

    assert response.status_code == 422


def test_save_orchestration_config_rejects_unknown_top_level_field() -> None:
    client = _create_test_client(_FakeSystemService())

    response = client.put(
        "/api/system/configs/orchestration",
        json={
            "default_orchestration_preset_id": "shipping",
            "presets": [
                {
                    "preset_id": "shipping",
                    "name": "Shipping",
                    "description": "Release work.",
                    "role_ids": ["writer"],
                    "orchestration_prompt": "Use writer for outward-facing updates.",
                }
            ],
            "unexpected": True,
        },
    )

    assert response.status_code == 422


def test_get_model_config_preserves_omitted_sparse_fields() -> None:
    class _SparseSystemService(_FakeSystemService):
        def get_model_config(self) -> dict[str, object]:
            return {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "secret",
                }
            }

    client = _create_test_client(_SparseSystemService())

    response = client.get("/api/system/configs/model")

    assert response.status_code == 200
    assert response.json() == {
        "default": {
            "provider": "openai_compatible",
            "model": "gpt-4o-mini",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
        }
    }

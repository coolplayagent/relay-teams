# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime
from json import dumps
from pathlib import Path
import re
from typing import Protocol

from pydantic import JsonValue, TypeAdapter, ValidationError

from relay_teams.connector.models import (
    ConnectorAuthType,
    ConnectorCategory,
    ConnectorHealthCheck,
    ConnectorItem,
    ConnectorProvider,
    ConnectorStatus,
    ConnectorTestResult,
)
from relay_teams.connector.w3_models import (
    W3ConnectorConfig,
    W3ConnectorSaveRequest,
    W3ConnectorSaveResponse,
    W3ConnectorStatusResponse,
    W3ConnectorSyncResponse,
    W3ConnectorTestRequest,
    W3ConnectorTestResponse,
    W3ModelImportFailure,
    W3ModelSyncSummary,
)
from relay_teams.providers.w3_auth_source import (
    W3_CONNECTOR_ID,
    W3_CONNECTOR_NAME,
    W3_PASSWORD_FIELD,
    W3_SECRET_NAMESPACE,
    W3_SECRET_OWNER_ID,
)
from relay_teams.logger import get_logger
from relay_teams.providers.maas_auth import (
    MaaSAuthContext,
    MaaSLoginError,
    get_maas_token_service,
    maas_password_secret_field_name,
)
from relay_teams.providers.codeagent_auth import codeagent_password_secret_field_name
from relay_teams.providers.model_config import (
    CodeAgentAuthConfig,
    CodeAgentAuthMethod,
    DEFAULT_CODEAGENT_BASE_URL,
    DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAAS_BASE_URL,
    MaaSAuthConfig,
    ModelCapabilities,
    ModelAuthSource,
    ProviderType,
)
from relay_teams.providers.model_connectivity import (
    ModelConnectivityProbeOverride,
    ModelDiscoveryEntry,
    ModelDiscoveryRequest,
    ModelDiscoveryResult,
)
from relay_teams.secrets import AppSecretStore, get_secret_store


LOGGER = get_logger(__name__)
_MODEL_PROFILE_SECRET_NAMESPACE = "model_profile"
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


class MaaSTokenServiceLike(Protocol):
    async def get_auth_context(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> MaaSAuthContext:
        raise NotImplementedError


class ModelConfigServiceLike(Protocol):
    def get_model_profiles(self) -> dict[str, dict[str, JsonValue]]:
        raise NotImplementedError

    def save_model_profile(
        self,
        name: str,
        profile: dict[str, JsonValue],
        *,
        source_name: str | None = None,
    ) -> None:
        raise NotImplementedError

    async def discover_models_async(
        self,
        request: ModelDiscoveryRequest,
    ) -> ModelDiscoveryResult:
        raise NotImplementedError

    def reload_model_config(self) -> None:
        raise NotImplementedError


class W3ConnectorService:
    def __init__(
        self,
        *,
        config_dir: Path,
        model_config_service: ModelConfigServiceLike,
        secret_store: AppSecretStore | None = None,
        token_service: MaaSTokenServiceLike | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._model_config_service = model_config_service
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )
        self._token_service = (
            get_maas_token_service() if token_service is None else token_service
        )

    def get_status(self) -> W3ConnectorStatusResponse:
        config = self._load_config()
        return W3ConnectorStatusResponse(
            username=config.username,
            has_password=self._get_password() is not None,
            status=self._resolve_status(config),
            updated_at=config.updated_at,
            last_sync=config.last_sync,
            last_error=config.last_error,
        )

    def connector_item(self) -> ConnectorItem:
        status = self.get_status()
        configured = bool(status.username and status.has_password)
        return ConnectorItem(
            connector_id=W3_CONNECTOR_ID,
            provider=ConnectorProvider.W3,
            category=ConnectorCategory.AUTH,
            display_name=W3_CONNECTOR_NAME,
            description="Connect W3 unified authentication for WEB_TOKEN reuse.",
            status=status.status,
            auth_type=ConnectorAuthType.USERNAME_PASSWORD,
            account_count=int(configured),
            enabled_count=int(status.status == ConnectorStatus.CONNECTED),
            last_activity_at=status.updated_at,
            last_error=status.last_error,
            capabilities=("w3_auth", "web_token"),
        )

    async def save_credentials(
        self,
        request: W3ConnectorSaveRequest,
    ) -> W3ConnectorSaveResponse:
        existing_config = self._load_config()
        existing_password = self._get_password()
        password = request.password or existing_password
        if password is None:
            return W3ConnectorSaveResponse(
                ok=False,
                status=ConnectorStatus.NEEDS_CONFIG,
                message="W3 password is required.",
                username=request.username,
                has_password=False,
                sync=None,
            )
        test_result = await self.test_connection(
            W3ConnectorTestRequest(username=request.username, password=password),
            force_refresh=True,
        )
        if not test_result.ok:
            config = existing_config.model_copy(
                update={
                    "updated_at": datetime.now(UTC),
                    "last_error": test_result.message,
                }
            )
            self._save_config(config)
            return W3ConnectorSaveResponse(
                ok=False,
                status=ConnectorStatus.ERROR,
                message=test_result.message,
                username=request.username,
                has_password=existing_password is not None,
                sync=None,
            )

        self._set_password(password)
        config = existing_config.model_copy(
            update={
                "username": request.username,
                "updated_at": datetime.now(UTC),
                "last_error": None,
            }
        )
        self._save_config(config)
        self._sync_w3_profile_credentials(username=request.username, password=password)
        self._model_config_service.reload_model_config()
        return W3ConnectorSaveResponse(
            ok=True,
            status=ConnectorStatus.CONNECTED,
            message="W3 connector credentials are valid.",
            username=request.username,
            has_password=True,
            sync=None,
        )

    async def save_credentials_and_import(
        self,
        request: W3ConnectorSaveRequest,
    ) -> W3ConnectorSaveResponse:
        return await self.save_credentials(request)

    async def test_connection(
        self,
        request: W3ConnectorTestRequest | None = None,
        *,
        force_refresh: bool = False,
    ) -> W3ConnectorTestResponse:
        resolved_request = request or W3ConnectorTestRequest()
        username = resolved_request.username or self._load_config().username
        password = resolved_request.password or self._get_password()
        if username is None or password is None:
            return W3ConnectorTestResponse(
                ok=False,
                status="needs_config",
                message="W3 username and password are required.",
                username=username,
                has_token=False,
            )
        try:
            auth_context = await self._token_service.get_auth_context(
                auth_config=MaaSAuthConfig(username=username, password=password),
                ssl_verify=None,
                connect_timeout_seconds=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
                force_refresh=force_refresh,
            )
        except MaaSLoginError as exc:
            return W3ConnectorTestResponse(
                ok=False,
                status="error",
                message=str(exc) or "W3 login failed.",
                username=username,
                has_token=False,
            )
        except Exception as exc:
            LOGGER.warning(
                "W3 credential validation failed.",
                extra={"event": "connector.w3.validation_failed"},
                exc_info=True,
            )
            return W3ConnectorTestResponse(
                ok=False,
                status="error",
                message=str(exc) or "W3 login failed.",
                username=username,
                has_token=False,
            )
        token = auth_context.token.strip()
        return W3ConnectorTestResponse(
            ok=bool(token),
            status="valid" if token else "error",
            message="W3 login returned X-Auth-Token."
            if token
            else "W3 login did not return X-Auth-Token.",
            username=username,
            has_token=bool(token),
        )

    async def test_connector_result(self) -> ConnectorTestResult:
        item = self.connector_item()
        test_result = await self.test_connection()
        ok = test_result.ok
        checks = (
            ConnectorHealthCheck(
                name="credentials_configured",
                ok=item.account_count > 0,
                message="W3 username and password are configured."
                if item.account_count > 0
                else "W3 username and password are required.",
            ),
            ConnectorHealthCheck(
                name="x_auth_token",
                ok=ok,
                message=test_result.message,
            ),
        )
        failed_status = (
            ConnectorStatus.NEEDS_CONFIG
            if item.account_count <= 0
            else ConnectorStatus.ERROR
        )
        return ConnectorTestResult(
            connector_id=W3_CONNECTOR_ID,
            provider=ConnectorProvider.W3,
            status=ConnectorStatus.CONNECTED if ok else failed_status,
            ok=ok,
            checked_at=datetime.now(UTC),
            message="W3 connection is healthy." if ok else test_result.message,
            account_count=item.account_count,
            enabled_count=1 if ok else 0,
            runtime_running=None,
            login_active=ok,
            last_error=None if ok else test_result.message,
            capabilities=item.capabilities,
            checks=checks,
        )

    async def sync_models_with_saved_credentials(self) -> W3ConnectorSyncResponse:
        config = self._load_config()
        username = config.username
        password = self._get_password()
        if username is None or password is None:
            summary = W3ModelSyncSummary(
                failed_count=1,
                failed_models=(
                    W3ModelImportFailure(
                        provider=W3_CONNECTOR_ID,
                        model=None,
                        message="W3 username and password are required.",
                    ),
                ),
                synced_at=datetime.now(UTC),
            )
            return W3ConnectorSyncResponse(
                ok=False,
                message="W3 username and password are required.",
                sync=summary,
            )
        sync = await self.sync_models(username=username, password=password)
        self._save_config(
            config.model_copy(
                update={
                    "last_sync": sync,
                    "last_error": None
                    if sync.failed_count == 0
                    else "W3 model sync failed.",
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        if self._sync_w3_profile_credentials(username=username, password=password):
            self._model_config_service.reload_model_config()
        return W3ConnectorSyncResponse(
            ok=sync.failed_count == 0,
            message="W3 model sync completed."
            if sync.failed_count == 0
            else "W3 model sync completed with errors.",
            sync=sync,
        )

    async def sync_models(self, *, username: str, password: str) -> W3ModelSyncSummary:
        discovered: list[tuple[ProviderType, ModelDiscoveryEntry]] = []
        failures: list[W3ModelImportFailure] = []
        for provider in (ProviderType.MAAS, ProviderType.CODEAGENT):
            try:
                result = await self._discover_provider_models(
                    provider=provider,
                    username=username,
                    password=password,
                )
            except Exception as exc:
                LOGGER.warning(
                    "W3 model discovery failed.",
                    extra={
                        "event": "connector.w3.discovery_failed",
                        "provider": provider.value,
                    },
                    exc_info=True,
                )
                failures.append(
                    W3ModelImportFailure(
                        provider=provider.value,
                        model=None,
                        message=str(exc) or "Model discovery failed.",
                    )
                )
                continue
            if not result.ok:
                failures.append(
                    W3ModelImportFailure(
                        provider=provider.value,
                        model=None,
                        message=result.error_message or "Model discovery failed.",
                    )
                )
                continue
            for entry in _discovery_entries(result):
                discovered.append((provider, entry))

        existing_keys = self._existing_provider_model_keys()
        created_profiles: list[str] = []
        skipped_models: list[str] = []
        for provider, entry in discovered:
            model_key = (provider.value, entry.model)
            if model_key in existing_keys:
                skipped_models.append(f"{provider.value}:{entry.model}")
                continue
            profile_name = self._available_profile_name(
                prefix=f"w3-{provider.value}",
                model=entry.model,
            )
            profile = self._build_profile(
                provider=provider,
                entry=entry,
                username=username,
                password=password,
            )
            try:
                self._model_config_service.save_model_profile(profile_name, profile)
            except Exception as exc:
                LOGGER.warning(
                    "Failed to create W3 imported model profile.",
                    extra={
                        "event": "connector.w3.profile_create_failed",
                        "provider": provider.value,
                        "model": entry.model,
                        "profile_name": profile_name,
                    },
                    exc_info=True,
                )
                failures.append(
                    W3ModelImportFailure(
                        provider=provider.value,
                        model=entry.model,
                        message=str(exc) or "Failed to create model profile.",
                    )
                )
                continue
            created_profiles.append(profile_name)
            existing_keys.add(model_key)

        return W3ModelSyncSummary(
            discovered_count=len(discovered),
            created_count=len(created_profiles),
            skipped_existing_count=len(skipped_models),
            failed_count=len(failures),
            created_profiles=tuple(created_profiles),
            skipped_models=tuple(skipped_models),
            failed_models=tuple(failures),
            synced_at=datetime.now(UTC),
        )

    async def _discover_provider_models(
        self,
        *,
        provider: ProviderType,
        username: str,
        password: str,
    ) -> ModelDiscoveryResult:
        if provider == ProviderType.MAAS:
            return await self._model_config_service.discover_models_async(
                ModelDiscoveryRequest(
                    override=ModelConnectivityProbeOverride(
                        provider=ProviderType.MAAS,
                        base_url=DEFAULT_MAAS_BASE_URL,
                        maas_auth=MaaSAuthConfig(username=username, password=password),
                    ),
                    metadata_policy="endpoint_only",
                )
            )
        return await self._model_config_service.discover_models_async(
            ModelDiscoveryRequest(
                override=ModelConnectivityProbeOverride(
                    provider=ProviderType.CODEAGENT,
                    codeagent_auth=CodeAgentAuthConfig(
                        auth_method=CodeAgentAuthMethod.PASSWORD,
                        username=username,
                        password=password,
                    ),
                ),
                metadata_policy="endpoint_only",
            )
        )

    def _existing_provider_model_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for profile in self._model_config_service.get_model_profiles().values():
            provider = profile.get("provider")
            model = profile.get("model")
            if isinstance(provider, str) and isinstance(model, str) and model.strip():
                keys.add((provider, model.strip()))
        return keys

    def _available_profile_name(self, *, prefix: str, model: str) -> str:
        base_name = f"{prefix}-{_slugify(model)}"
        existing_names = set(self._model_config_service.get_model_profiles().keys())
        if base_name not in existing_names:
            return base_name
        suffix = 2
        while f"{base_name}-{suffix}" in existing_names:
            suffix += 1
        return f"{base_name}-{suffix}"

    @staticmethod
    def _build_profile(
        *,
        provider: ProviderType,
        entry: ModelDiscoveryEntry,
        username: str,
        password: str,
    ) -> dict[str, JsonValue]:
        capabilities_payload: JsonValue = (
            entry.capabilities.model_dump(mode="json")
            if isinstance(entry.capabilities, ModelCapabilities)
            else ModelCapabilities().model_dump(mode="json")
        )
        profile: dict[str, JsonValue] = {
            "provider": provider.value,
            "model": entry.model,
            "base_url": DEFAULT_MAAS_BASE_URL
            if provider == ProviderType.MAAS
            else DEFAULT_CODEAGENT_BASE_URL,
            "catalog_provider_id": W3_CONNECTOR_ID,
            "catalog_provider_name": W3_CONNECTOR_NAME,
            "catalog_model_name": entry.model,
            "capabilities": capabilities_payload,
        }
        if entry.context_window is not None:
            profile["context_window"] = entry.context_window
        if entry.output_limit is not None:
            profile["max_tokens"] = entry.output_limit
        if provider == ProviderType.MAAS:
            profile["maas_auth"] = {"username": username, "password": password}
            return profile
        profile["codeagent_auth"] = {
            "auth_method": CodeAgentAuthMethod.PASSWORD.value,
            "username": username,
            "password": password,
        }
        return profile

    def _sync_w3_profile_credentials(
        self,
        *,
        username: str,
        password: str,
    ) -> bool:
        synced = False
        raw_profiles = self._load_raw_model_profiles()
        for name, profile in self._model_config_service.get_model_profiles().items():
            if profile.get("catalog_provider_id") != W3_CONNECTOR_ID:
                continue
            provider = profile.get("provider")
            if provider == ProviderType.MAAS.value:
                if not self._should_sync_w3_profile_auth(
                    raw_profiles=raw_profiles,
                    profile_name=name,
                    auth_field_name="maas_auth",
                ):
                    continue
                self._sync_raw_model_profile_username(
                    raw_profiles=raw_profiles,
                    profile_name=name,
                    auth_field_name="maas_auth",
                    username=username,
                )
                self._set_model_profile_secret(
                    profile_name=name,
                    field_name=maas_password_secret_field_name(),
                    value=password,
                )
                synced = True
            elif provider == ProviderType.CODEAGENT.value:
                if not self._should_sync_w3_profile_auth(
                    raw_profiles=raw_profiles,
                    profile_name=name,
                    auth_field_name="codeagent_auth",
                ):
                    continue
                self._sync_raw_model_profile_username(
                    raw_profiles=raw_profiles,
                    profile_name=name,
                    auth_field_name="codeagent_auth",
                    username=username,
                )
                self._set_model_profile_secret(
                    profile_name=name,
                    field_name=codeagent_password_secret_field_name(),
                    value=password,
                )
                synced = True
        if raw_profiles is not None:
            self._save_raw_model_profiles(raw_profiles)
        return synced

    @staticmethod
    def _should_sync_w3_profile_auth(
        *,
        raw_profiles: dict[str, JsonValue] | None,
        profile_name: str,
        auth_field_name: str,
    ) -> bool:
        if raw_profiles is None:
            return True
        profile = raw_profiles.get(profile_name)
        if not isinstance(profile, dict):
            return True
        raw_auth = profile.get(auth_field_name)
        if not isinstance(raw_auth, dict):
            return True
        auth_source = raw_auth.get("auth_source")
        if not isinstance(auth_source, str) or not auth_source.strip():
            return True
        return auth_source.strip() != ModelAuthSource.PROFILE.value

    @staticmethod
    def _sync_raw_model_profile_username(
        *,
        raw_profiles: dict[str, JsonValue] | None,
        profile_name: str,
        auth_field_name: str,
        username: str,
    ) -> None:
        if raw_profiles is None:
            return
        profile = raw_profiles.get(profile_name)
        if not isinstance(profile, dict):
            return
        raw_auth = profile.get(auth_field_name)
        auth: dict[str, JsonValue] = (
            dict(raw_auth) if isinstance(raw_auth, dict) else {}
        )
        auth["username"] = username
        if auth_field_name == "codeagent_auth":
            auth["auth_method"] = CodeAgentAuthMethod.PASSWORD.value
        profile[auth_field_name] = auth

    def _load_raw_model_profiles(self) -> dict[str, JsonValue] | None:
        model_file = self._config_dir / "model.json"
        if not model_file.exists():
            return None
        try:
            return _JSON_OBJECT_ADAPTER.validate_json(
                model_file.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError):
            return None

    def _save_raw_model_profiles(self, profiles: dict[str, JsonValue]) -> None:
        model_file = self._config_dir / "model.json"
        model_file.write_text(
            dumps(profiles, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _set_model_profile_secret(
        self,
        *,
        profile_name: str,
        field_name: str,
        value: str,
    ) -> None:
        self._secret_store.set_secret(
            self._config_dir,
            namespace=_MODEL_PROFILE_SECRET_NAMESPACE,
            owner_id=profile_name,
            field_name=field_name,
            value=value,
        )

    def _resolve_status(self, config: W3ConnectorConfig) -> ConnectorStatus:
        if str(config.last_error or "").strip():
            return ConnectorStatus.ERROR
        if config.username is None or self._get_password() is None:
            return ConnectorStatus.NEEDS_CONFIG
        return ConnectorStatus.CONNECTED

    def _get_password(self) -> str | None:
        return self._secret_store.get_secret(
            self._config_dir,
            namespace=W3_SECRET_NAMESPACE,
            owner_id=W3_SECRET_OWNER_ID,
            field_name=W3_PASSWORD_FIELD,
        )

    def _set_password(self, password: str) -> None:
        self._secret_store.set_secret(
            self._config_dir,
            namespace=W3_SECRET_NAMESPACE,
            owner_id=W3_SECRET_OWNER_ID,
            field_name=W3_PASSWORD_FIELD,
            value=password,
        )

    def _load_config(self) -> W3ConnectorConfig:
        config_file = self._config_file()
        if not config_file.exists():
            return W3ConnectorConfig()
        try:
            return W3ConnectorConfig.model_validate_json(
                config_file.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValidationError):
            return W3ConnectorConfig()

    def _save_config(self, config: W3ConnectorConfig) -> None:
        config_file = self._config_file()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            dumps(
                config.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _config_file(self) -> Path:
        return self._config_dir / "connectors" / "w3.json"


def _discovery_entries(result: ModelDiscoveryResult) -> tuple[ModelDiscoveryEntry, ...]:
    if result.model_entries:
        return result.model_entries
    return tuple(ModelDiscoveryEntry(model=model) for model in result.models)


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "model"

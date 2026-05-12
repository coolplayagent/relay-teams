from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from relay_teams.connector.w3_models import (
    W3ConnectorSaveRequest,
    W3ConnectorTestRequest,
)
from relay_teams.connector.w3_service import W3ConnectorService
from relay_teams.connector.models import ConnectorCategory
from relay_teams.providers.codeagent_auth import codeagent_password_secret_field_name
from relay_teams.providers.maas_auth import (
    MaaSAuthContext,
    MaaSLoginError,
    maas_password_secret_field_name,
)
from relay_teams.providers.model_config import (
    DEFAULT_CODEAGENT_BASE_URL,
    DEFAULT_MAAS_BASE_URL,
    ModelCapabilities,
    ModelModalityMatrix,
    MaaSAuthConfig,
    ProviderType,
)
from relay_teams.providers.model_connectivity import (
    ModelConnectivityDiagnostics,
    ModelDiscoveryEntry,
    ModelDiscoveryRequest,
    ModelDiscoveryResult,
)
from relay_teams.providers.w3_auth_source import (
    W3_PASSWORD_FIELD,
    W3_SECRET_NAMESPACE,
    W3_SECRET_OWNER_ID,
)
from relay_teams.secrets import AppSecretStore


class _FileSecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


class _TokenService:
    def __init__(
        self,
        *,
        token: str | None = "token",
        error: RuntimeError | None = None,
    ) -> None:
        self._token = token
        self._error = error
        self.calls: list[MaaSAuthConfig] = []

    def set_token(self, token: str | None) -> None:
        self._token = token

    async def get_auth_context(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> MaaSAuthContext:
        _ = ssl_verify
        _ = connect_timeout_seconds
        _ = force_refresh
        self.calls.append(auth_config)
        if self._error is not None:
            raise self._error
        if self._token is None:
            raise MaaSLoginError("login failed", status_code=401)
        return MaaSAuthContext(token=self._token)


class _ModelConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        secret_store: AppSecretStore,
        profiles: dict[str, dict[str, JsonValue]] | None = None,
        failed_providers: tuple[ProviderType, ...] = (),
        empty_providers: tuple[ProviderType, ...] = (),
        fail_save_models: tuple[str, ...] = (),
    ) -> None:
        self._config_dir = config_dir
        self._secret_store = secret_store
        self._profiles = profiles or {}
        self._failed_providers = set(failed_providers)
        self._empty_providers = set(empty_providers)
        self._fail_save_models = set(fail_save_models)
        self.reloads = 0

    def get_model_profiles(self) -> dict[str, dict[str, JsonValue]]:
        return {name: dict(profile) for name, profile in self._profiles.items()}

    def save_model_profile(
        self,
        name: str,
        profile: dict[str, JsonValue],
        *,
        source_name: str | None = None,
    ) -> None:
        _ = source_name
        stored = dict(profile)
        provider = stored.get("provider")
        model = stored.get("model")
        if isinstance(model, str) and model in self._fail_save_models:
            raise ValueError("save failed")
        if provider == ProviderType.MAAS.value:
            auth = cast(dict[str, JsonValue], stored.get("maas_auth"))
            password = auth.pop("password")
            self._secret_store.set_secret(
                self._config_dir,
                namespace="model_profile",
                owner_id=name,
                field_name=maas_password_secret_field_name(),
                value=cast(str, password),
            )
            auth["has_password"] = True
        if provider == ProviderType.CODEAGENT.value:
            auth = cast(dict[str, JsonValue], stored.get("codeagent_auth"))
            password = auth.pop("password")
            self._secret_store.set_secret(
                self._config_dir,
                namespace="model_profile",
                owner_id=name,
                field_name=codeagent_password_secret_field_name(),
                value=cast(str, password),
            )
            auth["has_password"] = True
        self._profiles[name] = stored

    async def discover_models_async(
        self,
        request: ModelDiscoveryRequest,
    ) -> ModelDiscoveryResult:
        provider = request.override.provider if request.override is not None else None
        if provider is None:
            raise AssertionError("unexpected provider")
        if provider in self._failed_providers:
            raise ValueError("discovery exploded")
        if provider in self._empty_providers:
            return ModelDiscoveryResult(
                ok=False,
                provider=provider,
                base_url=DEFAULT_CODEAGENT_BASE_URL
                if provider == ProviderType.CODEAGENT
                else DEFAULT_MAAS_BASE_URL,
                latency_ms=1,
                checked_at=datetime(2026, 5, 11, 9, 0, tzinfo=UTC),
                diagnostics=ModelConnectivityDiagnostics(
                    endpoint_reachable=False,
                    auth_valid=False,
                    rate_limited=False,
                ),
                models=(),
                error_message="discovery returned no models",
            )
        if provider == ProviderType.MAAS:
            return _discovery_result(
                provider=ProviderType.MAAS,
                base_url=DEFAULT_MAAS_BASE_URL,
                models=("pangu", "deepseek"),
            )
        if provider == ProviderType.CODEAGENT:
            return _discovery_result(
                provider=ProviderType.CODEAGENT,
                base_url=DEFAULT_CODEAGENT_BASE_URL,
                models=("claude-code",),
            )
        raise AssertionError("unexpected provider")

    def reload_model_config(self) -> None:
        self.reloads += 1


def test_connector_item_describes_w3_as_unified_auth(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    item = service.connector_item()

    assert item.category == ConnectorCategory.AUTH
    assert item.capabilities == ("w3_auth", "web_token")
    assert "WEB_TOKEN" in item.description


def test_status_reports_error_when_saved_config_has_last_error(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )
    (tmp_path / "connectors").mkdir()
    (tmp_path / "connectors" / "w3.json").write_text(
        '{"username": "user", "last_error": "previous failure"}',
        encoding="utf-8",
    )

    status = service.get_status()

    assert status.status.value == "error"
    assert status.last_error == "previous failure"


def test_malformed_saved_config_is_ignored(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )
    (tmp_path / "connectors").mkdir()
    (tmp_path / "connectors" / "w3.json").write_text("{", encoding="utf-8")

    status = service.get_status()

    assert status.username is None
    assert status.status.value == "needs_config"


@pytest.mark.asyncio
async def test_save_credentials_stores_password_in_secret_store_only(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    response = await service.save_credentials_and_import(
        W3ConnectorSaveRequest(username="user", password="secret-password")
    )

    assert response.ok is True
    assert response.sync is None
    assert service.get_status().has_password is True
    assert model_service.reloads == 1
    assert model_service.get_model_profiles() == {}
    config_text = (tmp_path / "connectors" / "w3.json").read_text(encoding="utf-8")
    assert "secret-password" not in config_text
    assert "user" in config_text


@pytest.mark.asyncio
async def test_save_credentials_preserves_last_valid_username_when_validation_fails(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    token_service = _TokenService(token="x-auth-token")
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=token_service,
    )
    await service.save_credentials(
        W3ConnectorSaveRequest(username="old-user", password="old-password")
    )

    token_service.set_token(None)
    response = await service.save_credentials(
        W3ConnectorSaveRequest(username="new-user", password="new-password")
    )

    assert response.ok is False
    assert service.get_status().username == "old-user"
    assert service.get_status().has_password is True
    assert (
        secret_store.get_secret(
            tmp_path,
            namespace=W3_SECRET_NAMESPACE,
            owner_id=W3_SECRET_OWNER_ID,
            field_name=W3_PASSWORD_FIELD,
        )
        == "old-password"
    )
    assert model_service.reloads == 1


@pytest.mark.asyncio
async def test_save_credentials_syncs_imported_profile_secrets(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    profiles: dict[str, dict[str, JsonValue]] = {
        "w3-maas-pangu": {
            "catalog_provider_id": "w3",
            "provider": ProviderType.MAAS.value,
            "model": "pangu",
        },
        "w3-codeagent-claude": {
            "catalog_provider_id": "w3",
            "provider": ProviderType.CODEAGENT.value,
            "model": "claude-code",
        },
    }
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
        profiles=profiles,
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )
    (tmp_path / "model.json").write_text(
        """
        {
          "w3-maas-pangu": {"maas_auth": {"username": "old-user"}},
          "w3-codeagent-claude": {"codeagent_auth": {"username": "old-user"}}
        }
        """,
        encoding="utf-8",
    )

    response = await service.save_credentials(
        W3ConnectorSaveRequest(username="new-user", password="new-password")
    )

    assert response.ok is True
    saved_profiles = (tmp_path / "model.json").read_text(encoding="utf-8")
    assert "new-user" in saved_profiles
    assert (
        secret_store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="w3-maas-pangu",
            field_name=maas_password_secret_field_name(),
        )
        == "new-password"
    )
    assert (
        secret_store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="w3-codeagent-claude",
            field_name=codeagent_password_secret_field_name(),
        )
        == "new-password"
    )
    assert model_service.reloads == 1


@pytest.mark.asyncio
async def test_save_credentials_does_not_sync_decoupled_imported_profile_secrets(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    profiles: dict[str, dict[str, JsonValue]] = {
        "w3-maas-pangu": {
            "catalog_provider_id": "w3",
            "provider": ProviderType.MAAS.value,
            "model": "pangu",
        },
        "w3-codeagent-claude": {
            "catalog_provider_id": "w3",
            "provider": ProviderType.CODEAGENT.value,
            "model": "claude-code",
        },
    }
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
        profiles=profiles,
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )
    (tmp_path / "model.json").write_text(
        """
        {
          "w3-maas-pangu": {
            "maas_auth": {"auth_source": "profile", "username": "profile-maas-user"}
          },
          "w3-codeagent-claude": {
            "codeagent_auth": {
              "auth_source": "profile",
              "auth_method": "password",
              "username": "profile-codeagent-user"
            }
          }
        }
        """,
        encoding="utf-8",
    )
    secret_store.set_secret(
        tmp_path,
        namespace="model_profile",
        owner_id="w3-maas-pangu",
        field_name=maas_password_secret_field_name(),
        value="profile-maas-password",
    )
    secret_store.set_secret(
        tmp_path,
        namespace="model_profile",
        owner_id="w3-codeagent-claude",
        field_name=codeagent_password_secret_field_name(),
        value="profile-codeagent-password",
    )

    response = await service.save_credentials(
        W3ConnectorSaveRequest(username="new-user", password="new-password")
    )

    assert response.ok is True
    saved_profiles = (tmp_path / "model.json").read_text(encoding="utf-8")
    assert "profile-maas-user" in saved_profiles
    assert "profile-codeagent-user" in saved_profiles
    assert "new-user" not in saved_profiles
    assert (
        secret_store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="w3-maas-pangu",
            field_name=maas_password_secret_field_name(),
        )
        == "profile-maas-password"
    )
    assert (
        secret_store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="w3-codeagent-claude",
            field_name=codeagent_password_secret_field_name(),
        )
        == "profile-codeagent-password"
    )
    assert model_service.reloads == 1


@pytest.mark.asyncio
async def test_test_connection_reports_login_failure(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=_TokenService(token=None),
    )

    result = await service.test_connection(
        W3ConnectorTestRequest(username="user", password="bad")
    )

    assert result.ok is False
    assert result.status == "error"
    assert result.has_token is False


@pytest.mark.asyncio
async def test_test_connection_reports_missing_credentials(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    result = await service.test_connection()

    assert result.ok is False
    assert result.status == "needs_config"
    assert result.message == "W3 username and password are required."


@pytest.mark.asyncio
async def test_test_connection_reports_empty_token(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=_TokenService(token="   "),
    )

    result = await service.test_connection(
        W3ConnectorTestRequest(username="user", password="password")
    )

    assert result.ok is False
    assert result.message == "W3 login did not return X-Auth-Token."


@pytest.mark.asyncio
async def test_test_connection_reports_unexpected_validation_error(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=_TokenService(error=RuntimeError("network down")),
    )

    result = await service.test_connection(
        W3ConnectorTestRequest(username="user", password="password")
    )

    assert result.ok is False
    assert result.message == "network down"


@pytest.mark.asyncio
async def test_test_connector_result_includes_configuration_checks(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    result = await service.test_connector_result()

    assert result.ok is False
    assert result.status.value == "needs_config"
    assert {check.name: check.ok for check in result.checks} == {
        "credentials_configured": False,
        "x_auth_token": False,
    }


@pytest.mark.asyncio
async def test_test_connector_result_reports_error_for_saved_login_failure(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    token_service = _TokenService(token="x-auth-token")
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=token_service,
    )
    await service.save_credentials(
        W3ConnectorSaveRequest(username="user", password="password")
    )
    token_service.set_token(None)

    result = await service.test_connector_result()

    assert result.ok is False
    assert result.status.value == "error"
    assert result.account_count == 1


@pytest.mark.asyncio
async def test_import_creates_missing_profiles_and_skips_existing(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
        profiles={
            "existing-maas": {
                "provider": ProviderType.MAAS.value,
                "model": "pangu",
            }
        },
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    summary = await service.sync_models(username="user", password="secret-password")

    assert summary.discovered_count == 3
    assert summary.created_count == 2
    assert summary.skipped_existing_count == 1
    assert set(summary.created_profiles) == {
        "w3-maas-deepseek",
        "w3-codeagent-claude-code",
    }
    profiles = model_service.get_model_profiles()
    assert profiles["w3-maas-deepseek"]["catalog_provider_id"] == "w3"
    assert profiles["w3-codeagent-claude-code"]["catalog_provider_name"] == "W3"


@pytest.mark.asyncio
async def test_import_uses_model_metadata_and_unique_profile_names(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
        profiles={
            "w3-maas-deepseek": {
                "provider": ProviderType.MAAS.value,
                "model": "other-model",
            }
        },
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    summary = await service.sync_models(username="user", password="secret-password")

    assert "w3-maas-deepseek-2" in summary.created_profiles
    profile = model_service.get_model_profiles()["w3-maas-deepseek-2"]
    assert profile["context_window"] == 128000
    assert profile["max_tokens"] == 4096
    capabilities = cast(dict[str, JsonValue], profile["capabilities"])
    input_capabilities = cast(dict[str, JsonValue], capabilities["input"])
    assert input_capabilities["image"] is True


@pytest.mark.asyncio
async def test_import_records_discovery_and_profile_save_failures(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
        failed_providers=(ProviderType.MAAS,),
        fail_save_models=("claude-code",),
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    summary = await service.sync_models(username="user", password="secret-password")

    assert summary.created_count == 0
    assert summary.failed_count == 2
    assert {failure.provider for failure in summary.failed_models} == {
        "maas",
        "codeagent",
    }


@pytest.mark.asyncio
async def test_import_records_provider_result_errors(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
        empty_providers=(ProviderType.CODEAGENT,),
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    summary = await service.sync_models(username="user", password="secret-password")

    assert summary.created_count == 2
    assert summary.failed_count == 1
    assert summary.failed_models[0].message == "discovery returned no models"


@pytest.mark.asyncio
async def test_imported_maas_and_codeagent_profiles_use_w3_password_secret(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    model_service = _ModelConfigService(config_dir=tmp_path, secret_store=secret_store)
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    await service.sync_models(username="user", password="secret-password")

    assert (
        secret_store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="w3-maas-deepseek",
            field_name=maas_password_secret_field_name(),
        )
        == "secret-password"
    )
    assert (
        secret_store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="w3-codeagent-claude-code",
            field_name=codeagent_password_secret_field_name(),
        )
        == "secret-password"
    )


@pytest.mark.asyncio
async def test_sync_models_with_saved_credentials_requires_config(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=_ModelConfigService(
            config_dir=tmp_path,
            secret_store=secret_store,
        ),
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )

    response = await service.sync_models_with_saved_credentials()

    assert response.ok is False
    assert response.sync is not None
    assert response.sync.failed_count == 1


@pytest.mark.asyncio
async def test_sync_models_with_saved_credentials_updates_w3_imported_profiles(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    profiles: dict[str, dict[str, JsonValue]] = {
        "w3-maas-pangu": {
            "catalog_provider_id": "w3",
            "provider": ProviderType.MAAS.value,
            "model": "pangu",
        },
        "w3-codeagent-claude": {
            "catalog_provider_id": "w3",
            "provider": ProviderType.CODEAGENT.value,
            "model": "claude-code",
        },
    }
    model_service = _ModelConfigService(
        config_dir=tmp_path,
        secret_store=secret_store,
        profiles=profiles,
    )
    service = W3ConnectorService(
        config_dir=tmp_path,
        model_config_service=model_service,
        secret_store=secret_store,
        token_service=_TokenService(token="x-auth-token"),
    )
    await service.save_credentials(
        W3ConnectorSaveRequest(username="old-user", password="old-password")
    )
    (tmp_path / "model.json").write_text(
        """
        {
          "w3-maas-pangu": {"maas_auth": {"username": "old-user"}},
          "w3-codeagent-claude": {"codeagent_auth": {"username": "old-user"}}
        }
        """,
        encoding="utf-8",
    )
    await service.save_credentials(
        W3ConnectorSaveRequest(username="new-user", password="new-password")
    )

    response = await service.sync_models_with_saved_credentials()

    assert response.ok is True
    saved_profiles = (tmp_path / "model.json").read_text(encoding="utf-8")
    assert "new-user" in saved_profiles
    assert (
        secret_store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="w3-maas-pangu",
            field_name=maas_password_secret_field_name(),
        )
        == "new-password"
    )
    assert (
        secret_store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="w3-codeagent-claude",
            field_name=codeagent_password_secret_field_name(),
        )
        == "new-password"
    )
    assert model_service.reloads == 3


def _discovery_result(
    *,
    provider: ProviderType,
    base_url: str,
    models: tuple[str, ...],
) -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        ok=True,
        provider=provider,
        base_url=base_url,
        latency_ms=1,
        checked_at=datetime(2026, 5, 11, 9, 0, tzinfo=UTC),
        diagnostics=ModelConnectivityDiagnostics(
            endpoint_reachable=True,
            auth_valid=True,
            rate_limited=False,
        ),
        models=models,
        model_entries=tuple(
            ModelDiscoveryEntry(
                model=model,
                context_window=128000 if model == "deepseek" else None,
                output_limit=4096 if model == "deepseek" else None,
                capabilities=ModelCapabilities(
                    input=ModelModalityMatrix(
                        image=True if model == "deepseek" else None
                    )
                ),
            )
            for model in models
        ),
    )

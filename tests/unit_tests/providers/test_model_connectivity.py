# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest

from relay_teams.media import MediaModality
from relay_teams.providers.codeagent_auth import (
    CodeAgentOAuthTokenResult,
    clear_codeagent_oauth_session_store,
    create_codeagent_oauth_session,
    save_codeagent_oauth_tokens,
)
from relay_teams.providers.maas_auth import MaaSAuthContext, MaaSLoginError
from relay_teams.providers.model_config import (
    CodeAgentAuthConfig,
    DEFAULT_CODEAGENT_BASE_URL,
    MaaSAuthConfig,
    ModelEndpointConfig,
    ModelRequestHeader,
    ProviderType,
    SamplingConfig,
)
from relay_teams.providers.model_connectivity import (
    ModelDiscoveryRequest,
    ModelConnectivityProbeOverride,
    ModelConnectivityProbeRequest,
    ModelConnectivityProbeService,
)
from relay_teams.sessions.runs.runtime_config import RuntimeConfig, RuntimePaths


class _FakeHttpClient:
    def __init__(
        self,
        *,
        response: httpx.Response | None = None,
        error: BaseException | None = None,
        captured: dict[str, object] | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self._captured = captured if captured is not None else {}

    def __enter__(self) -> _FakeHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: object,
    ) -> httpx.Response:
        self._captured["url"] = url
        self._captured["headers"] = dict(headers)
        self._captured["json"] = json
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        self._captured["url"] = url
        self._captured["headers"] = dict(headers)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _FakeMaaSTokenService:
    def __init__(
        self,
        tokens: list[str],
        captured: dict[str, object],
        *,
        departments: list[str | None] | None = None,
    ) -> None:
        self._tokens = tokens
        self._captured = captured
        self._departments = departments or ["Relay/Department"] * len(tokens)

    def get_token_sync(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> str:
        return self.get_auth_context_sync(
            auth_config=auth_config,
            ssl_verify=ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
            force_refresh=force_refresh,
        ).token

    def get_auth_context_sync(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> MaaSAuthContext:
        calls = self._captured.setdefault("maas_token_calls", [])
        assert isinstance(calls, list)
        calls.append(
            {
                "username": auth_config.username,
                "password": auth_config.password,
                "ssl_verify": ssl_verify,
                "connect_timeout_seconds": connect_timeout_seconds,
                "force_refresh": force_refresh,
            }
        )
        token = self._tokens.pop(0)
        department = self._departments.pop(0)
        return MaaSAuthContext(token=token, department=department)


class _FakeCodeAgentTokenService:
    def __init__(
        self,
        tokens: list[str],
        captured: dict[str, object],
    ) -> None:
        self._tokens = tokens
        self._captured = captured

    def get_token_sync(
        self,
        *,
        base_url: str,
        auth_config: CodeAgentAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> str:
        calls = self._captured.setdefault("codeagent_token_calls", [])
        assert isinstance(calls, list)
        calls.append(
            {
                "base_url": base_url,
                "access_token": auth_config.access_token,
                "refresh_token": auth_config.refresh_token,
                "ssl_verify": ssl_verify,
                "connect_timeout_seconds": connect_timeout_seconds,
                "force_refresh": force_refresh,
            }
        )
        return self._tokens.pop(0)


def test_probe_uses_saved_profile_and_returns_usage(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    json={
                        "id": "cmpl-test",
                        "usage": {
                            "prompt_tokens": 8,
                            "completion_tokens": 1,
                            "total_tokens": 9,
                        },
                    },
                ),
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(profile_name="default", timeout_ms=3200)
    )

    assert result.ok is True
    assert result.provider == ProviderType.OPENAI_COMPATIBLE
    assert result.token_usage is not None
    assert result.token_usage.total_tokens == 9
    assert captured["url"] == "https://example.test/v1/chat/completions"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer saved-api-key"
    assert captured["timeout_seconds"] == pytest.approx(3.2)
    assert captured["connect_timeout_seconds"] == pytest.approx(3.2)
    payload = cast(dict[str, object], captured["json"])
    assert payload["temperature"] == pytest.approx(1.0)
    assert payload["top_p"] == pytest.approx(0.95)


def test_probe_uses_profile_connect_timeout_when_request_timeout_omitted(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(ModelConnectivityProbeRequest(profile_name="default"))

    assert result.ok is True
    assert captured["timeout_seconds"] == pytest.approx(17.5)


def test_probe_merges_override_with_saved_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            profile_name="default",
            override=ModelConnectivityProbeOverride(
                model="draft-model",
                base_url="https://draft.test/v1",
            ),
        )
    )

    assert result.ok is True
    assert result.model == "draft-model"
    assert captured["url"] == "https://draft.test/v1/chat/completions"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer saved-api-key"
    payload = cast(dict[str, object], captured["json"])
    assert payload["model"] == "draft-model"


def test_probe_uses_model_ssl_override_before_global_default(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            profile_name="default",
            override=ModelConnectivityProbeOverride(ssl_verify=False),
        )
    )

    assert result.ok is True
    assert captured["ssl_verify"] is False


def test_probe_returns_timeout_error(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(error=httpx.ReadTimeout("timed out")),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(profile_name="default", timeout_ms=2000)
    )

    assert result.ok is False
    assert result.error_code == "network_timeout"
    assert result.retryable is True
    assert result.diagnostics.endpoint_reachable is False


def test_probe_returns_auth_error_for_unauthorized_response(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(
                401,
                json={"error": {"message": "Invalid API key."}},
            )
        ),
    )

    result = service.probe(ModelConnectivityProbeRequest(profile_name="default"))

    assert result.ok is False
    assert result.error_code == "auth_invalid"
    assert result.retryable is False
    assert result.diagnostics.auth_valid is False
    assert result.error_message == "Invalid API key."


def test_probe_accepts_editor_default_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                model="draft-model",
                base_url="https://draft.test/v1",
                api_key="draft-api-key",
            ),
            timeout_ms=15000,
        )
    )

    assert result.ok is True
    assert captured["url"] == "https://draft.test/v1/chat/completions"
    assert captured["timeout_seconds"] == pytest.approx(15.0)


def test_probe_supports_bigmodel_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.BIGMODEL,
                model="glm-4.5",
                base_url="https://open.bigmodel.cn/api/coding/paas/v4",
                api_key="draft-api-key",
            )
        )
    )

    assert result.ok is True
    assert result.provider == ProviderType.BIGMODEL
    assert (
        captured["url"]
        == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
    )


def test_probe_allows_header_only_override(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                model="draft-model",
                base_url="https://draft.test/v1",
                headers=(
                    ModelRequestHeader(
                        name="Authorization",
                        value="Bearer header-only",
                    ),
                ),
            )
        )
    )

    assert result.ok is True
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer header-only"


def test_probe_supports_maas_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: _FakeMaaSTokenService(["maas-token"], captured),
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"usage": {"total_tokens": 3}}),
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                model="maas-chat",
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            )
        )
    )

    assert result.ok is True
    headers = cast(dict[str, str], captured["headers"])
    assert headers["X-Auth-Token"] == "maas-token"
    assert headers["app-id"] == "RelayTeams"
    assert "Authorization" not in headers
    token_calls = cast(list[dict[str, object]], captured["maas_token_calls"])
    assert token_calls[0]["force_refresh"] is False


def test_probe_supports_codeagent_provider_with_oauth_session(
    monkeypatch,
) -> None:
    clear_codeagent_oauth_session_store()
    session = create_codeagent_oauth_session(
        base_url="https://codeagent.example/codeAgentPro",
        client_id="codeagent-client",
        scope="SCOPE",
        scope_resource="devuc",
    )
    save_codeagent_oauth_tokens(
        state=session.state,
        token_result=CodeAgentOAuthTokenResult(
            access_token="session-access-token",
            refresh_token="session-refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_codeagent_token_service",
        lambda: _FakeCodeAgentTokenService(["session-access-token"], captured),
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"usage": {"total_tokens": 4}}),
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.CODEAGENT,
                model="codeagent-chat",
                codeagent_auth=CodeAgentAuthConfig(
                    client_id="codeagent-client",
                    scope="SCOPE",
                    scope_resource="devuc",
                    oauth_session_id=session.auth_session_id,
                ),
            )
        )
    )

    assert result.ok is True
    assert captured["url"] == f"{DEFAULT_CODEAGENT_BASE_URL}/chat/completions"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["X-Auth-Token"] == "session-access-token"
    assert headers["app-id"] == "CodeAgent2.0"
    assert headers["User-Agent"] == "AgentKernel/1.0"
    assert headers["gray"] == "false"
    assert headers["oc-heartbeat"] == "1"
    assert headers["Accept"] == "text/event-stream"
    assert headers["X-snap-traceid"]
    assert headers["X-session-id"].startswith("ses_")
    assert "Authorization" not in headers
    payload = cast(dict[str, object], captured["json"])
    assert payload["stream"] is True
    token_calls = cast(list[dict[str, object]], captured["codeagent_token_calls"])
    assert token_calls[0]["access_token"] == "session-access-token"
    assert token_calls[0]["refresh_token"] == "session-refresh-token"
    clear_codeagent_oauth_session_store()


def test_probe_merges_saved_maas_password_when_override_omits_it(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(
        get_runtime=lambda: _runtime_config(
            profile_name="maas-profile",
            provider=ProviderType.MAAS,
            model="maas-chat",
            base_url="https://maas.example/api/v2",
            api_key=None,
            maas_auth=MaaSAuthConfig(
                username="saved-user",
                password="saved-password",
            ),
        )
    )

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: _FakeMaaSTokenService(["maas-token"], captured),
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"usage": {"total_tokens": 2}}),
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            profile_name="maas-profile",
            override=ModelConnectivityProbeOverride(
                maas_auth=MaaSAuthConfig(username="edited-user"),
            ),
        )
    )

    assert result.ok is True
    token_calls = cast(list[dict[str, object]], captured["maas_token_calls"])
    assert token_calls[0]["username"] == "edited-user"
    assert token_calls[0]["password"] == "saved-password"


def test_probe_returns_maas_auth_error_for_invalid_credentials(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    class _InvalidCredentialsTokenService:
        def get_token_sync(
            self,
            *,
            auth_config: MaaSAuthConfig,
            ssl_verify: bool | None,
            connect_timeout_seconds: float,
            force_refresh: bool = False,
        ) -> str:
            raise MaaSLoginError(
                "invalid username or password",
                status_code=401,
            )

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: _InvalidCredentialsTokenService(),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                model="maas-chat",
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            )
        )
    )

    assert result.ok is False
    assert result.error_code == "auth_invalid"
    assert result.retryable is False
    assert result.diagnostics.auth_valid is False


def test_probe_returns_retryable_maas_login_service_error(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    class _UnavailableTokenService:
        def get_token_sync(
            self,
            *,
            auth_config: MaaSAuthConfig,
            ssl_verify: bool | None,
            connect_timeout_seconds: float,
            force_refresh: bool = False,
        ) -> str:
            raise MaaSLoginError(
                "MAAS auth service unavailable",
                status_code=503,
            )

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: _UnavailableTokenService(),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                model="maas-chat",
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            )
        )
    )

    assert result.ok is False
    assert result.error_code == "provider_error"
    assert result.retryable is True
    assert result.diagnostics.auth_valid is True


def test_probe_refreshes_maas_token_after_unauthorized_response(monkeypatch) -> None:
    captured: dict[str, object] = {"requests": []}
    responses = [
        httpx.Response(401, json={"error": {"message": "expired"}}),
        httpx.Response(200, json={"usage": {"total_tokens": 1}}),
    ]
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    token_service = _FakeMaaSTokenService(["expired-token", "fresh-token"], captured)
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: token_service,
    )

    def build_client(**kwargs: object) -> _FakeHttpClient:
        requests = cast(list[dict[str, object]], captured["requests"])
        local_capture: dict[str, object] = {}
        requests.append(local_capture)
        return _FakeHttpClient(captured=local_capture, response=responses.pop(0))

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        build_client,
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                model="maas-chat",
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            )
        )
    )

    assert result.ok is True
    requests = cast(list[dict[str, object]], captured["requests"])
    first_headers = cast(dict[str, str], requests[0]["headers"])
    second_headers = cast(dict[str, str], requests[1]["headers"])
    assert first_headers["X-Auth-Token"] == "expired-token"
    assert second_headers["X-Auth-Token"] == "fresh-token"
    token_calls = cast(list[dict[str, object]], captured["maas_token_calls"])
    assert token_calls[0]["force_refresh"] is False
    assert token_calls[1]["force_refresh"] is True


def test_discover_models_supports_maas_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: _FakeMaaSTokenService(["maas-token"], captured),
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    json={
                        "user_model_list": [
                            {"model_id": "gpt-4"},
                            {"model_id": "123"},
                        ],
                        "plugin_config": [
                            {
                                "config": (
                                    '[{"composor_act_mode_model_list":[{"model_id":"gpt-4.5"}],'
                                    '"composor_plan_mode_model_list":[{"model_id":"model:ignored"}],'
                                    '"user_model_list":[{"model_id":"gpt-4.1"},{"model_id":"gpt-4"}]}]'
                                )
                            },
                            {"config": "{not-valid-json}"},
                        ],
                    },
                ),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            ),
            timeout_ms=2800,
        )
    )

    assert result.ok is True
    assert result.provider == ProviderType.MAAS
    assert result.models == ("gpt-4", "gpt-4.1", "gpt-4.5")
    assert (
        captured["url"]
        == "https://promptcenter.aims.cce.prod.dragon.tools.huawei.com/PromptCenterService/v1/policy/bundle"
    )
    headers = cast(dict[str, str], captured["headers"])
    assert headers["X-Auth-Token"] == "maas-token"
    request_payload = cast(dict[str, str], captured["json"])
    assert request_payload == {
        "area": "green",
        "plugin_version": "1.0.4",
        "application": "RelayAgent",
        "ide": "RelayAgent",
        "plugin_name": "maas_relay",
        "department": "Relay/Department",
    }
    assert tuple(entry.model for entry in result.model_entries) == (
        "gpt-4",
        "gpt-4.1",
        "gpt-4.5",
    )


def test_discover_models_supports_codeagent_provider_with_oauth_session(
    monkeypatch,
) -> None:
    clear_codeagent_oauth_session_store()
    session = create_codeagent_oauth_session(
        base_url="https://codeagent.example/codeAgentPro",
        client_id="codeagent-client",
        scope="SCOPE",
        scope_resource="devuc",
    )
    save_codeagent_oauth_tokens(
        state=session.state,
        token_result=CodeAgentOAuthTokenResult(
            access_token="session-access-token",
            refresh_token="session-refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_codeagent_token_service",
        lambda: _FakeCodeAgentTokenService(["session-access-token"], captured),
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    json=[{"name": "codeagent-chat"}, {"name": "codeagent-coder"}],
                ),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.CODEAGENT,
                model="codeagent-chat",
                codeagent_auth=CodeAgentAuthConfig(
                    client_id="codeagent-client",
                    scope="SCOPE",
                    scope_resource="devuc",
                    oauth_session_id=session.auth_session_id,
                ),
            )
        )
    )

    assert result.ok is True
    assert result.models == ("codeagent-chat", "codeagent-coder")
    assert (
        captured["url"]
        == f"{DEFAULT_CODEAGENT_BASE_URL}/chat/modles?checkUserPermission=TRUE"
    )
    headers = cast(dict[str, str], captured["headers"])
    assert headers["X-Auth-Token"] == "session-access-token"
    assert headers["app-id"] == "CodeAgent2.0"
    assert headers["User-Agent"] == "AgentKernel/1.0"
    assert headers["gray"] == "false"
    assert headers["oc-heartbeat"] == "1"
    assert headers["X-snap-traceid"]
    assert headers["X-session-id"].startswith("ses_")
    token_calls = cast(list[dict[str, object]], captured["codeagent_token_calls"])
    assert token_calls[0]["access_token"] == "session-access-token"
    assert token_calls[0]["refresh_token"] == "session-refresh-token"
    clear_codeagent_oauth_session_store()


def test_discover_models_merges_saved_maas_password_when_override_omits_it(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(
        get_runtime=lambda: _runtime_config(
            profile_name="maas-profile",
            provider=ProviderType.MAAS,
            model="maas-chat",
            base_url="https://maas.example/api/v2",
            api_key=None,
            maas_auth=MaaSAuthConfig(
                username="saved-user",
                password="saved-password",
            ),
        )
    )

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: _FakeMaaSTokenService(["maas-token"], captured),
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    json={"user_model_list": [{"model_id": "maas-chat"}]},
                ),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            profile_name="maas-profile",
            override=ModelConnectivityProbeOverride(
                maas_auth=MaaSAuthConfig(username="edited-user"),
            ),
        )
    )

    assert result.ok is True
    token_calls = cast(list[dict[str, object]], captured["maas_token_calls"])
    assert token_calls[0]["username"] == "edited-user"
    assert token_calls[0]["password"] == "saved-password"


def test_discover_models_refreshes_maas_token_after_unauthorized_response(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {"requests": []}
    responses = [
        httpx.Response(401, json={"error": {"message": "expired"}}),
        httpx.Response(200, json={"user_model_list": [{"model_id": "maas-chat"}]}),
    ]
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    token_service = _FakeMaaSTokenService(["expired-token", "fresh-token"], captured)
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: token_service,
    )

    def build_client(**_kwargs: object) -> _FakeHttpClient:
        requests = cast(list[dict[str, object]], captured["requests"])
        local_capture: dict[str, object] = {}
        requests.append(local_capture)
        return _FakeHttpClient(captured=local_capture, response=responses.pop(0))

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        build_client,
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            )
        )
    )

    assert result.ok is True
    requests = cast(list[dict[str, object]], captured["requests"])
    first_headers = cast(dict[str, str], requests[0]["headers"])
    second_headers = cast(dict[str, str], requests[1]["headers"])
    assert first_headers["X-Auth-Token"] == "expired-token"
    assert second_headers["X-Auth-Token"] == "fresh-token"
    token_calls = cast(list[dict[str, object]], captured["maas_token_calls"])
    assert token_calls[0]["force_refresh"] is False
    assert token_calls[1]["force_refresh"] is True


def test_discover_models_refreshes_maas_auth_when_department_missing(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    token_service = _FakeMaaSTokenService(
        ["stale-token", "fresh-token"],
        captured,
        departments=[None, "Relay/Department"],
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: token_service,
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    json={"user_model_list": [{"model_id": "maas-chat"}]},
                ),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            )
        )
    )

    assert result.ok is True
    headers = cast(dict[str, str], captured["headers"])
    assert headers["X-Auth-Token"] == "fresh-token"
    token_calls = cast(list[dict[str, object]], captured["maas_token_calls"])
    assert token_calls[0]["force_refresh"] is False
    assert token_calls[1]["force_refresh"] is True


def test_discover_models_returns_invalid_response_when_maas_department_missing(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: _FakeMaaSTokenService(
            ["stale-token", "fresh-token"],
            captured,
            departments=[None, None],
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            )
        )
    )

    assert result.ok is False
    assert result.error_code == "invalid_response"
    assert result.error_message == (
        "MAAS login response did not include user department information."
    )
    token_calls = cast(list[dict[str, object]], captured["maas_token_calls"])
    assert token_calls[0]["force_refresh"] is False
    assert token_calls[1]["force_refresh"] is True


def test_discover_models_uses_saved_profile_and_parses_catalog(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    json={
                        "object": "list",
                        "data": [
                            {"id": "reasoning-model"},
                            {"id": "fake-chat-model"},
                            {"id": "fake-chat-model"},
                        ],
                    },
                ),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(profile_name="default", timeout_ms=2800)
    )

    assert result.ok is True
    assert result.provider == ProviderType.OPENAI_COMPATIBLE
    assert result.models == ("fake-chat-model", "reasoning-model")
    assert captured["url"] == "https://example.test/v1/models"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer saved-api-key"
    assert captured["timeout_seconds"] == pytest.approx(2.8)
    assert captured["connect_timeout_seconds"] == pytest.approx(2.8)
    assert tuple(entry.model for entry in result.model_entries) == (
        "fake-chat-model",
        "reasoning-model",
    )


def test_discover_models_projects_input_modalities_from_catalog(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "gpt-4o-mini"},
                        {
                            "id": "text-plus-image",
                            "input_modalities": ["image"],
                        },
                    ],
                },
            )
        ),
    )

    result = service.discover_models(ModelDiscoveryRequest(profile_name="default"))

    assert result.ok is True
    assert result.model_entries[0].model == "gpt-4o-mini"
    assert result.model_entries[0].input_modalities == (MediaModality.IMAGE,)
    assert result.model_entries[0].capabilities.input.image is True
    assert result.model_entries[1].model == "text-plus-image"
    assert result.model_entries[1].input_modalities == (MediaModality.IMAGE,)
    assert result.model_entries[1].capabilities.input.image is True


def test_discover_models_extracts_context_window_when_provider_returns_it(
    monkeypatch,
) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "fake-chat-model",
                            "context_window": 256000,
                        },
                        {
                            "id": "reasoning-model",
                            "limits": {
                                "context": 128000,
                            },
                        },
                    ],
                },
            )
        ),
    )

    result = service.discover_models(ModelDiscoveryRequest(profile_name="default"))

    assert result.ok is True
    assert result.models == ("fake-chat-model", "reasoning-model")
    assert result.model_entries[0].model == "fake-chat-model"
    assert result.model_entries[0].context_window == 256000
    assert result.model_entries[1].model == "reasoning-model"
    assert result.model_entries[1].context_window == 128000


def test_discover_models_falls_back_to_known_context_window_rules(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "gpt-4o-mini"},
                        {"id": "kimi-k2.5"},
                    ],
                },
            )
        ),
    )

    result = service.discover_models(ModelDiscoveryRequest(profile_name="default"))

    assert result.ok is True
    assert result.models == ("gpt-4o-mini", "kimi-k2.5")
    assert result.model_entries[0].context_window == 128000
    assert result.model_entries[1].context_window == 256000


def test_discover_models_allows_saved_api_key_with_override_base_url(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"data": [{"id": "draft-model"}]}),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            profile_name="default",
            override=ModelConnectivityProbeOverride(base_url="https://draft.test/v1"),
        )
    )

    assert result.ok is True
    assert result.models == ("draft-model",)
    assert captured["url"] == "https://draft.test/v1/models"
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer saved-api-key"
    assert captured["timeout_seconds"] == pytest.approx(17.5)


def test_discover_models_supports_bigmodel_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"data": [{"id": "glm-4.5"}]}),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.BIGMODEL,
                base_url="https://open.bigmodel.cn/api/coding/paas/v4",
                api_key="draft-api-key",
            )
        )
    )

    assert result.ok is True
    assert result.provider == ProviderType.BIGMODEL
    assert result.models == ("glm-4.5",)
    assert captured["url"] == "https://open.bigmodel.cn/api/coding/paas/v4/models"


def test_discover_models_allows_header_only_override(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(200, json={"data": [{"id": "draft-model"}]}),
            )
        ),
    )

    result = service.discover_models(
        ModelDiscoveryRequest(
            override=ModelConnectivityProbeOverride(
                base_url="https://draft.test/v1",
                headers=(
                    ModelRequestHeader(
                        name="Authorization",
                        value="Bearer discovery-header",
                    ),
                ),
            )
        )
    )

    assert result.ok is True
    headers = cast(dict[str, str], captured["headers"])
    assert headers["Authorization"] == "Bearer discovery-header"


def test_discover_models_returns_invalid_response_error(monkeypatch) -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **_kwargs: _FakeHttpClient(
            response=httpx.Response(200, json={"items": [{"id": "missing-data"}]})
        ),
    )

    result = service.discover_models(ModelDiscoveryRequest(profile_name="default"))

    assert result.ok is False
    assert result.error_code == "invalid_response"
    assert result.retryable is False


def test_probe_maas_supports_event_stream_wrapped_json(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.get_maas_token_service",
        lambda: _FakeMaaSTokenService(["maas-token"], captured),
    )
    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured,
                response=httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=(
                        b'data: {"id":"cmpl-test","usage":{"total_tokens":3}}\n\n'
                        b"data: [DONE]\n\n"
                    ),
                ),
            )
        ),
    )

    result = service.probe(
        ModelConnectivityProbeRequest(
            override=ModelConnectivityProbeOverride(
                provider=ProviderType.MAAS,
                model="maas-chat",
                base_url="https://maas.example/api/v2",
                maas_auth=MaaSAuthConfig(
                    username="relay-user",
                    password="relay-password",
                ),
            )
        )
    )

    assert result.ok is True
    assert result.token_usage is not None
    assert result.token_usage.total_tokens == 3


def test_probe_requires_source_config() -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    with pytest.raises(ValueError, match="Provide profile_name, override, or both."):
        service.probe(ModelConnectivityProbeRequest())


def test_discover_models_requires_source_config() -> None:
    service = ModelConnectivityProbeService(get_runtime=lambda: _runtime_config())

    with pytest.raises(ValueError, match="Provide profile_name, override, or both."):
        service.discover_models(ModelDiscoveryRequest())


def test_probe_resolves_default_alias_to_runtime_default_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}
    service = ModelConnectivityProbeService(
        get_runtime=lambda: _runtime_config(
            profile_name="kimi",
            default_model_profile="kimi",
        )
    )

    monkeypatch.setattr(
        "relay_teams.providers.model_connectivity.create_sync_http_client",
        lambda **kwargs: (
            captured.update(kwargs)
            or _FakeHttpClient(
                captured=captured, response=httpx.Response(200, json={"usage": {}})
            )
        ),
    )

    result = service.probe(ModelConnectivityProbeRequest(profile_name="default"))

    assert result.ok is True
    assert captured["url"] == "https://example.test/v1/chat/completions"


def _runtime_config(
    *,
    profile_name: str = "default",
    default_model_profile: str | None = None,
    provider: ProviderType = ProviderType.OPENAI_COMPATIBLE,
    model: str = "saved-model",
    base_url: str = "https://example.test/v1",
    api_key: str | None = "saved-api-key",
    maas_auth: MaaSAuthConfig | None = None,
    codeagent_auth: CodeAgentAuthConfig | None = None,
) -> RuntimeConfig:
    config = ModelEndpointConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        maas_auth=maas_auth,
        codeagent_auth=codeagent_auth,
        ssl_verify=True,
        sampling=SamplingConfig(
            temperature=1.0,
            top_p=0.95,
            max_tokens=128,
        ),
        connect_timeout_seconds=17.5,
    )
    return RuntimeConfig(
        paths=RuntimePaths(
            config_dir=Path("D:/tmp/.agent_teams"),
            env_file=Path("D:/tmp/.agent_teams/.env"),
            db_path=Path("D:/tmp/.agent_teams/relay_teams.db"),
            roles_dir=Path("D:/tmp/.agent_teams/roles"),
        ),
        llm_profiles={profile_name: config},
        default_model_profile=default_model_profile or profile_name,
    )

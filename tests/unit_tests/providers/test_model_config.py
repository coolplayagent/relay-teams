from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.providers.model_config import (
    CodeAgentAuthMethod,
    CodeAgentAuthConfig,
    MaaSAuthConfig,
    ModelEndpointConfig,
    ProviderType,
)


def test_codeagent_auth_with_secret_owner_copies_private_metadata() -> None:
    auth_config = CodeAgentAuthConfig(refresh_token="refresh-token")

    copied = auth_config.with_secret_owner(
        config_dir=Path("C:/tmp/.agent-teams"),
        owner_id="codeagent-profile",
    )

    assert copied is not auth_config
    assert copied._secret_config_dir == Path("C:/tmp/.agent-teams")
    assert copied._secret_owner_id == "codeagent-profile"
    assert auth_config._secret_config_dir is None
    assert auth_config._secret_owner_id is None


def test_model_endpoint_config_requires_maas_auth() -> None:
    with pytest.raises(
        ValueError,
        match="MAAS model endpoint config requires maas_auth configuration.",
    ):
        ModelEndpointConfig(
            provider=ProviderType.MAAS,
            model="maas-chat",
            base_url="https://maas.example/api/v2",
        )


def test_model_endpoint_config_requires_maas_password() -> None:
    with pytest.raises(
        ValueError,
        match="MAAS model endpoint config requires maas_auth.password.",
    ):
        ModelEndpointConfig(
            provider=ProviderType.MAAS,
            model="maas-chat",
            base_url="https://maas.example/api/v2",
            maas_auth=MaaSAuthConfig(username="relay-user"),
        )


def test_model_endpoint_config_requires_codeagent_password_fields() -> None:
    with pytest.raises(
        ValueError,
        match="CodeAgent model endpoint config requires codeagent_auth.username and codeagent_auth.password for password auth.",
    ):
        ModelEndpointConfig(
            provider=ProviderType.CODEAGENT,
            model="codeagent-chat",
            base_url="https://codeagent.example/codeAgentPro",
            codeagent_auth=CodeAgentAuthConfig(
                auth_method=CodeAgentAuthMethod.PASSWORD,
                username="relay-user",
            ),
        )

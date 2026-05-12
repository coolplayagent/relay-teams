# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock, patch

from relay_teams.interfaces.server.deps import (
    get_general_config_service,
    get_llm_evaluator,
)


def test_get_llm_evaluator_returns_evaluator() -> None:
    fake_container = MagicMock()
    fake_container.resolve_auxiliary_model_config.return_value = MagicMock(
        model="gpt-4o"
    )
    fake_container.resolve_auxiliary_model_profile_name.return_value = "default"
    fake_provider = MagicMock()
    fake_container.create_provider.return_value = fake_provider
    fake_request = MagicMock()

    with patch(
        "relay_teams.interfaces.server.deps.get_container",
        return_value=fake_container,
    ):
        result = get_llm_evaluator(fake_request)
        assert result is not None
        fake_container.resolve_auxiliary_model_config.assert_called_once()
        fake_container.create_provider.assert_called_once()


def test_get_llm_evaluator_uses_default_model_when_no_config() -> None:
    fake_container = MagicMock()
    fake_container.resolve_auxiliary_model_config.return_value = None
    fake_container.resolve_auxiliary_model_profile_name.return_value = None
    fake_provider = MagicMock()
    fake_container.create_provider.return_value = fake_provider
    fake_request = MagicMock()

    with patch(
        "relay_teams.interfaces.server.deps.get_container",
        return_value=fake_container,
    ):
        result = get_llm_evaluator(fake_request)
        assert result is not None


def test_get_general_config_service_returns_container_service() -> None:
    fake_container = MagicMock()
    fake_request = MagicMock()

    with patch(
        "relay_teams.interfaces.server.deps.get_container",
        return_value=fake_container,
    ):
        result = get_general_config_service(fake_request)

    assert result is fake_container.general_config_service

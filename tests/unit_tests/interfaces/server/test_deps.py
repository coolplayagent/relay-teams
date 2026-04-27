# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import Mock

from fastapi import Request

from relay_teams.interfaces.server.deps import (
    get_xiaoluban_gateway_service,
    get_xiaoluban_im_listener_service,
)


def test_get_xiaoluban_im_listener_service_delegates_to_container() -> None:
    mock_listener = object()
    mock_container = Mock()
    mock_container.xiaoluban_im_listener_service = mock_listener
    mock_request = Mock(spec=Request)
    mock_request.app.state.container = mock_container

    result = get_xiaoluban_im_listener_service(mock_request)

    assert result is mock_listener


def test_get_xiaoluban_gateway_service_delegates_to_container() -> None:
    mock_service = object()
    mock_container = Mock()
    mock_container.xiaoluban_gateway_service = mock_service
    mock_request = Mock(spec=Request)
    mock_request.app.state.container = mock_container

    result = get_xiaoluban_gateway_service(mock_request)

    assert result is mock_service

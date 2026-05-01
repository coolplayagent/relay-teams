# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from relay_teams.gateway.xiaoluban.models import XiaolubanAccountCreateInput


class TestXiaolubanNormalizeTextRaises:
    """Cover the ``if normalized is None`` branch inside _normalize_text."""

    @patch(
        "relay_teams.gateway.xiaoluban.models.normalize_optional_string",
        return_value=None,
    )
    def test_normalize_text_raises_on_none(self, _mock: MagicMock) -> None:
        with pytest.raises(ValidationError):
            XiaolubanAccountCreateInput(
                account_id=None,
                display_name="test-name",
                token="test-token",
                base_url="https://example.com",
            )

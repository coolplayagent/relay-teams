from __future__ import annotations

import base64

from agent_teams.wechat.service import WeChatGatewayService


def test_normalize_qr_code_url_keeps_image_url() -> None:
    value = "https://example.test/qr.png"

    result = WeChatGatewayService._normalize_qr_code_url(value)

    assert result == value


def test_normalize_qr_code_url_renders_non_image_url_as_svg_data_uri() -> None:
    value = "https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=qr-token&bot_type=3"

    result = WeChatGatewayService._normalize_qr_code_url(value)

    assert result.startswith("data:image/svg+xml;base64,")
    encoded = result.removeprefix("data:image/svg+xml;base64,")
    decoded = base64.b64decode(encoded).decode("utf-8")
    assert decoded.startswith("<?xml")
    assert "<svg" in decoded


def test_normalize_qr_code_url_wraps_base64_png() -> None:
    result = WeChatGatewayService._normalize_qr_code_url("iVBORw0KGgoAAAANS")

    assert result == "data:image/png;base64,iVBORw0KGgoAAAANS"


def test_normalize_qr_code_url_wraps_base64_svg() -> None:
    result = WeChatGatewayService._normalize_qr_code_url("PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg==")

    assert result == (
        "data:image/svg+xml;base64,"
        "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciPjwvc3ZnPg=="
    )

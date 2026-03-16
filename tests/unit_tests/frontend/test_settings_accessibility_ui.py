# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_settings_markup_links_labels_to_controls_and_wraps_api_key_in_form() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    settings_source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")

    assert 'id="profile-editor-form"' in settings_source
    assert 'for="profile-name"' in settings_source
    assert 'for="profile-model"' in settings_source
    assert 'for="profile-base-url"' in settings_source
    assert 'for="profile-api-key"' in settings_source
    assert 'for="profile-temperature"' in settings_source
    assert 'for="profile-top-p"' in settings_source
    assert 'for="profile-max-tokens"' in settings_source
    assert 'for="profile-context-window"' in settings_source
    assert 'for="profile-connect-timeout"' in settings_source
    assert 'for="proxy-http-proxy"' in settings_source
    assert 'for="proxy-https-proxy"' in settings_source
    assert 'for="proxy-all-proxy"' in settings_source
    assert 'for="proxy-no-proxy"' in settings_source
    assert 'for="proxy-probe-url"' in settings_source
    assert 'for="proxy-probe-timeout"' in settings_source
    assert 'for="role-id-input"' in settings_source
    assert 'for="role-name-input"' in settings_source
    assert 'for="role-version-input"' in settings_source
    assert 'for="role-model-profile-input"' in settings_source
    assert 'for="role-memory-enabled-input"' in settings_source

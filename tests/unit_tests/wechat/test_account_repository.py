from __future__ import annotations

from pathlib import Path

from agent_teams.sessions.runs.run_models import RunThinkingConfig
from agent_teams.sessions.session_models import SessionMode
from agent_teams.gateway.wechat import WeChatAccountRecord, WeChatAccountRepository


def test_wechat_account_repository_round_trips_account_settings(
    tmp_path: Path,
) -> None:
    repository = WeChatAccountRepository(tmp_path / "wechat.db")
    created = repository.upsert_account(
        WeChatAccountRecord(
            account_id="wx_123",
            display_name="WeChat Main",
            base_url="https://wechat.example.test",
            cdn_base_url="https://cdn.example.test",
            route_tag="route-a",
            workspace_id="workspace-ops",
            session_mode=SessionMode.ORCHESTRATION,
            orchestration_preset_id="ops",
            yolo=False,
            thinking=RunThinkingConfig(enabled=True, effort="high"),
            sync_cursor="cursor-1",
        )
    )

    loaded = repository.get_account(created.account_id)

    assert loaded.account_id == "wx_123"
    assert loaded.display_name == "WeChat Main"
    assert loaded.route_tag == "route-a"
    assert loaded.workspace_id == "workspace-ops"
    assert loaded.session_mode == SessionMode.ORCHESTRATION
    assert loaded.orchestration_preset_id == "ops"
    assert loaded.yolo is False
    assert loaded.thinking.enabled is True
    assert loaded.thinking.effort == "high"
    assert loaded.sync_cursor == "cursor-1"


def test_wechat_account_repository_deletes_account(tmp_path: Path) -> None:
    repository = WeChatAccountRepository(tmp_path / "wechat.db")
    _ = repository.upsert_account(
        WeChatAccountRecord(
            account_id="wx_delete",
            display_name="Delete Me",
        )
    )

    repository.delete_account("wx_delete")

    assert repository.list_accounts() == ()

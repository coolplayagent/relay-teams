from __future__ import annotations

from agent_teams.feishu.client import FeishuClient
from agent_teams.feishu.models import FeishuEnvironment


class _FakeChatResponseData:
    def __init__(self, name: str | None) -> None:
        self.name = name


class _FakeChatResponse:
    def __init__(self, *, name: str | None, success: bool = True) -> None:
        self.data = _FakeChatResponseData(name) if name is not None else None
        self.msg = "ok" if success else "chat_error"
        self._success = success

    def success(self) -> bool:
        return self._success


class _FakeUser:
    def __init__(self, name: str | None) -> None:
        self.name = name


class _FakeUserResponseData:
    def __init__(self, name: str | None) -> None:
        self.user = _FakeUser(name) if name is not None else None


class _FakeUserResponse:
    def __init__(self, *, name: str | None, success: bool = True) -> None:
        self.data = _FakeUserResponseData(name) if name is not None else None
        self.msg = "ok" if success else "user_error"
        self._success = success

    def success(self) -> bool:
        return self._success


class _FakeChatResource:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, _FakeChatResponse] = {}

    def get(self, request: object) -> _FakeChatResponse:
        chat_id = str(getattr(request, "paths", {}).get("chat_id", ""))
        self.calls.append(chat_id)
        return self.responses[chat_id]


class _FakeUserResource:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, _FakeUserResponse] = {}

    def get(self, request: object) -> _FakeUserResponse:
        user_id = str(getattr(request, "paths", {}).get("user_id", ""))
        self.calls.append(user_id)
        return self.responses[user_id]


class _FakeSdkClient:
    def __init__(
        self,
        *,
        chat_resource: _FakeChatResource,
        user_resource: _FakeUserResource,
    ) -> None:
        self.im = type(
            "_ImService",
            (),
            {"v1": type("_ImV1", (), {"chat": chat_resource})()},
        )()
        self.contact = type(
            "_ContactService",
            (),
            {"v3": type("_ContactV3", (), {"user": user_resource})()},
        )()


def test_get_chat_name_uses_sdk_result_and_cache(monkeypatch) -> None:
    chat_resource = _FakeChatResource()
    user_resource = _FakeUserResource()
    chat_resource.responses["oc_group_1"] = _FakeChatResponse(name="Release Updates")
    client = FeishuClient()
    monkeypatch.setattr(
        client,
        "_sdk",
        lambda environment=None: _FakeSdkClient(
            chat_resource=chat_resource,
            user_resource=user_resource,
        ),
    )
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    first = client.get_chat_name(chat_id="oc_group_1", environment=environment)
    second = client.get_chat_name(chat_id="oc_group_1", environment=environment)

    assert first == "Release Updates"
    assert second == "Release Updates"
    assert chat_resource.calls == ["oc_group_1"]


def test_get_user_name_uses_sdk_result_and_cache(monkeypatch) -> None:
    chat_resource = _FakeChatResource()
    user_resource = _FakeUserResource()
    user_resource.responses["ou_user_1"] = _FakeUserResponse(name="Alice")
    client = FeishuClient()
    monkeypatch.setattr(
        client,
        "_sdk",
        lambda environment=None: _FakeSdkClient(
            chat_resource=chat_resource,
            user_resource=user_resource,
        ),
    )
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    first = client.get_user_name(open_id="ou_user_1", environment=environment)
    second = client.get_user_name(open_id="ou_user_1", environment=environment)

    assert first == "Alice"
    assert second == "Alice"
    assert user_resource.calls == ["ou_user_1"]


def test_get_chat_name_raises_runtime_error_for_failed_response(monkeypatch) -> None:
    chat_resource = _FakeChatResource()
    user_resource = _FakeUserResource()
    chat_resource.responses["oc_group_2"] = _FakeChatResponse(name=None, success=False)
    client = FeishuClient()
    monkeypatch.setattr(
        client,
        "_sdk",
        lambda environment=None: _FakeSdkClient(
            chat_resource=chat_resource,
            user_resource=user_resource,
        ),
    )
    environment = FeishuEnvironment(app_id="cli_1", app_secret="secret", app_name="bot")

    try:
        client.get_chat_name(chat_id="oc_group_2", environment=environment)
    except RuntimeError as exc:
        assert "chat_error" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")

# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agent_runtimes.models import (
    ExternalAgentSessionRecord,
    ExternalAgentTransportType,
)
from relay_teams.agent_runtimes.session_repository import (
    ExternalAgentSessionRepository,
)
from relay_teams.gateway.feishu.account_repository import (
    FeishuAccountNameConflictError,
    FeishuAccountRepository,
)
from relay_teams.gateway.feishu.message_pool_repository import (
    FeishuMessagePoolRepository,
)
from relay_teams.gateway.feishu.models import (
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountStatus,
    FeishuMessageDeliveryStatus,
    FeishuMessagePoolRecord,
    FeishuMessageProcessingStatus,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
)
from relay_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from relay_teams.gateway.gateway_session_repository import GatewaySessionRepository
from relay_teams.gateway.wechat.account_repository import WeChatAccountRepository
from relay_teams.gateway.wechat.inbound_queue_repository import (
    WeChatInboundQueueRepository,
)
from relay_teams.gateway.wechat.models import (
    WeChatAccountRecord,
    WeChatInboundQueueRecord,
    WeChatInboundQueueStatus,
)
from relay_teams.gateway.xiaoluban.account_repository import (
    XiaolubanAccountRepository,
)
from relay_teams.gateway.xiaoluban.models import XiaolubanAccountRecord
from relay_teams.media import (
    MediaAssetRecord,
    MediaAssetRepository,
    MediaAssetStorageKind,
    MediaModality,
)
from relay_teams.monitors.models import (
    MonitorAction,
    MonitorActionType,
    MonitorEventEnvelope,
    MonitorRule,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
    MonitorTriggerRecord,
)
from relay_teams.monitors.repository import MonitorRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.retrieval import (
    RetrievalDocument,
    RetrievalQuery,
    RetrievalScopeConfig,
    RetrievalScopeKind,
    RetrievalTokenizer,
    SqliteFts5RetrievalStore,
)
from relay_teams.sessions.external_session_binding_repository import (
    ExternalSessionBindingRepository,
)
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import IntentInput, RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.user_question_models import (
    UserQuestionAnswer,
    UserQuestionOption,
    UserQuestionPrompt,
    UserQuestionRequestStatus,
    UserQuestionSelection,
)
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.sessions.session_history_marker_models import (
    SessionHistoryMarkerType,
)
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRepository,
)
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
    ShellApprovalScope,
)
from relay_teams.tools.workspace_tools.shell_policy import ShellRuntimeFamily
from relay_teams.triggers.models import (
    GitHubActionSpec,
    GitHubActionType,
    GitHubRepoSubscriptionRecord,
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountStatus,
    GitHubTriggerRunTemplate,
    GitHubWebhookStatus,
    TriggerActionAttemptRecord,
    TriggerActionPhase,
    TriggerActionStatus,
    TriggerDeliveryIngestStatus,
    TriggerDeliveryRecord,
    TriggerDeliverySignatureStatus,
    TriggerDispatchConfig,
    TriggerDispatchRecord,
    TriggerDispatchStatus,
    TriggerEvaluationRecord,
    TriggerProvider,
    TriggerRuleMatchConfig,
    TriggerRuleRecord,
    TriggerTargetType,
)
from relay_teams.triggers.repository import (
    GitHubRepoSubscriptionConflictError,
    GitHubTriggerAccountNameConflictError,
    TriggerDeliveryConflictError,
    TriggerRepository,
    TriggerRuleNameConflictError,
)
from relay_teams.workspace import (
    SshProfileRepository,
    SshProfileStoredConfig,
    WorkspaceLocalMountConfig,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceRepository,
    WorkspaceSshMountConfig,
)


@pytest.mark.asyncio
async def test_small_repository_async_paths_share_state(tmp_path: Path) -> None:
    now = datetime.now(tz=timezone.utc)

    external_repo = ExternalAgentSessionRepository(tmp_path / "external-agent.db")
    external_record = ExternalAgentSessionRecord(
        session_id="session-1",
        role_id="role-1",
        agent_id="agent-1",
        transport=ExternalAgentTransportType.STDIO,
        external_session_id="external-1",
    )
    await external_repo.upsert_async(external_record)
    loaded_external = await external_repo.get_async(
        session_id="session-1",
        role_id="role-1",
        agent_id="agent-1",
    )
    await external_repo.delete_async(
        session_id="session-1",
        role_id="role-1",
        agent_id="agent-1",
    )

    feishu_repo = FeishuAccountRepository(tmp_path / "feishu-account.db")
    feishu_record = FeishuGatewayAccountRecord(
        account_id="feishu-1",
        name="feishu-main",
        display_name="Feishu Main",
        status=FeishuGatewayAccountStatus.ENABLED,
        source_config=FeishuTriggerSourceConfig(
            app_id="cli_a",
            app_name="Relay",
        ).model_dump(mode="json"),
        target_config=FeishuTriggerTargetConfig().model_dump(mode="json"),
        secret_config={"app_secret": "secret"},
        secret_status={"app_secret_configured": True},
        created_at=now,
        updated_at=now,
    )
    await feishu_repo.create_account_async(feishu_record)
    feishu_updated = await feishu_repo.update_account_async(
        feishu_record.model_copy(update={"display_name": "Feishu Ops"})
    )
    feishu_loaded = await feishu_repo.get_account_async("feishu-1")
    feishu_accounts = await feishu_repo.list_accounts_async()
    await feishu_repo.delete_account_async("feishu-1")

    wechat_repo = WeChatAccountRepository(tmp_path / "wechat-account.db")
    wechat_record = WeChatAccountRecord(
        account_id="wechat-1",
        display_name="WeChat Main",
        remote_user_id="wxid_1",
    )
    await wechat_repo.upsert_account_async(wechat_record)
    wechat_loaded = await wechat_repo.get_account_async("wechat-1")
    wechat_accounts = await wechat_repo.list_accounts_async()
    await wechat_repo.delete_account_async("wechat-1")

    xiaoluban_repo = XiaolubanAccountRepository(tmp_path / "xiaoluban-account.db")
    xiaoluban_record = XiaolubanAccountRecord(
        account_id="xiaoluban-1",
        display_name="Xiaoluban Main",
        derived_uid="uid-1",
        notification_workspace_ids=("default",),
        notification_receivers=("user-1",),
    )
    await xiaoluban_repo.upsert_account_async(xiaoluban_record)
    xiaoluban_loaded = await xiaoluban_repo.get_account_async("xiaoluban-1")
    xiaoluban_accounts = await xiaoluban_repo.list_accounts_async()
    await xiaoluban_repo.delete_account_async("xiaoluban-1")

    media_repo = MediaAssetRepository(tmp_path / "media.db")
    media_record = MediaAssetRecord(
        asset_id="asset-1",
        session_id="session-1",
        workspace_id="default",
        storage_kind=MediaAssetStorageKind.LOCAL,
        modality=MediaModality.IMAGE,
        mime_type="image/png",
        name="chart.png",
        relative_path="media/chart.png",
        size_bytes=42,
        width=10,
        height=10,
        source="test",
    )
    await media_repo.upsert_async(media_record)
    media_loaded = await media_repo.get_async("asset-1")
    media_by_session = await media_repo.list_by_session_async("session-1")
    await media_repo.delete_by_session_async("session-1")

    binding_repo = ExternalSessionBindingRepository(tmp_path / "bindings.db")
    binding = await binding_repo.upsert_binding_async(
        platform="feishu",
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
        session_id="session-1",
    )
    loaded_binding = await binding_repo.get_binding_async(
        platform="feishu",
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
    )
    platform_bindings = await binding_repo.list_by_platform_async("feishu")
    binding_exists = await binding_repo.exists_async(
        platform="feishu",
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        external_chat_id="chat-1",
    )
    await binding_repo.delete_by_trigger_async("trigger-1")

    gateway_repo = GatewaySessionRepository(tmp_path / "gateway.db")
    gateway_record = GatewaySessionRecord(
        gateway_session_id="gateway-1",
        channel_type=GatewayChannelType.WECHAT,
        external_session_id="wechat:wxid_1:user_1",
        internal_session_id="session-1",
        peer_user_id="user-1",
        capabilities={"filesystem": True},
    )
    await gateway_repo.create_async(gateway_record)
    gateway_updated = await gateway_repo.update_async(
        gateway_record.model_copy(update={"active_run_id": "run-1"})
    )
    gateway_loaded = await gateway_repo.get_async("gateway-1")
    gateway_by_external = await gateway_repo.get_by_external_async(
        channel_type=GatewayChannelType.WECHAT,
        external_session_id="wechat:wxid_1:user_1",
    )
    gateway_by_session = await gateway_repo.get_by_internal_session_id_async(
        "session-1"
    )
    gateway_all = await gateway_repo.list_all_async()

    ssh_repo = SshProfileRepository(tmp_path / "ssh.db")
    ssh_record = await ssh_repo.save_async(
        ssh_profile_id="prod",
        config=SshProfileStoredConfig(
            host="example.com",
            username="deploy",
            port=22,
            private_key_name="prod-key",
        ),
    )
    ssh_loaded = await ssh_repo.get_async("prod")
    ssh_exists = await ssh_repo.exists_async("prod")
    ssh_all = await ssh_repo.list_all_async()
    await ssh_repo.delete_async("prod")

    shell_repo = ShellApprovalRepository(tmp_path / "shell-approval.db")
    exact = await shell_repo.grant_async(
        workspace_key="default",
        runtime_family=ShellRuntimeFamily.BASH,
        scope=ShellApprovalScope.EXACT,
        value="git status",
    )
    await shell_repo.grant_async(
        workspace_key="default",
        runtime_family=ShellRuntimeFamily.BASH,
        scope=ShellApprovalScope.PREFIX,
        value="git",
    )
    shell_loaded = await shell_repo.get_async(
        workspace_key="default",
        runtime_family=ShellRuntimeFamily.BASH,
        scope=ShellApprovalScope.EXACT,
        value="git status",
    )
    has_exact = await shell_repo.has_exact_grant_async(
        workspace_key="default",
        runtime_family=ShellRuntimeFamily.BASH,
        normalized_command="git status",
    )
    has_prefix = await shell_repo.has_prefix_grants_async(
        workspace_key="default",
        runtime_family=ShellRuntimeFamily.BASH,
        prefix_candidates=("git",),
    )

    workspace_repo = WorkspaceRepository(tmp_path / "workspace.db")
    app_root = tmp_path / "app"
    app_root.mkdir()
    created_workspace = await workspace_repo.create_async(
        workspace_id="workspace-1",
        default_mount_name="app",
        mounts=(
            WorkspaceMountRecord(
                mount_name="app",
                provider=WorkspaceMountProvider.LOCAL,
                provider_config=WorkspaceLocalMountConfig(root_path=app_root),
            ),
        ),
    )
    remote_mount = WorkspaceMountRecord(
        mount_name="prod",
        provider=WorkspaceMountProvider.SSH,
        provider_config=WorkspaceSshMountConfig(
            ssh_profile_id="prod",
            remote_root="/srv/app",
        ),
    )
    updated_workspace = await workspace_repo.update_async(
        workspace_id="workspace-1",
        default_mount_name="prod",
        mounts=(remote_mount,),
    )
    fetched_workspace = await workspace_repo.get_async("workspace-1")
    workspace_all = await workspace_repo.list_all_async()
    workspace_exists = await workspace_repo.exists_async("workspace-1")
    await workspace_repo.delete_async("workspace-1")

    assert loaded_external is not None
    assert loaded_external.external_session_id == "external-1"
    assert feishu_updated.display_name == "Feishu Ops"
    assert feishu_loaded.account_id == "feishu-1"
    assert tuple(record.account_id for record in feishu_accounts) == ("feishu-1",)
    assert wechat_loaded.remote_user_id == "wxid_1"
    assert tuple(record.account_id for record in wechat_accounts) == ("wechat-1",)
    assert xiaoluban_loaded.derived_uid == "uid-1"
    assert tuple(record.account_id for record in xiaoluban_accounts) == ("xiaoluban-1",)
    assert media_loaded.asset_id == "asset-1"
    assert tuple(record.asset_id for record in media_by_session) == ("asset-1",)
    assert binding.session_id == "session-1"
    assert loaded_binding is not None
    assert loaded_binding.session_id == "session-1"
    assert tuple(record.session_id for record in platform_bindings) == ("session-1",)
    assert binding_exists is True
    assert gateway_updated.active_run_id == "run-1"
    assert gateway_loaded.gateway_session_id == "gateway-1"
    assert gateway_by_external is not None
    assert gateway_by_session is not None
    assert tuple(record.gateway_session_id for record in gateway_all) == ("gateway-1",)
    assert ssh_record.ssh_profile_id == "prod"
    assert ssh_loaded.host == "example.com"
    assert ssh_exists is True
    assert tuple(record.ssh_profile_id for record in ssh_all) == ("prod",)
    assert exact.value == "git status"
    assert shell_loaded is not None
    assert has_exact is True
    assert has_prefix is True
    assert created_workspace.workspace_id == "workspace-1"
    assert updated_workspace.default_mount_name == "prod"
    assert fetched_workspace.default_mount.provider == WorkspaceMountProvider.SSH
    assert tuple(record.workspace_id for record in workspace_all) == ("workspace-1",)
    assert workspace_exists is True


@pytest.mark.asyncio
async def test_small_repository_async_edge_paths(tmp_path: Path) -> None:
    now = datetime.now(tz=timezone.utc)

    external_repo = ExternalSessionBindingRepository(tmp_path / "bindings-edge.db")
    missing_binding = await external_repo.get_binding_async(
        platform="feishu",
        trigger_id="missing",
        tenant_key="tenant",
        external_chat_id="chat",
    )
    missing_binding_exists = await external_repo.exists_async(
        platform="feishu",
        trigger_id="missing",
        tenant_key="tenant",
        external_chat_id="chat",
    )
    await external_repo.delete_by_session_async("missing-session")

    feishu_repo = FeishuAccountRepository(tmp_path / "feishu-edge.db")
    feishu_record = FeishuGatewayAccountRecord(
        account_id="feishu-1",
        name="feishu-main",
        display_name="Feishu Main",
        status=FeishuGatewayAccountStatus.ENABLED,
        source_config=FeishuTriggerSourceConfig(
            app_id="cli_a",
            app_name="Relay",
        ).model_dump(mode="json"),
        target_config=FeishuTriggerTargetConfig().model_dump(mode="json"),
        created_at=now,
        updated_at=now,
    )
    await feishu_repo.create_account_async(feishu_record)
    with pytest.raises(FeishuAccountNameConflictError):
        await feishu_repo.create_account_async(
            feishu_record.model_copy(update={"account_id": "feishu-duplicate"})
        )
    feishu_other = feishu_record.model_copy(
        update={
            "account_id": "feishu-2",
            "name": "feishu-other",
            "display_name": "Feishu Other",
        }
    )
    await feishu_repo.create_account_async(feishu_other)
    with pytest.raises(FeishuAccountNameConflictError):
        await feishu_repo.update_account_async(
            feishu_other.model_copy(update={"name": "feishu-main"})
        )
    with pytest.raises(KeyError):
        await feishu_repo.get_account_async("missing")

    wechat_repo = WeChatAccountRepository(tmp_path / "wechat-edge.db")
    with pytest.raises(KeyError):
        await wechat_repo.get_account_async("missing")

    xiaoluban_repo = XiaolubanAccountRepository(tmp_path / "xiaoluban-edge.db")
    with pytest.raises(KeyError):
        await xiaoluban_repo.get_account_async("missing")

    gateway_repo = GatewaySessionRepository(tmp_path / "gateway-edge.db")
    missing_gateway = GatewaySessionRecord(
        gateway_session_id="missing",
        channel_type=GatewayChannelType.WECHAT,
        external_session_id="wechat:missing",
        internal_session_id="session-missing",
    )
    with pytest.raises(KeyError):
        await gateway_repo.get_async("missing")
    missing_by_external = await gateway_repo.get_by_external_async(
        channel_type=GatewayChannelType.WECHAT,
        external_session_id="wechat:missing",
    )
    missing_by_session = await gateway_repo.get_by_internal_session_id_async(
        "session-missing"
    )
    with pytest.raises(KeyError):
        await gateway_repo.update_async(missing_gateway)

    ssh_repo = SshProfileRepository(tmp_path / "ssh-edge.db")
    with pytest.raises(KeyError):
        await ssh_repo.get_async("missing")

    shell_repo = ShellApprovalRepository(tmp_path / "shell-edge.db")
    with pytest.raises(ValueError, match="must not be empty"):
        await shell_repo.grant_async(
            workspace_key="default",
            runtime_family=ShellRuntimeFamily.BASH,
            scope=ShellApprovalScope.EXACT,
            value="   ",
        )
    missing_shell = await shell_repo.get_async(
        workspace_key="default",
        runtime_family=ShellRuntimeFamily.BASH,
        scope=ShellApprovalScope.EXACT,
        value="missing",
    )
    empty_prefix_grant = await shell_repo.has_prefix_grants_async(
        workspace_key="default",
        runtime_family=ShellRuntimeFamily.BASH,
        prefix_candidates=(),
    )

    workspace_repo = WorkspaceRepository(tmp_path / "workspace-edge.db")
    with pytest.raises(ValueError, match="root_path or mounts"):
        await workspace_repo.create_async(workspace_id="missing")
    with pytest.raises(KeyError):
        await workspace_repo.get_async("missing")
    missing_root = tmp_path / "missing-root"
    missing_root.mkdir()
    with pytest.raises(KeyError):
        await workspace_repo.update_async(
            workspace_id="missing",
            default_mount_name="app",
            mounts=(
                WorkspaceMountRecord(
                    mount_name="app",
                    provider=WorkspaceMountProvider.LOCAL,
                    provider_config=WorkspaceLocalMountConfig(root_path=missing_root),
                ),
            ),
        )

    assert missing_binding is None
    assert missing_binding_exists is False
    assert missing_by_external is None
    assert missing_by_session is None
    assert missing_shell is None
    assert empty_prefix_grant is False


@pytest.mark.asyncio
async def test_runtime_repository_async_paths_share_state(tmp_path: Path) -> None:
    background_repo = BackgroundTaskRepository(tmp_path / "background.db")
    running_task = BackgroundTaskRecord(
        background_task_id="background-1",
        run_id="run-1",
        session_id="session-1",
        command="sleep 30",
        cwd="/tmp/project",
        status=BackgroundTaskStatus.RUNNING,
        pid=123,
        log_path="background-1.log",
    )
    await background_repo.upsert_async(running_task)
    loaded_background = await background_repo.get_async("background-1")
    backgrounds_by_run = await background_repo.list_by_run_async("run-1")
    backgrounds_by_session = await background_repo.list_by_session_async("session-1")
    backgrounds_by_sessions = await background_repo.list_by_session_ids_async(
        ("session-1", "session-missing")
    )
    all_backgrounds = await background_repo.list_all_async()
    interruptible = await background_repo.list_interruptible_async()
    interrupted_count = (
        await background_repo.mark_transient_background_tasks_interrupted_async()
    )
    await background_repo.delete_async("background-1")

    intent_repo = RunIntentRepository(tmp_path / "intent.db")
    intent = IntentInput(session_id="session-1")
    intent.intent = "Summarize async repository status"
    await intent_repo.upsert_async(
        run_id="run-1",
        session_id="session-1",
        intent=intent,
    )
    await intent_repo.append_followup_async(
        run_id="run-1",
        content="include validation",
    )
    loaded_intent = await intent_repo.get_async("run-1")
    intents_by_session = await intent_repo.list_by_session_async("session-1")
    first_intents = await intent_repo.first_by_session_ids_async(("session-1",))
    first_titles = await intent_repo.first_titles_by_session_ids_async(("session-1",))

    runtime_repo = RunRuntimeRepository(tmp_path / "runtime.db")
    runtime = RunRuntimeRecord(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    await runtime_repo.upsert_async(runtime)
    ensured_runtime = await runtime_repo.ensure_async(
        run_id="run-2",
        session_id="session-1",
        status=RunRuntimeStatus.QUEUED,
        phase=RunRuntimePhase.IDLE,
    )
    updated_runtime = await runtime_repo.update_async(
        "run-1",
        status=RunRuntimeStatus.STOPPING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    loaded_runtime = await runtime_repo.get_async("run-1")
    runtimes_by_session_ids = await runtime_repo.list_by_session_ids_async(
        ("session-1", "session-missing")
    )
    runtimes_by_session = await runtime_repo.list_by_session_async("session-1")
    recoverable_runtimes = await runtime_repo.list_recoverable_async()
    interrupted_runs = await runtime_repo.mark_transient_runs_interrupted_async()
    await runtime_repo.delete_async("run-2")

    question_repo = UserQuestionRepository(tmp_path / "questions.db")
    question = UserQuestionPrompt(
        question="Proceed?",
        options=(UserQuestionOption(label="Yes", description="Continue"),),
    )
    await question_repo.upsert_requested_async(
        question_id="question-1",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="coordinator",
        tool_name="ask_question",
        questions=(question,),
    )
    open_question_count = await question_repo.count_open_by_run_ids_async(("run-1",))
    questions_by_run = await question_repo.list_by_run_async("run-1")
    questions_by_session = await question_repo.list_by_session_async("session-1")
    resolved_question = await question_repo.resolve_async(
        question_id="question-1",
        status=UserQuestionRequestStatus.ANSWERED,
        answers=(
            UserQuestionAnswer(
                selections=(UserQuestionSelection(label="Yes"),),
            ),
        ),
        expected_status=UserQuestionRequestStatus.REQUESTED,
    )
    completed_question = await question_repo.mark_completed_async("question-1")
    await question_repo.delete_by_run_async("run-1")

    approval_repo = ApprovalTicketRepository(tmp_path / "approvals.db")
    await approval_repo.upsert_requested_async(
        tool_call_id="call-1",
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="writer",
        tool_name="shell",
        args_preview='{"command": "git status"}',
    )
    open_approval_count = await approval_repo.count_open_by_run_ids_async(("run-1",))
    approvals_by_session = await approval_repo.list_open_by_session_async("session-1")
    await approval_repo.delete_by_session_async("session-1")

    marker_repo = SessionHistoryMarkerRepository(tmp_path / "markers.db")
    marker = await marker_repo.create_async(
        session_id="session-1",
        marker_type=SessionHistoryMarkerType.COMPACTION,
        metadata={"conversation_id": "conversation-1"},
    )
    clear_marker = await marker_repo.create_clear_marker_async("session-1")
    markers = await marker_repo.list_by_session_async("session-1")
    latest_clear = await marker_repo.get_latest_async(
        "session-1",
        marker_type=SessionHistoryMarkerType.CLEAR,
    )
    await marker_repo.delete_by_conversation_async("session-1", "conversation-1")

    event_log = EventLog(tmp_path / "events.db")
    await event_log.emit_async(
        EventEnvelope(
            event_type=EventType.TASK_CREATED,
            trace_id="run-1",
            session_id="session-1",
            task_id="task-1",
            instance_id="inst-1",
        )
    )
    run_event_id = await event_log.emit_run_event_async(
        RunEvent(
            event_type=RunEventType.RUN_STARTED,
            trace_id="run-1",
            run_id="run-1",
            session_id="session-1",
            task_id="task-1",
            instance_id="inst-1",
            payload_json='{"ok": true}',
        )
    )
    events_by_trace = await event_log.list_by_trace_async("run-1")
    events_after = await event_log.list_by_trace_after_id_async("run-1", 0)
    run_states = await event_log.list_run_states_async()
    await event_log.delete_by_session_async("session-1")

    assert loaded_background is not None
    assert loaded_background.background_task_id == "background-1"
    assert tuple(record.background_task_id for record in backgrounds_by_run) == (
        "background-1",
    )
    assert tuple(record.background_task_id for record in backgrounds_by_session) == (
        "background-1",
    )
    assert tuple(
        record.background_task_id for record in backgrounds_by_sessions["session-1"]
    ) == ("background-1",)
    assert tuple(record.background_task_id for record in all_backgrounds) == (
        "background-1",
    )
    assert tuple(record.background_task_id for record in interruptible) == (
        "background-1",
    )
    assert interrupted_count == 1
    assert loaded_intent is not None
    assert loaded_intent.display_intent.startswith("Summarize async repository status")
    assert "include validation" in loaded_intent.display_intent
    assert tuple(intents_by_session) == ("run-1",)
    assert first_intents["session-1"].display_intent.startswith(
        "Summarize async repository status"
    )
    assert first_titles["session-1"].startswith("Summarize async repository status")
    assert ensured_runtime.run_id == "run-2"
    assert updated_runtime.status == RunRuntimeStatus.STOPPING
    assert loaded_runtime is not None
    assert loaded_runtime.phase == RunRuntimePhase.COORDINATOR_RUNNING
    assert {record.run_id for record in runtimes_by_session_ids["session-1"]} == {
        "run-1",
        "run-2",
    }
    assert tuple(record.run_id for record in runtimes_by_session) == (
        "run-1",
        "run-2",
    )
    assert tuple(record.run_id for record in recoverable_runtimes) == ("run-2",)
    assert interrupted_runs == 1
    assert open_question_count == {"run-1": 1}
    assert tuple(record.question_id for record in questions_by_run) == ("question-1",)
    assert tuple(record.question_id for record in questions_by_session) == (
        "question-1",
    )
    assert resolved_question.status == UserQuestionRequestStatus.ANSWERED
    assert completed_question is not None
    assert completed_question.status == UserQuestionRequestStatus.COMPLETED
    assert open_approval_count == {"run-1": 1}
    assert tuple(record.tool_call_id for record in approvals_by_session) == ("call-1",)
    assert marker.marker_type == SessionHistoryMarkerType.COMPACTION
    assert clear_marker.marker_type == SessionHistoryMarkerType.CLEAR
    assert tuple(record.marker_id for record in markers) == (
        marker.marker_id,
        clear_marker.marker_id,
    )
    assert latest_clear is not None
    assert latest_clear.marker_id == clear_marker.marker_id
    assert run_event_id > 0
    assert len(events_by_trace) == 2
    assert len(events_after) == 2
    assert tuple(record.run_id for record in run_states) == ("run-1",)


@pytest.mark.asyncio
async def test_message_repository_async_projection_and_mutation_paths(
    tmp_path: Path,
) -> None:
    marker_repo = SessionHistoryMarkerRepository(tmp_path / "message-markers.db")
    repo = MessageRepository(
        tmp_path / "messages.db",
        session_history_marker_repo=marker_repo,
    )

    await repo.append_async(
        session_id="session-1",
        workspace_id="default",
        conversation_id="conversation-1",
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=(ModelRequest(parts=[UserPromptPart(content="first prompt")]),),
    )
    await repo.append_async(
        session_id="session-1",
        workspace_id="default",
        conversation_id="conversation-1",
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=(ModelResponse(parts=[TextPart(content="first answer")]),),
    )
    await repo.append_user_prompt_if_missing_async(
        session_id="session-1",
        workspace_id="default",
        conversation_id="conversation-1",
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-2",
        trace_id="run-2",
        content="pending prompt",
    )
    replaced = await repo.replace_pending_user_prompt_async(
        session_id="session-1",
        workspace_id="default",
        conversation_id="conversation-1",
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-2",
        trace_id="run-2",
        content="replacement prompt",
    )
    system_added = await repo.append_system_prompt_if_missing_async(
        session_id="session-1",
        workspace_id="default",
        conversation_id="conversation-1",
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-2",
        trace_id="run-2",
        content="system prompt",
    )
    await repo.append_async(
        session_id="session-2",
        workspace_id="default",
        conversation_id="conversation-2",
        agent_role_id="reviewer",
        instance_id="inst-2",
        task_id="task-3",
        trace_id="run-3",
        messages=(ModelRequest(parts=[UserPromptPart(content="other prompt")]),),
    )

    latest_task_message_id = await repo.get_latest_task_message_id_async(
        task_id="task-1",
        instance_id="inst-1",
    )
    first_messages = await repo.first_user_messages_by_session_ids_async(
        ("session-1", "session-2")
    )
    by_run = await repo.get_messages_by_session_run_ids_async(
        "session-1",
        ("run-1", "run-2", "run-1"),
    )
    by_session = await repo.get_messages_by_session_async("session-1")
    users_by_session = await repo.get_user_messages_by_session_async("session-1")
    by_instance = await repo.get_messages_for_instance_async("session-1", "inst-1")
    history = await repo.get_history_async("inst-1")
    conversation_history = await repo.get_history_for_conversation_async(
        "conversation-1"
    )
    task_history = await repo.get_history_for_task_async("inst-1", "task-2")
    conversation_task_history = await repo.get_history_for_conversation_task_async(
        "conversation-1",
        "task-2",
    )

    hidden_count = await repo.hide_conversation_messages_for_compaction_async(
        conversation_id="conversation-1",
        hide_message_count=1,
        hidden_marker_id="marker-1",
    )
    hidden_messages = await repo.get_messages_by_session_async(
        "session-1",
        include_hidden_from_context=True,
    )
    await repo.compact_conversation_history_async(
        "conversation-1",
        keep_message_count=1,
        hidden_marker_id="marker-2",
    )
    await marker_repo.create_clear_marker_async("session-1")
    await repo.append_user_prompt_if_missing_async(
        session_id="session-1",
        workspace_id="default",
        conversation_id="conversation-1",
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-4",
        trace_id="run-4",
        content="after clear",
    )
    active_after_clear = await repo.get_messages_by_session_async("session-1")
    await repo.prune_history_to_safe_boundary_async("inst-1")
    await repo.prune_conversation_history_to_safe_boundary_async("conversation-1")
    await repo.delete_by_instance_async("inst-2")
    await repo.delete_by_session_async("session-2")

    assert replaced is True
    assert system_added is True
    assert latest_task_message_id > 0
    assert set(first_messages) == {"session-1", "session-2"}
    assert [message["trace_id"] for message in by_run] == [
        "run-1",
        "run-1",
        "run-2",
        "run-2",
    ]
    assert len(by_session) >= 4
    assert len(users_by_session) >= 2
    assert len(by_instance) >= 4
    assert len(history) >= 4
    assert len(conversation_history) >= 4
    assert len(task_history) == 2
    assert len(conversation_task_history) == 2
    assert hidden_count == 1
    assert any(message["hidden_from_context"] for message in hidden_messages)
    assert [message["trace_id"] for message in active_after_clear] == ["run-4"]


@pytest.mark.asyncio
async def test_message_repository_async_edge_paths(tmp_path: Path) -> None:
    repo = MessageRepository(tmp_path / "messages-edge.db")

    await repo.append_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-empty",
        trace_id="run-empty",
        messages=(),
    )
    empty_runs = await repo.get_messages_by_session_run_ids_async(
        "session-edge",
        ("", "   "),
    )
    empty_first_messages = await repo.first_user_messages_by_session_ids_async(())
    empty_replace = await repo.replace_pending_user_prompt_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-empty",
        trace_id="run-empty",
        content="",
    )
    empty_system = await repo.append_system_prompt_if_missing_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-empty",
        trace_id="run-empty",
        content="   ",
    )
    zero_hide = await repo.hide_conversation_messages_for_compaction_async(
        conversation_id="conversation-edge",
        hide_message_count=0,
        hidden_marker_id="marker-zero",
    )
    empty_hide = await repo.hide_conversation_messages_for_compaction_async(
        conversation_id="conversation-empty",
        hide_message_count=1,
        hidden_marker_id="marker-empty",
    )

    await repo.append_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-response",
        trace_id="run-response",
        messages=(ModelResponse(parts=[TextPart(content="already answered")]),),
    )
    response_replace = await repo.replace_pending_user_prompt_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-response",
        trace_id="run-response",
        content="new prompt",
    )

    for prompt in ("pending one", "pending two"):
        await repo.append_async(
            session_id="session-edge",
            workspace_id="default",
            conversation_id="conversation-edge",
            agent_role_id="writer",
            instance_id="inst-edge",
            task_id="task-pending",
            trace_id="run-pending",
            messages=(ModelRequest(parts=[UserPromptPart(content=prompt)]),),
        )
    replaced_pending = await repo.replace_pending_user_prompt_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-pending",
        trace_id="run-pending",
        content="replacement",
    )
    duplicate_prompt = await repo.append_user_prompt_if_missing_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-pending",
        trace_id="run-pending",
        content="replacement",
    )
    first_system = await repo.append_system_prompt_if_missing_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-system",
        trace_id="run-system",
        content="system prompt",
    )
    duplicate_system = await repo.append_system_prompt_if_missing_async(
        session_id="session-edge",
        workspace_id="default",
        conversation_id="conversation-edge",
        agent_role_id="writer",
        instance_id="inst-edge",
        task_id="task-system",
        trace_id="run-system",
        content="system prompt",
    )
    pending_messages = await repo.get_user_messages_by_session_async("session-edge")

    assert empty_runs == []
    assert empty_first_messages == {}
    assert empty_replace is False
    assert empty_system is False
    assert zero_hide == 0
    assert empty_hide == 0
    assert response_replace is False
    assert replaced_pending is True
    assert duplicate_prompt is False
    assert first_system is True
    assert duplicate_system is False
    assert [
        message["task_id"]
        for message in pending_messages
        if message["task_id"] == "task-pending"
    ] == ["task-pending"]


@pytest.mark.asyncio
async def test_retrieval_store_async_paths_keep_indexes_in_sync(
    tmp_path: Path,
) -> None:
    store = SqliteFts5RetrievalStore(tmp_path / "retrieval-async.db")
    skill_config = RetrievalScopeConfig(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="skills",
    )
    memory_config = RetrievalScopeConfig(
        scope_kind=RetrievalScopeKind.MEMORY,
        scope_id="memories",
        tokenizer=RetrievalTokenizer.TRIGRAM,
    )

    replace_stats = await store.replace_scope_async(
        config=skill_config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="router",
                title="Router",
                body="skill catalog routing",
                keywords=("skill", "route"),
            ),
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="memory",
                title="Memory",
                body="memory storage",
                keywords=("memory",),
            ),
        ),
    )
    await store.upsert_documents_async(
        config=skill_config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="router",
                title="Router Guide",
                body="skill catalog routing and search",
                keywords=("skill", "search"),
            ),
        ),
    )
    hits = await store.search_async(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="skills",
            text="search",
            limit=5,
        )
    )
    delete_stats = await store.delete_documents_async(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="skills",
        document_ids=("memory",),
    )
    await store.replace_scope_async(
        config=memory_config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.MEMORY,
                scope_id="memories",
                document_id="zh",
                title="中文检索",
                body="这是中文检索测试",
            ),
        ),
    )
    await store.upsert_documents_async(
        config=skill_config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="skills",
                document_id="hooks",
                title="Hooks",
                body="runtime hook matching",
            ),
        ),
    )
    rebuild_stats = await store.rebuild_scope_async(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="skills",
    )
    stats = await store.stats_async(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="skills",
    )
    missing_hits = await store.search_async(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="missing",
            text="search",
        )
    )
    empty_query_hits = await store.search_async(
        query=RetrievalQuery(
            scope_kind=RetrievalScopeKind.SKILL,
            scope_id="skills",
            text="   ",
        )
    )
    empty_delete_stats = await store.delete_documents_async(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="skills",
        document_ids=(),
    )
    missing_delete_stats = await store.delete_documents_async(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="skills",
        document_ids=("missing",),
    )
    missing_rebuild_stats = await store.rebuild_scope_async(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="missing",
    )
    rotate_config = RetrievalScopeConfig(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="rotate",
    )
    rotate_trigram_config = RetrievalScopeConfig(
        scope_kind=RetrievalScopeKind.SKILL,
        scope_id="rotate",
        tokenizer=RetrievalTokenizer.TRIGRAM,
    )
    await store.replace_scope_async(
        config=rotate_config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="rotate",
                document_id="rotate-1",
                title="Rotate",
                body="unicode tokenizer",
            ),
        ),
    )
    await store.upsert_documents_async(
        config=rotate_trigram_config,
        documents=(
            RetrievalDocument(
                scope_kind=RetrievalScopeKind.SKILL,
                scope_id="rotate",
                document_id="rotate-1",
                title="Rotate",
                body="trigram tokenizer",
            ),
        ),
    )
    emptied_rotate_stats = await store.replace_scope_async(
        config=rotate_trigram_config,
        documents=(),
    )

    assert replace_stats.document_count == 2
    assert [hit.document_id for hit in hits] == ["router"]
    assert delete_stats.document_count == 1
    assert rebuild_stats.document_count == 2
    assert stats.document_count == 2
    assert missing_hits == ()
    assert empty_query_hits == ()
    assert empty_delete_stats.document_count == 2
    assert missing_delete_stats.document_count == 2
    assert missing_rebuild_stats.document_count == 0
    assert emptied_rotate_stats.document_count == 0


@pytest.mark.asyncio
async def test_feishu_message_pool_async_queue_paths(tmp_path: Path) -> None:
    repo = FeishuMessagePoolRepository(tmp_path / "feishu-message-pool.db")
    now = datetime.now(tz=timezone.utc)

    queued, created = await repo.create_or_get_async(
        _feishu_pool_record(
            "pool-queued",
            "message-queued",
            next_attempt_at=now - timedelta(seconds=1),
            run_id="run-queued",
        )
    )
    waiting, _ = await repo.create_or_get_async(
        _feishu_pool_record(
            "pool-waiting",
            "message-waiting",
            processing_status=FeishuMessageProcessingStatus.WAITING_RESULT,
            run_id="run-waiting",
        )
    )
    claimed, _ = await repo.create_or_get_async(
        _feishu_pool_record(
            "pool-claimed",
            "message-claimed",
            processing_status=FeishuMessageProcessingStatus.CLAIMED,
            last_claimed_at=now - timedelta(minutes=5),
        )
    )
    ack_pending, _ = await repo.create_or_get_async(
        _feishu_pool_record(
            "pool-ack",
            "message-ack",
            ack_text="Working on it",
        )
    )
    reaction_pending, _ = await repo.create_or_get_async(
        _feishu_pool_record(
            "pool-reaction",
            "message-reaction",
            reaction_type="eyes",
        )
    )
    duplicate, duplicate_created = await repo.create_or_get_async(
        _feishu_pool_record(
            "pool-duplicate",
            "message-queued",
        )
    )

    updated = await repo.update_async(
        queued.message_pool_id,
        processing_status=FeishuMessageProcessingStatus.WAITING_RESULT,
        process_attempts=1,
        session_id="session-1",
        run_id="run-queued",
    )
    loaded = await repo.get_async(queued.message_pool_id)
    by_message_key = await repo.get_by_message_key_async(
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        message_key="message-queued",
    )
    latest_by_run = await repo.get_latest_by_run_id_async("run-queued")
    ahead = await repo.count_active_chat_messages_ahead_async(
        reaction_pending.message_pool_id
    )
    ready = await repo.list_ready_for_processing_async(ready_at=now, limit=10)
    waiting_records = await repo.list_waiting_for_result_async(limit=10)
    acknowledgements = await repo.list_pending_acknowledgements_async(limit=10)
    reactions = await repo.list_pending_reactions_async(limit=10)
    active = await repo.list_active_chat_messages_async(
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        chat_id="chat-1",
    )
    counts = await repo.get_chat_status_counts_async(
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        chat_id="chat-1",
    )
    recovered = await repo.recover_stale_claims_async(
        claimed_before=now - timedelta(minutes=1)
    )
    cancelled = await repo.cancel_active_chat_messages_async(
        trigger_id="trigger-1",
        tenant_key="tenant-1",
        chat_id="chat-1",
        cancelled_at=now,
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.delivery_count == 2
    assert updated.processing_status == FeishuMessageProcessingStatus.WAITING_RESULT
    assert loaded is not None
    assert by_message_key.message_pool_id == queued.message_pool_id
    assert latest_by_run is not None
    assert latest_by_run.message_pool_id == queued.message_pool_id
    assert ahead >= 1
    assert tuple(record.message_pool_id for record in ready) == ()
    assert waiting.message_pool_id in {
        record.message_pool_id for record in waiting_records
    }
    assert ack_pending.message_pool_id in {
        record.message_pool_id for record in acknowledgements
    }
    assert reaction_pending.message_pool_id in {
        record.message_pool_id for record in reactions
    }
    assert {record.message_pool_id for record in active}
    assert counts[FeishuMessageProcessingStatus.WAITING_RESULT] >= 1
    assert recovered == 1
    assert cancelled >= 1
    assert claimed.message_pool_id == "pool-claimed"


@pytest.mark.asyncio
async def test_feishu_message_pool_async_missing_paths(tmp_path: Path) -> None:
    repo = FeishuMessagePoolRepository(tmp_path / "feishu-message-pool-edge.db")

    missing = await repo.get_async("missing")
    missing_latest = await repo.get_latest_by_run_id_async("missing-run")
    with pytest.raises(KeyError):
        await repo.get_by_message_key_async(
            trigger_id="trigger-1",
            tenant_key="tenant-1",
            message_key="missing-message",
        )
    with pytest.raises(KeyError):
        await repo.update_async(
            "missing",
            processing_status=FeishuMessageProcessingStatus.WAITING_RESULT,
        )

    assert missing is None
    assert missing_latest is None


@pytest.mark.asyncio
async def test_wechat_inbound_queue_async_paths(tmp_path: Path) -> None:
    repo = WeChatInboundQueueRepository(tmp_path / "wechat-inbound.db")
    now = datetime.now(tz=timezone.utc)
    queued, created = await repo.create_or_get_async(
        WeChatInboundQueueRecord(
            inbound_queue_id="inbound-1",
            account_id="wechat-1",
            message_key="message-1",
            gateway_session_id="gateway-1",
            session_id="session-1",
            peer_user_id="user-1",
            text="hello",
            created_at=now,
            updated_at=now,
        )
    )
    duplicate, duplicate_created = await repo.create_or_get_async(
        WeChatInboundQueueRecord(
            inbound_queue_id="inbound-duplicate",
            account_id="wechat-1",
            message_key="message-1",
            gateway_session_id="gateway-1",
            session_id="session-1",
            peer_user_id="user-1",
            text="hello again",
        )
    )
    second, _ = await repo.create_or_get_async(
        WeChatInboundQueueRecord(
            inbound_queue_id="inbound-2",
            account_id="wechat-1",
            message_key="message-2",
            gateway_session_id="gateway-1",
            session_id="session-1",
            peer_user_id="user-1",
            text="second",
        )
    )

    updated = await repo.update_async(
        queued.model_copy(
            update={
                "status": WeChatInboundQueueStatus.WAITING_RESULT,
                "run_id": "run-1",
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
    )
    loaded = await repo.get_async("inbound-1")
    by_key = await repo.get_by_message_key_async(
        account_id="wechat-1",
        peer_user_id="user-1",
        message_key="message-1",
    )
    latest_by_run = await repo.get_latest_by_run_id_async("run-1")
    has_non_terminal = await repo.has_non_terminal_item_for_run_async("run-1")
    count_by_session = await repo.count_non_terminal_by_session_async("session-1")
    ahead = await repo.count_non_terminal_ahead_async(second.inbound_queue_id)
    ready = await repo.list_ready_to_start_async(limit=10)
    claimed = await repo.claim_starting_async(
        inbound_queue_id=second.inbound_queue_id,
        stale_before=now - timedelta(seconds=1),
    )
    requeued = await repo.requeue_if_starting_async(
        inbound_queue_id=second.inbound_queue_id,
        last_error="retry",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.inbound_queue_id == queued.inbound_queue_id
    assert updated.status == WeChatInboundQueueStatus.WAITING_RESULT
    assert loaded is not None
    assert by_key is not None
    assert latest_by_run is not None
    assert has_non_terminal is True
    assert count_by_session >= 1
    assert ahead >= 1
    assert tuple(record.inbound_queue_id for record in ready) == ("inbound-2",)
    assert claimed is not None
    assert claimed.status == WeChatInboundQueueStatus.STARTING
    assert requeued is not None
    assert requeued.status == WeChatInboundQueueStatus.QUEUED


@pytest.mark.asyncio
async def test_wechat_inbound_queue_async_missing_paths(tmp_path: Path) -> None:
    repo = WeChatInboundQueueRepository(tmp_path / "wechat-inbound-edge.db")

    missing = await repo.get_async("missing")
    blank_latest = await repo.get_latest_by_run_id_async("   ")
    missing_latest = await repo.get_latest_by_run_id_async("missing-run")
    blank_has_non_terminal = await repo.has_non_terminal_item_for_run_async("   ")
    missing_claim = await repo.claim_starting_async(
        inbound_queue_id="missing",
        stale_before=datetime.now(tz=timezone.utc),
    )
    missing_requeue = await repo.requeue_if_starting_async(
        inbound_queue_id="missing",
        last_error="missing",
    )
    with pytest.raises(KeyError):
        await repo.get_by_message_key_async(
            account_id="wechat-1",
            peer_user_id="user-1",
            message_key="missing-message",
        )

    assert missing is None
    assert blank_latest is None
    assert missing_latest is None
    assert blank_has_non_terminal is False
    assert missing_claim is None
    assert missing_requeue is None


@pytest.mark.asyncio
async def test_monitor_repository_async_paths(tmp_path: Path) -> None:
    repo = MonitorRepository(tmp_path / "monitors.db")
    subscription = MonitorSubscriptionRecord(
        monitor_id="monitor-1",
        run_id="run-1",
        session_id="session-1",
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background-1",
        rule=MonitorRule(
            event_names=("background_task.line",),
            text_patterns_any=("ERROR",),
        ),
        action=MonitorAction(action_type=MonitorActionType.WAKE_INSTANCE),
        created_by_instance_id="inst-1",
        created_by_role_id="writer",
    )
    await repo.create_subscription_async(subscription)
    updated_subscription = await repo.update_subscription_async(
        subscription.model_copy(update={"trigger_count": 1})
    )
    loaded_subscription = await repo.get_subscription_async("monitor-1")
    by_run = await repo.list_for_run_async("run-1")
    active_for_source = await repo.list_active_for_source_async(
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background-1",
    )
    trigger = MonitorTriggerRecord(
        monitor_trigger_id="trigger-1",
        monitor_id="monitor-1",
        run_id="run-1",
        session_id="session-1",
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background-1",
        event_name="background_task.line",
        dedupe_key="line-1",
        body_text="ERROR down",
        action_type=MonitorActionType.WAKE_INSTANCE,
    )
    created_trigger = await repo.create_trigger_async(trigger)
    envelope = MonitorEventEnvelope(
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background-1",
        event_name="background_task.line",
        body_text="ERROR again",
        dedupe_key="line-2",
    )
    matched = await repo.record_matching_trigger_async(
        monitor_id=updated_subscription.monitor_id,
        envelope=envelope,
    )
    assert matched is not None
    duplicate_match = await repo.record_matching_trigger_async(
        monitor_id=updated_subscription.monitor_id,
        envelope=envelope,
    )
    matched_subscription, matched_trigger = matched
    has_dedupe = await repo.has_trigger_dedupe_key_async(
        monitor_id="monitor-1",
        dedupe_key="line-1",
    )
    triggers = await repo.list_triggers_for_monitor_async("monitor-1")
    await repo.delete_by_session_async("session-1")

    assert updated_subscription.trigger_count == 1
    assert loaded_subscription.monitor_id == "monitor-1"
    assert tuple(record.monitor_id for record in by_run) == ("monitor-1",)
    assert tuple(record.monitor_id for record in active_for_source) == ("monitor-1",)
    assert created_trigger.monitor_trigger_id == "trigger-1"
    assert matched_subscription.trigger_count == 2
    assert matched_trigger.dedupe_key == "line-2"
    assert duplicate_match is None
    assert has_dedupe is True
    assert {record.monitor_trigger_id for record in triggers} == {
        "trigger-1",
        matched_trigger.monitor_trigger_id,
    }


@pytest.mark.asyncio
async def test_monitor_repository_async_edge_paths(tmp_path: Path) -> None:
    repo = MonitorRepository(tmp_path / "monitors-edge.db")
    envelope = MonitorEventEnvelope(
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background-missing",
        event_name="background_task.line",
        body_text="ERROR",
        dedupe_key="line-missing",
    )

    with pytest.raises(KeyError):
        await repo.get_subscription_async("missing")
    with pytest.raises(KeyError):
        await repo.record_matching_trigger_async(
            monitor_id="missing",
            envelope=envelope,
        )
    await repo.delete_by_run_async("missing-run")


@pytest.mark.asyncio
async def test_token_usage_repository_async_paths(tmp_path: Path) -> None:
    marker_repo = SessionHistoryMarkerRepository(tmp_path / "token-markers.db")
    repo = TokenUsageRepository(
        tmp_path / "token-usage.db",
        session_history_marker_repo=marker_repo,
    )

    await repo.record_async(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        input_tokens=100,
        cached_input_tokens=25,
        output_tokens=40,
        reasoning_output_tokens=10,
        requests=2,
        tool_calls=1,
        context_window=2000,
        model_profile="gpt-test",
    )
    await repo.record_async(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        input_tokens=5,
        output_tokens=2,
        requests=1,
    )
    await marker_repo.create_clear_marker_async("session-1")
    await repo.record_async(
        session_id="session-1",
        run_id="run-2",
        instance_id="inst-2",
        role_id="reviewer",
        input_tokens=50,
        output_tokens=25,
        requests=1,
    )
    await repo.record_async(
        session_id="session-1",
        run_id="run-3",
        instance_id="inst-3",
        role_id="reviewer",
        input_tokens=10,
        output_tokens=5,
        requests=1,
    )
    run_usage = await repo.get_by_run_async("run-1")
    session_usage = await repo.get_by_session_async("session-1")
    await repo.delete_by_run_async("run-1")
    await repo.delete_by_session_async("session-1")

    assert run_usage.total_input_tokens == 105
    assert run_usage.total_cached_input_tokens == 25
    assert run_usage.total_output_tokens == 42
    assert run_usage.total_reasoning_output_tokens == 10
    assert run_usage.total_requests == 3
    assert run_usage.total_tool_calls == 1
    assert run_usage.by_agent[0].instance_id == "inst-1"
    assert session_usage.total_input_tokens == 60
    assert session_usage.total_output_tokens == 30
    assert tuple(session_usage.by_role) == ("reviewer",)


@pytest.mark.asyncio
async def test_trigger_repository_async_paths(tmp_path: Path) -> None:
    repo = TriggerRepository(tmp_path / "triggers.db")
    account = GitHubTriggerAccountRecord(
        account_id="account-1",
        name="main",
        display_name="Main",
        status=GitHubTriggerAccountStatus.ENABLED,
        token_configured=True,
        webhook_secret_configured=True,
    )
    await repo.create_account_async(account)
    with pytest.raises(GitHubTriggerAccountNameConflictError):
        await repo.create_account_async(
            account.model_copy(update={"account_id": "account-duplicate"})
        )
    account_updated = await repo.update_account_async(
        account.model_copy(update={"display_name": "Main GitHub"})
    )
    loaded_account = await repo.get_account_async("account-1")
    accounts = await repo.list_accounts_async()
    with pytest.raises(KeyError):
        await repo.get_account_async("missing-account")

    subscription = GitHubRepoSubscriptionRecord(
        repo_subscription_id="repo-1",
        account_id="account-1",
        owner="coolplayagent",
        repo_name="relay-teams",
        full_name="coolplayagent/relay-teams",
        external_repo_id="123",
        default_branch="main",
        callback_url="https://example.test/hooks",
        provider_webhook_id="hook-1",
        subscribed_events=("pull_request",),
        webhook_status=GitHubWebhookStatus.REGISTERED,
    )
    await repo.create_repo_subscription_async(subscription)
    with pytest.raises(GitHubRepoSubscriptionConflictError):
        await repo.create_repo_subscription_async(
            subscription.model_copy(update={"repo_subscription_id": "repo-duplicate"})
        )
    subscription_updated = await repo.update_repo_subscription_async(
        subscription.model_copy(update={"default_branch": "develop"})
    )
    loaded_subscription = await repo.get_repo_subscription_async("repo-1")
    subscriptions = await repo.list_repo_subscriptions_async()
    subscriptions_by_name = await repo.list_repo_subscriptions_by_full_name_async(
        "coolplayagent/relay-teams",
        enabled_only=True,
    )
    subscriptions_by_account = await repo.list_repo_subscriptions_by_account_async(
        "account-1"
    )
    with pytest.raises(KeyError):
        await repo.get_repo_subscription_async("missing-repo")

    dispatch_config = TriggerDispatchConfig(
        target_type=TriggerTargetType.RUN_TEMPLATE,
        run_template=GitHubTriggerRunTemplate(
            workspace_id="default",
            prompt_template="Review {{pull_request.number}}",
        ),
        action_hooks=(
            GitHubActionSpec(
                action_type=GitHubActionType.COMMENT,
                body_template="Done",
            ),
        ),
    )
    rule = TriggerRuleRecord(
        trigger_rule_id="rule-1",
        account_id="account-1",
        repo_subscription_id="repo-1",
        name="PR review",
        match_config=TriggerRuleMatchConfig(
            event_name="pull_request",
            actions=("opened",),
            base_branches=("main",),
        ),
        dispatch_config=dispatch_config,
    )
    await repo.create_rule_async(rule)
    with pytest.raises(TriggerRuleNameConflictError):
        await repo.create_rule_async(
            rule.model_copy(update={"trigger_rule_id": "rule-duplicate"})
        )
    rule_updated = await repo.update_rule_async(rule.model_copy(update={"version": 2}))
    loaded_rule = await repo.get_rule_async("rule-1")
    rules = await repo.list_rules_async()
    enabled_rules = await repo.list_enabled_rules_for_repo_async("repo-1")
    with pytest.raises(KeyError):
        await repo.get_rule_async("missing-rule")

    delivery = TriggerDeliveryRecord(
        trigger_delivery_id="delivery-1",
        provider=TriggerProvider.GITHUB,
        provider_delivery_id="provider-delivery-1",
        account_id="account-1",
        repo_subscription_id="repo-1",
        event_name="pull_request",
        event_action="opened",
        signature_status=TriggerDeliverySignatureStatus.VALID,
        ingest_status=TriggerDeliveryIngestStatus.TRIGGERED,
        headers={"x-github-event": "pull_request"},
        payload={"number": 649},
        normalized_payload={"number": 649},
    )
    await repo.create_delivery_async(delivery)
    with pytest.raises(TriggerDeliveryConflictError):
        await repo.create_delivery_async(
            delivery.model_copy(update={"trigger_delivery_id": "delivery-duplicate"})
        )
    delivery_updated = await repo.update_delivery_async(
        delivery.model_copy(update={"last_error": "none"})
    )
    loaded_delivery = await repo.get_delivery_async("delivery-1")
    delivery_by_provider = await repo.get_delivery_by_provider_id_async(
        provider=TriggerProvider.GITHUB.value,
        provider_delivery_id="provider-delivery-1",
    )
    missing_delivery_by_provider = await repo.get_delivery_by_provider_id_async(
        provider=TriggerProvider.GITHUB.value,
        provider_delivery_id="missing-delivery",
    )
    deliveries = await repo.list_deliveries_async()
    with pytest.raises(KeyError):
        await repo.get_delivery_async("missing-delivery")

    evaluation = TriggerEvaluationRecord(
        trigger_evaluation_id="evaluation-1",
        trigger_delivery_id="delivery-1",
        trigger_rule_id="rule-1",
        matched=True,
        reason_code="matched",
    )
    await repo.create_evaluation_async(evaluation)
    evaluations = await repo.list_evaluations_by_delivery_async("delivery-1")

    dispatch = TriggerDispatchRecord(
        trigger_dispatch_id="dispatch-1",
        trigger_delivery_id="delivery-1",
        trigger_rule_id="rule-1",
        target_type=TriggerTargetType.RUN_TEMPLATE,
        status=TriggerDispatchStatus.PENDING,
        session_id="session-1",
        run_id="run-1",
    )
    await repo.create_dispatch_async(dispatch)
    dispatch_updated = await repo.update_dispatch_async(
        dispatch.model_copy(update={"status": TriggerDispatchStatus.RUNNING})
    )
    loaded_dispatch = await repo.get_dispatch_async("dispatch-1")
    dispatches = await repo.list_dispatches_async()
    dispatches_by_delivery = await repo.list_dispatches_by_delivery_async("delivery-1")
    open_dispatches = await repo.list_open_dispatches_async()
    with pytest.raises(KeyError):
        await repo.get_dispatch_async("missing-dispatch")

    action = GitHubActionSpec(
        action_type=GitHubActionType.COMMENT,
        body_template="Done",
    )
    attempt = TriggerActionAttemptRecord(
        trigger_action_attempt_id="attempt-1",
        trigger_dispatch_id="dispatch-1",
        phase=TriggerActionPhase.IMMEDIATE,
        action_type=GitHubActionType.COMMENT,
        status=TriggerActionStatus.PENDING,
        action_spec=action,
        request_payload={"body": "Done"},
    )
    await repo.create_action_attempt_async(attempt)
    attempt_updated = await repo.update_action_attempt_async(
        attempt.model_copy(
            update={
                "status": TriggerActionStatus.SENDING,
                "attempt_count": 1,
            }
        )
    )
    attempts = await repo.list_action_attempts_async()
    attempts_by_dispatch = await repo.list_action_attempts_by_dispatch_async(
        "dispatch-1"
    )
    pending_attempts = await repo.list_pending_action_attempts_async()
    await repo.delete_rule_async("rule-1")
    await repo.delete_repo_subscription_async("repo-1")
    await repo.delete_account_async("account-1")

    assert account_updated.display_name == "Main GitHub"
    assert loaded_account.account_id == "account-1"
    assert tuple(record.account_id for record in accounts) == ("account-1",)
    assert subscription_updated.default_branch == "develop"
    assert loaded_subscription.repo_subscription_id == "repo-1"
    assert tuple(record.repo_subscription_id for record in subscriptions) == ("repo-1",)
    assert tuple(record.repo_subscription_id for record in subscriptions_by_name) == (
        "repo-1",
    )
    assert tuple(
        record.repo_subscription_id for record in subscriptions_by_account
    ) == ("repo-1",)
    assert rule_updated.version == 2
    assert loaded_rule.trigger_rule_id == "rule-1"
    assert tuple(record.trigger_rule_id for record in rules) == ("rule-1",)
    assert tuple(record.trigger_rule_id for record in enabled_rules) == ("rule-1",)
    assert delivery_updated.last_error == "none"
    assert loaded_delivery.trigger_delivery_id == "delivery-1"
    assert delivery_by_provider is not None
    assert missing_delivery_by_provider is None
    assert tuple(record.trigger_delivery_id for record in deliveries) == ("delivery-1",)
    assert tuple(record.trigger_evaluation_id for record in evaluations) == (
        "evaluation-1",
    )
    assert dispatch_updated.status == TriggerDispatchStatus.RUNNING
    assert loaded_dispatch.trigger_dispatch_id == "dispatch-1"
    assert tuple(record.trigger_dispatch_id for record in dispatches) == ("dispatch-1",)
    assert tuple(record.trigger_dispatch_id for record in dispatches_by_delivery) == (
        "dispatch-1",
    )
    assert tuple(record.trigger_dispatch_id for record in open_dispatches) == (
        "dispatch-1",
    )
    assert attempt_updated.status == TriggerActionStatus.SENDING
    assert tuple(record.trigger_action_attempt_id for record in attempts) == (
        "attempt-1",
    )
    assert tuple(
        record.trigger_action_attempt_id for record in attempts_by_dispatch
    ) == ("attempt-1",)
    assert tuple(record.trigger_action_attempt_id for record in pending_attempts) == (
        "attempt-1",
    )


def _feishu_pool_record(
    message_pool_id: str,
    message_key: str,
    *,
    processing_status: FeishuMessageProcessingStatus = (
        FeishuMessageProcessingStatus.QUEUED
    ),
    next_attempt_at: datetime | None = None,
    run_id: str | None = None,
    ack_text: str | None = None,
    reaction_type: str | None = None,
    last_claimed_at: datetime | None = None,
) -> FeishuMessagePoolRecord:
    now = datetime.now(tz=timezone.utc)
    return FeishuMessagePoolRecord(
        message_pool_id=message_pool_id,
        trigger_id="trigger-1",
        trigger_name="Feishu Main",
        tenant_key="tenant-1",
        chat_id="chat-1",
        chat_type="group",
        event_id=f"event-{message_key}",
        message_key=message_key,
        message_id=f"om_{message_key}",
        intent_text=f"process {message_key}",
        payload={"text": message_key},
        metadata={"source": "test"},
        processing_status=processing_status,
        reaction_status=FeishuMessageDeliveryStatus.PENDING,
        reaction_type=reaction_type,
        ack_status=FeishuMessageDeliveryStatus.PENDING,
        ack_text=ack_text,
        final_reply_status=FeishuMessageDeliveryStatus.PENDING,
        session_id=None if run_id is None else "session-1",
        run_id=run_id,
        next_attempt_at=next_attempt_at or now,
        last_claimed_at=last_claimed_at,
        created_at=now,
        updated_at=now,
    )

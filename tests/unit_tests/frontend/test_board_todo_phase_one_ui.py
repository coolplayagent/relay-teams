from __future__ import annotations

from pathlib import Path


def test_board_todo_start_flow_previews_prompt_before_start() -> None:
    handoff = Path("frontend/dist/js/components/boards/todoHandoff.js").read_text(
        encoding="utf-8"
    )
    board = Path("frontend/dist/js/components/boards/todoBoard.js").read_text(
        encoding="utf-8"
    )
    styles = Path("frontend/dist/css/components/board-todos.css").read_text(
        encoding="utf-8"
    )

    assert "previewStartBoardTodo" in handoff
    assert "startBoardTodo(todoId, values)" in handoff
    assert "final_prompt" in handoff
    assert "fetchRoleConfigOptions" in handoff
    assert "fetchOrchestrationConfig" in handoff
    assert (
        "board-todo-start-composer input-container is-new-session-draft-composer"
        in handoff
    )
    assert "input-wrapper" in handoff
    assert "composer-actions" in handoff
    assert "input-controls" in handoff
    assert "composer-segmented-btn" in handoff
    assert "composer-mode-toggle" in handoff
    assert "new-session-draft-mention-hint" in handoff
    assert "board-todo-start-prompt-field" not in handoff
    assert "new-session-workspace-bar" not in handoff
    assert "data-draft-workspace" not in handoff
    assert "board-todo-start-voice-btn" not in handoff
    assert "renderVoiceIcon" not in handoff
    assert "data-board-todo-normal-role" in handoff
    assert "state.sessionMode = 'normal';" in handoff
    assert "data-board-todo-orchestration-preset" in handoff
    assert "state.sessionMode = 'orchestration';" in handoff
    assert "data-board-todo-start-prompt" in handoff
    assert 'data-board-todo-start-action="submit"' in handoff
    assert "session_mode" in handoff
    assert "view_workspace_id" in handoff
    assert "viewWorkspaceId" in handoff
    normalize_start = handoff[
        handoff.index("function normalizeStartPayload") : handoff.index(
            "function normalizeRoleOptions"
        )
    ]
    assert "preview?.view_workspace_id" not in normalize_start
    assert "explicitNormalRoleId !== defaultNormalRoleId" in normalize_start
    assert "normal_root_role_id" in handoff
    assert "orchestration_preset_id" in handoff
    assert "thinking" in handoff
    assert ".board-todo-start-composer .input-wrapper" in styles
    assert ".board-todo-start-composer .composer-actions" in styles
    assert ".board-todo-start-composer .input-controls" in styles
    assert ".board-todo-start-prompt-field" not in styles
    assert ".board-todo-start-runtime" not in styles
    assert "padding: 22px 420px 64px 22px" not in styles
    assert "padding: 22px 22px 76px" in styles
    assert handoff.index("previewStartBoardTodo") < handoff.index(
        "startBoardTodo(todoId, values)"
    )
    assert "reviewAndStartBoardTodo" in board
    assert "startBoardTodo" not in board


def test_board_todo_source_settings_are_split_from_board_renderer() -> None:
    settings = Path(
        "frontend/dist/js/components/boards/todoSourceSettings.js"
    ).read_text(encoding="utf-8")
    board = Path("frontend/dist/js/components/boards/todoBoard.js").read_text(
        encoding="utf-8"
    )
    api = Path("frontend/dist/js/core/api/boardTodos.js").read_text(encoding="utf-8")

    assert "fetchBoardTodoSources" in settings
    assert "createBoardTodoSource" in settings
    assert "updateBoardTodoSource" in settings
    assert "deleteBoardTodoSource" in settings
    assert "board-todo-source-record" in settings
    assert "selectedSource = githubSources[0]" not in settings
    assert "renderDisplayModeSettings" in settings
    assert 'data-board-todo-source-action="view-mode"' in settings
    assert "board-todo-settings-view-toggle" in settings
    assert "board-todo-settings-view-row" in settings
    assert "board-todo-source-inline-action" in settings
    assert "showSourceMutationError" in settings
    assert "state.error = message" in settings
    assert "profile-card-action-btn" not in settings
    assert "openBoardTodoSourceSettings" in board
    assert 'data-board-todo-action="sources"' in board
    assert "board-todos-toolbar-icon-btn" in board
    assert "M8.9 2.8h2.2" in board
    assert (
        'board-todos-tool-btn" type="button" data-board-todo-action="sources"'
        not in board
    )
    assert 'data-board-todo-action="create"' not in board
    assert "renderDisplayModeToggle" not in board
    assert "renderSourceSettingsColumnButton" not in board
    assert "!groups.length && filteredItems.length" in board
    assert "visibleItems.map(renderCard).join('')" in board
    assert "createBoardTodo(" not in api
    assert "/api/boards/todo-sources" in api
    assert ":preview-start" in api


def test_board_todo_in_process_cards_render_runtime_badges() -> None:
    board = Path("frontend/dist/js/components/boards/todoBoard.js").read_text(
        encoding="utf-8"
    )
    styles = Path("frontend/dist/css/components/board-todos.css").read_text(
        encoding="utf-8"
    )

    assert "renderRuntimeBadge" in board
    assert "run_recoverable" in board
    assert "board_todos.run.${badgeKey}" in board
    assert "board-todos-run-badge" in styles


def test_board_todo_review_cards_can_be_marked_done() -> None:
    board = Path("frontend/dist/js/components/boards/todoBoard.js").read_text(
        encoding="utf-8"
    )
    api = Path("frontend/dist/js/core/api/boardTodos.js").read_text(encoding="utf-8")
    i18n = Path("frontend/dist/js/utils/i18n.js").read_text(encoding="utf-8")

    review_start = board.index("if (status === 'review')")
    review_end = board.index("if (status !== 'archived')", review_start)
    review_actions = board[review_start:review_end]

    assert "markBoardTodoDone" in board
    assert 'data-board-todo-action="mark-done"' in review_actions
    assert review_actions.index("board_todos.action.mark_done") < review_actions.index(
        "board_todos.action.request_changes"
    )
    assert "async function handleMarkDone" in board
    assert "board_todos.toast.marked_done" in board
    assert ":mark-done" in api
    assert "'board_todos.action.mark_done': 'Done'" in i18n
    assert "'board_todos.action.mark_done': '完成'" in i18n


def test_board_todo_grouped_and_mixed_views_are_available() -> None:
    board = Path("frontend/dist/js/components/boards/todoBoard.js").read_text(
        encoding="utf-8"
    )
    settings = Path(
        "frontend/dist/js/components/boards/todoSourceSettings.js"
    ).read_text(encoding="utf-8")
    styles = Path("frontend/dist/css/components/board-todos.css").read_text(
        encoding="utf-8"
    )

    assert "source_groups" in board
    assert "DISPLAY_MODES" in board
    assert "renderGroupedColumn" in board
    assert "renderSourceGroup" in board
    assert "source:${provider}:${sourceKey}" in board
    assert "renderBoard(getRoot())" in board
    assert "total === 0 || isSourceGroupCollapsed" in board
    assert "BoardTodoSourceKind.MANUAL" not in board
    assert "board_todos.source.manual" not in board
    assert "Manual" not in board
    assert "Manual" not in settings
    assert "chevron-down" in board
    assert "is-collapsed" in board
    assert "aria-hidden=\"${collapsed ? 'true' : 'false'}\"" in board
    assert "\u203a" not in board
    assert "\u2304" not in board
    assert "board-todos-view-toggle" in styles
    assert ".board-todo-settings-view-row" in styles
    assert ".board-todo-source-inline-action" in styles
    assert "board-todos-source-group" in styles
    assert "board-todos-source-group.is-collapsed" in styles
    assert "grid-template-rows 180ms ease" in styles
    assert "transform: rotate(-90deg)" in styles


def test_board_todo_toolbar_uses_refresh_and_rightmost_settings() -> None:
    board = Path("frontend/dist/js/components/boards/todoBoard.js").read_text(
        encoding="utf-8"
    )
    i18n = Path("frontend/dist/js/utils/i18n.js").read_text(encoding="utf-8")
    toolbar_start = board.index('<div class="board-todos-actions">')
    toolbar_end = board.index("</div>", toolbar_start)
    toolbar = board[toolbar_start:toolbar_end]

    assert "renderSyncButton()" in toolbar
    assert "renderSettingsToolbarButton()" in toolbar
    assert toolbar.index("renderSyncButton()") < toolbar.index(
        "renderSettingsToolbarButton()"
    )
    assert "board-todos-toggle" in toolbar
    assert "data-board-todo-archived" in toolbar
    assert 'data-board-todo-action="sources"' not in board[:toolbar_start]
    assert "board-todos-toolbar-icon-btn board-todos-settings-btn" in board
    assert "'board_todos.action.sync': 'Refresh'" in i18n
    assert "'board_todos.action.sync': '刷新'" in i18n
    assert "Sync GitHub" not in i18n
    assert "同步 GitHub" not in i18n
    assert "'board_todos.action.new'" not in i18n


def test_board_todo_search_refresh_does_not_replace_column_shell() -> None:
    board = Path("frontend/dist/js/components/boards/todoBoard.js").read_text(
        encoding="utf-8"
    )
    refresh_start = board.index("function refreshBoardTodoColumn")
    refresh_end = board.index("function computeStatusCounts")
    refresh_body = board[refresh_start:refresh_end]

    assert "outerHTML" not in refresh_body
    assert "renderColumn(safeStatus" not in refresh_body
    assert "renderGroupedColumn" in refresh_body
    assert "refreshVisibleBoardTodoLists" not in board
    assert "listElement.innerHTML" in refresh_body


def test_board_todo_initial_render_does_not_batch_cards() -> None:
    board = Path("frontend/dist/js/components/boards/todoBoard.js").read_text(
        encoding="utf-8"
    )
    styles = Path("frontend/dist/css/components/board-todos.css").read_text(
        encoding="utf-8"
    )
    progressive_start = board.index("function startProgressiveRender")
    progressive_end = board.index("function cancelProgressiveRender")
    progressive_body = board[progressive_start:progressive_end]

    assert "renderBoard(root)" in progressive_body
    assert "window.setTimeout" not in progressive_body
    assert "stagedCounts.set" not in progressive_body
    assert "scroll-padding-bottom: 14px" in styles
    assert "max-height: 2400px" not in styles
    assert "grid-template-rows: 1fr" in styles
    assert "board-todos-source-group-list-inner" in board

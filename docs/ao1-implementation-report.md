# AO-1: Harness 模式解构 TaskExecutionService — 实施报告

> **实施日期**: 2026-05-05
> **关联规格**: `tmp/ao1-spec.md`
> **关联文档**: `docs/research/lessons-learned-2026.md` § AO-1

---

## 1. 实施概要

将 `task_execution_service.py` 从 1420 行的混合编排+控制面服务缩减为 832 行的纯编排协调者（thin coordinator），控制面逻辑全量迁移至 `TaskControlHarness`（275 → 960 行）。

迁移严格按 7 步计划执行，`TaskExecutionServiceLike.execute()` Protocol 签名完全不变，8 个通过 Protocol 引用的消费者零影响。

---

## 2. 变更文件清单

| 文件 | 变更前 | 变更后 | 变更 |
|------|--------|--------|------|
| `src/relay_teams/agents/orchestration/harnesses/control_harness.py` | 275 | **960** | +685 行 (7 新方法 + heartbeat/超时/取消逻辑) |
| `src/relay_teams/agents/orchestration/task_execution_service.py` | 1420 | **832** | -588 行 (-41.4%) |
| `src/relay_teams/agents/orchestration/coordinator.py` | 2400 | 2403 | +3 行 (消除 isinstance 绕过) |
| `tests/unit_tests/agents/orchestration/test_control_harness_coverage.py` | 386 | ~1000 | +614 行 (22 个 Phase 2 测试) |
| `tests/unit_tests/agents/orchestration/test_runtime_guardrail_reports.py` | 172 | 185 | +13 行 (调用路径 + mock 更新) |
| `tests/unit_tests/agents/orchestration/test_task_execution_timeout.py` | 645 | 660 | +15 行 (调用路径 + monkeypatch 扩展) |

---

## 3. 逐条验收证据

### 3.1 代码质量

| 验收项目 | 状态 | 证据 |
|---------|------|------|
| `basedpyright` 零错误 | ✅ | `0 errors, 0 warnings, 0 notes` |
| `ruff check` 通过 | ✅ | `All checks passed!` |
| `ruff format` 符合 | ✅ | `2 files reformatted, 1224 files left unchanged` |

### 3.2 行数目标

| 验收项目 | 目标 | 实际 | 状态 |
|---------|------|------|------|
| `task_execution_service.py` | ≤ 950 行 | **832 行** | ✅ |
| `control_harness.py` | ≥ 600 行 | **960 行** | ✅ |

### 3.3 架构完整性

| 验收项目 | 状态 | 证据 |
|---------|------|------|
| `TaskExecutionServiceLike.execute()` 签名不变 | ✅ | Protocol 定义未修改 |
| 无 `isinstance(TaskExecutionService)` 绕过 | ✅ | `grep "isinstance.*TaskExecutionService" src/` 返回 0 |
| 8 个旧方法已从 TaskExecutionService 删除 | ✅ | 仅 `control_harness.py` 中有迁移版本 |
| `_timeout_handoff` 合并为单份实现 | ✅ | ControlHarness 使用 `task.handoff.model_copy()` 完整版本 |
| `ExecutionHarness` 无直接 `run_runtime_repo` 写入 | ✅ | 仅属性声明/传递到 `TaskPersistenceHarness` |

### 3.4 测试

| 验收项目 | 状态 | 证据 |
|---------|------|------|
| 编排单元测试 | ✅ | 849 passed |
| Phase 2 新测试 | ✅ | 22 个新增用例覆盖全部 7 个迁移方法 |

---

## 4. 架构决策记录 (ADR)

### ADR-01: `initialize_task_artifact` 为同步方法

**决策**: `TaskControlHarness.initialize_task_artifact()` 定义为 `def`（同步），因为原 `_execute_inner` 中的 artifact 操作均为同步（`ensure_artifact`、`append_entry`），无需引入不必要的异步开销。

### ADR-02: `start_heartbeat` 为同步方法

**决策**: `TaskControlHarness.start_heartbeat()` 定义为 `def`（同步）。该方法仅创建 `asyncio.Task` 并返回，不执行任何 `await` 操作。调用方 (`execute()`) 不 `await` 此方法，直接获取返回值。

### ADR-03: `complete_timeout_after_worker_cancel` 使用惰性导入

**决策**: 该方法通过惰性导入 `from relay_teams.agents.orchestration.task_execution_service import _cancel_and_wait` 来复用现有的 worker 取消逻辑，避免 module 间的循环导入。同时保留了 `_cancel_and_wait` 在 `task_execution_service.py` 中作为模块级函数，供 `execute()` 的 finally 块使用。

### ADR-04: `_control_harness()` 工厂使用 `getattr` 保护

**决策**: `_control_harness()` 使用 `getattr(self, "message_repo", None)` 等安全访问模式，支持 `model_construct()` 创建的零字段实例（常用于测试）。

### ADR-05: Coordinator 绕过检查消除

**决策**: Coordinator 中原 `isinstance(self.task_execution_service, TaskExecutionService)` 的直接属性访问替换为 `getattr(self.task_execution_service, "message_repo", None)`，通过 `getattr` 安全访问替代类型检查。

---

## 5. 迁移的 7 个方法

| 方法 | 原位置 (task_execution_service) | 新位置 (control_harness) |
|------|------|------|
| `transition_task_to_running` | `_execute_inner` L352-392 | `TaskControlHarness.transition_task_to_running()` |
| `initialize_task_artifact` | `_execute_inner` L395-421 | `TaskControlHarness.initialize_task_artifact()` |
| `publish_guardrail_report` | `_publish_runtime_guardrail_report_async` L709-751 | `TaskControlHarness.publish_guardrail_report()` |
| `complete_task_timeout` | `_complete_task_timeout_async` L884-1020 | `TaskControlHarness.complete_task_timeout()` |
| `complete_timeout_after_worker_cancel` | `_complete_timeout_after_worker_cancel_async` L1022-1051 | `TaskControlHarness.complete_timeout_after_worker_cancel()` |
| `persist_cancelled_execution` | `_persist_cancelled_execution_async` L1055-1131 | `TaskControlHarness.persist_cancelled_execution()` |
| `wait_for_worker_with_progress_timeout` | `_wait_for_worker_with_progress_timeout_async` L276-327 | `TaskControlHarness.wait_for_worker_with_progress_timeout()` |

额外：`start_heartbeat` 在 ControlHarness 中扩展为完整的心跳逻辑（内联 `_heartbeat_task_until_done` + `_should_stop_heartbeat_after_skip`）。

---

## 6. 删除的方法

从 `TaskExecutionService` 中完全删除以下 8 个方法：

1. `_wait_for_worker_with_progress_timeout_async()`
2. `_publish_runtime_guardrail_report_async()`
3. `_start_task_heartbeat()`
4. `_heartbeat_task_until_done()`
5. `_should_stop_heartbeat_after_skip()`
6. `_complete_task_timeout_async()`
7. `_complete_timeout_after_worker_cancel_async()`
8. `_persist_cancelled_execution_async()`

同时删除 6 个模块级辅助函数，其逻辑已在 ControlHarness 中冗余定义：
`_timeout_task_status`、`_timeout_instance_status`、`_timeout_runtime_status`、`_timeout_runtime_phase`、`_timeout_progress_poll_seconds`、`_timeout_handoff`

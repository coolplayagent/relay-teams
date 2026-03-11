# Role State-Space and Recursive Communication Design

## 1. Purpose

This document describes how the current `roles`, `agents`, and `coordination` modules implement role-scoped communication based on state-space boundaries and recursive feedback loops.

澶?Agent 鍗忎綔鐨勬湰璐紝涓嶆槸娑堟伅浼犻€掞紝鑰屾槸瑙掕壊浣滅敤鍩熺姸鎬佺┖闂翠箣闂达紝鍦ㄧ害鏉熶笅杩涜鐨勯€掑綊鐘舵€佸彉鎹笌鍙嶉鎺у埗銆?

The design follows these principles:

1. Role is prior to instance: role defines state-space boundaries, instance only executes transitions.
2. Memory is layered and scoped by role context rather than instance identity.
3. Communication is modeled as encoded state transitions.
4. Collaboration must define convergence conditions with explicit acceptance and verification signals.
5. Role-scoped workspace and conversation binding provides isolation and traceability.

## 2. Layered Memory Scope Strategy

To avoid over-constraining all memory into one key shape, memory scopes are split into three layers:

- `RoleWorkspaceMemoryScope(workspace_id, role_id)` for durable role-level memory.
- `RoleConversationMemoryScope(workspace_id, role_id, conversation_id)` for conversation-thread continuity.
- `RoleTaskMemoryScope(workspace_id, role_id, conversation_id, task_id)` for short-lived task scratchpad state.

This aligns with the workspace-memory layering documented in `docs/role-workspace-memory-design.md` and supports both reuse and isolation.

## 3. Module Responsibilities

### 3.1 `roles` module

- `RoleDefinition` is the stable contract of role identity and role-level capabilities.
- `role_id` is used as the authoritative identity for role-scoped state-space and memory ownership.

Reference:
- `src/agent_teams/roles/models.py`

### 3.2 `agents` module

- `AgentRuntimeRecord` and `SubAgentInstance` are execution carriers.
- They hold runtime bindings (`instance_id`, `workspace_id`, `conversation_id`) and execute state transitions under role boundaries.

Reference:
- `src/agent_teams/agents/models.py`

### 3.3 `coordination` module

`src/agent_teams/coordination/role_communication.py` provides concrete coordination capabilities:

- `RoleStateSpace` and `RoleStateTransition`: role-defined state-space boundary.
- `RoleInstanceExecution` and `execute_role_transition(...)`: instance execution inside role boundary.
- `RoleAgentBinding` and `bind_role_to_agent_instance(...)`: connect `RoleDefinition` with runtime agent records.
- `build_role_workspace_memory_scope_from_binding(...)`: build role-level durable memory scope.
- `build_memory_scope_from_binding(...)`: build conversation-level thread memory scope.
- `build_task_memory_scope_from_binding(...)`: build task-level scratchpad scope.
- `RoleCommunicationExchange`, `validate_role_communication(...)`, and `validate_exchange_binding(...)`: communication as transition payload and boundary validation.
- `FeedbackLoopSpec`, `FeedbackLoopEvaluation`, `evaluate_feedback_loop(...)`, and `evaluate_feedback_loop_recursively(...)`: convergence evaluation for iterative collaboration.

## 4. Runtime Flow

### Step 1: Role and instance binding

Use `bind_role_to_agent_instance(role_definition, agent_instance)` to build a strict runtime binding.

Contract:
- `agent_instance.role_id` must equal `role_definition.role_id`.

Output:
- `RoleAgentBinding(role_id, instance_id, workspace_id, conversation_id)`.

### Step 2: Build memory scope by memory type

- Role durable memory: `build_role_workspace_memory_scope_from_binding(binding)`.
- Conversation continuity: `build_memory_scope_from_binding(binding)`.
- Task scratchpad: `build_task_memory_scope_from_binding(binding, task_id)`.

### Step 3: Execute role transition

Use `execute_role_transition(role_state_space, execution)` before applying a transition.

Checks:
- instance role must match role state-space role.
- transition must be inside allowed role transitions.
- no-op transitions (`from_state == to_state`) are only accepted when the state exists inside the declared role state-space.

### Step 4: Validate communication exchange

When one role sends communication to another, represent the message as `RoleCommunicationExchange` with conversation scope.

Checks:
- exchange memory role must match receiver role.
- transition must be legal in receiver role state-space.
- receiver binding can be validated with `validate_exchange_binding(...)`.

### Step 5: Recursive feedback convergence

Use `evaluate_feedback_loop(...)` for one iteration and `evaluate_feedback_loop_recursively(...)` for iterative runs.

Convergence condition:
- all acceptance criteria and verification points are observed.

Stop condition:
- convergence reached, or
- iteration reaches `max_iterations`.

## 5. Engineering Constraints

- Keep role boundary checks in coordination layer, not in ad-hoc prompt text.
- Keep imports at module top-level for dependency visibility.
- Keep public coordination interfaces exported from `agent_teams.coordination.__init__`.
- Add unit tests that mirror coordination path in `tests/unit_tests/coordination/`.

## 6. Related Source Files

- `src/agent_teams/coordination/role_communication.py`
- `src/agent_teams/coordination/__init__.py`
- `src/agent_teams/roles/models.py`
- `src/agent_teams/agents/models.py`
- `tests/unit_tests/coordination/test_role_communication.py`
- `docs/role-workspace-memory-design.md`

## 7. Workflow Boundary Supplement

Additional boundary rules:
- Role is the HR layer: it defines stable identity, responsibility boundary, and executable capability.
- Workflow is the institutional layer: it defines fixed stage order, task decomposition, and dependency topology.
- Coordination is the decision layer: it interprets intent, creates delegated tasks, and decides dispatch order at runtime.
- The same set of roles may be reused by different industries with different task breakdowns, so fixed flow must not be encoded in role markdown.
- If a dependency hint such as `depends_on` appears in role metadata, treat it as a design error. Ordering belongs to runtime task orchestration, not role files.



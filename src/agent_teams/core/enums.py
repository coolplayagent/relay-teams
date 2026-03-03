from enum import Enum


class ExecutionMode(str, Enum):
    AI    = 'ai'      # default: Coordinator LLM drives dispatch
    HUMAN = 'human'   # human manually selects which sub-task to run
    AUTO  = 'auto'    # run all sub-tasks to completion without returning to Coordinator


class TaskStatus(str, Enum):
    CREATED = 'created'
    ASSIGNED = 'assigned'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    TIMEOUT = 'timeout'


class InstanceStatus(str, Enum):
    IDLE = 'idle'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    TIMEOUT = 'timeout'


class ScopeType(str, Enum):
    GLOBAL = 'global'
    SESSION = 'session'
    TASK = 'task'
    INSTANCE = 'instance'


class EventType(str, Enum):
    TASK_CREATED = 'task_created'
    TASK_ASSIGNED = 'task_assigned'
    TASK_STARTED = 'task_started'
    TASK_COMPLETED = 'task_completed'
    TASK_FAILED = 'task_failed'
    TASK_TIMEOUT = 'task_timeout'
    INSTANCE_CREATED = 'instance_created'
    INSTANCE_RECYCLED = 'instance_recycled'
    VERIFICATION_PASSED = 'verification_passed'
    VERIFICATION_FAILED = 'verification_failed'


class InjectionSource(str, Enum):
    SYSTEM = 'system'
    USER = 'user'
    SUBAGENT = 'subagent'


class RunEventType(str, Enum):
    RUN_STARTED = 'run_started'
    MODEL_STEP_STARTED = 'model_step_started'
    MODEL_STEP_FINISHED = 'model_step_finished'
    TEXT_DELTA = 'text_delta'
    TOOL_CALL = 'tool_call'
    TOOL_INPUT_VALIDATION_FAILED = 'tool_input_validation_failed'
    TOOL_RESULT = 'tool_result'
    INJECTION_ENQUEUED = 'injection_enqueued'
    INJECTION_APPLIED = 'injection_applied'
    TOOL_APPROVAL_REQUESTED = 'tool_approval_requested'
    TOOL_APPROVAL_RESOLVED = 'tool_approval_resolved'
    RUN_COMPLETED = 'run_completed'
    RUN_FAILED = 'run_failed'
    # Human orchestration mode
    AWAITING_HUMAN_DISPATCH = 'awaiting_human_dispatch'  # human mode: waiting for user to pick next task
    HUMAN_TASK_DISPATCHED   = 'human_task_dispatched'    # human mode: user dispatched a task
    # Confirmation gate
    SUBAGENT_GATE   = 'subagent_gate'    # gate open: waiting for user to approve or request revision
    GATE_RESOLVED   = 'gate_resolved'    # gate closed: user chose approve or revise

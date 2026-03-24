/**
 * core/api/index.js
 * Public API facade composed from domain-specific modules.
 */
export {
    deleteSession,
    fetchAgentMessages,
    fetchAgentReflection,
    fetchSessionAgents,
    fetchSessionHistory,
    fetchSessionRecovery,
    fetchSessionRounds,
    fetchSessions,
    fetchSessionTasks,
    updateSessionTopology,
    refreshAgentReflection,
    updateAgentReflection,
    deleteAgentReflection,
    startNewSession,
    updateSession,
} from './sessions.js';

export {
    dispatchHumanTask,
    injectMessage,
    injectSubagentMessage,
    resolveGate,
    resolveToolApproval,
    resumeRun,
    sendUserPrompt,
    stopRun,
} from './runs.js';

export {
    fetchRoleConfigOptions,
    fetchRoleConfig,
    fetchRoleConfigs,
    saveRoleConfig,
    validateRoleConfig,
} from './roles.js';

export {
    deleteEnvironmentVariable,
    deleteModelProfile,
    fetchConfigStatus,
    fetchUiLanguageSettings,
    fetchEnvironmentVariables,
    fetchMcpServerTools,
    fetchModelConfig,
    fetchModelProfiles,
    fetchNotificationConfig,
    fetchOrchestrationConfig,
    fetchProxyConfig,
    fetchWebConfig,
    fetchSystemHealth,
    discoverModelCatalog,
    probeModelConnection,
    probeWebConnectivity,
    reloadMcpConfig,
    reloadModelConfig,
    reloadProxyConfig,
    reloadSkillsConfig,
    saveEnvironmentVariable,
    saveModelConfig,
    saveModelProfile,
    saveNotificationConfig,
    saveOrchestrationConfig,
    saveProxyConfig,
    saveWebConfig,
    saveUiLanguageSettings,
} from './system.js';

export {
    createTrigger,
    disableTrigger,
    enableTrigger,
    fetchTriggers,
    rotateTriggerToken,
    updateTrigger,
} from './triggers.js';

export {
    fetchRunTokenUsage,
    fetchSessionTokenUsage,
} from './token_usage.js';

export {
    deleteWorkspace,
    fetchWorkspaceDiffFile,
    fetchWorkspaceDiffs,
    fetchWorkspaceSnapshot,
    fetchWorkspaceTree,
    fetchWorkspaces,
    forkWorkspace,
    pickWorkspace,
} from './workspaces.js';

export {
    fetchObservabilityBreakdowns,
    fetchObservabilityOverview,
} from './observability.js';


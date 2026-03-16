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
    refreshAgentReflection,
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
    fetchEnvironmentVariables,
    fetchMcpServerTools,
    fetchModelConfig,
    fetchModelProfiles,
    fetchNotificationConfig,
    fetchProxyConfig,
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
    saveProxyConfig,
} from './system.js';

export {
    fetchRunTokenUsage,
    fetchSessionTokenUsage,
} from './token_usage.js';

export {
    deleteWorkspace,
    fetchWorkspaces,
    forkWorkspace,
    pickWorkspace,
} from './workspaces.js';

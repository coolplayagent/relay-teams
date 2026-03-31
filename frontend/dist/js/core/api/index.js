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
    fetchRunBackgroundTerminal,
    fetchRunBackgroundTerminals,
    injectMessage,
    injectSubagentMessage,
    resolveGate,
    resolveToolApproval,
    resumeRun,
    sendUserPrompt,
    stopBackgroundTerminal,
    stopRun,
} from './runs.js';

export {
    deleteRoleConfig,
    fetchRoleConfigOptions,
    fetchRoleConfig,
    fetchRoleConfigs,
    saveRoleConfig,
    validateRoleConfig,
} from './roles.js';

export {
    deleteEnvironmentVariable,
    deleteExternalAgent,
    deleteModelProfile,
    fetchConfigStatus,
    fetchExternalAgent,
    fetchExternalAgents,
    fetchUiLanguageSettings,
    fetchEnvironmentVariables,
    fetchMcpServerTools,
    fetchModelConfig,
    fetchModelProfiles,
    fetchNotificationConfig,
    fetchOrchestrationConfig,
    fetchProxyConfig,
    fetchGitHubConfig,
    fetchWebConfig,
    fetchSystemHealth,
    discoverModelCatalog,
    probeGitHubConnectivity,
    probeModelConnection,
    probeWebConnectivity,
    reloadMcpConfig,
    reloadModelConfig,
    reloadProxyConfig,
    reloadSkillsConfig,
    saveExternalAgent,
    saveEnvironmentVariable,
    saveModelConfig,
    saveModelProfile,
    saveNotificationConfig,
    saveOrchestrationConfig,
    saveProxyConfig,
    saveGitHubConfig,
    saveWebConfig,
    saveUiLanguageSettings,
    testExternalAgent,
} from './system.js';

export {
    createTrigger,
    deleteTrigger,
    disableTrigger,
    enableTrigger,
    fetchTriggers,
    rotateTriggerToken,
    updateTrigger,
} from './triggers.js';

export {
    deleteWeChatGatewayAccount,
    disableWeChatGatewayAccount,
    enableWeChatGatewayAccount,
    fetchWeChatGatewayAccounts,
    reloadWeChatGateway,
    startWeChatGatewayLogin,
    updateWeChatGatewayAccount,
    waitWeChatGatewayLogin,
} from './gateway.js';

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


export {
    createAutomationProject,
    deleteAutomationProject,
    disableAutomationProject,
    enableAutomationProject,
    fetchAutomationFeishuBindings,
    fetchAutomationProject,
    fetchAutomationProjectSessions,
    fetchAutomationProjects,
    runAutomationProject,
    updateAutomationProject,
} from './automation.js';

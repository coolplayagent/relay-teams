/**
 * components/messageRenderer.js
 * Backward-compatible facade. New implementation lives under ./messageRenderer/.
 */
export {
    renderMessageBlock,
    renderHistoricalMessageList,
    getOrCreateStreamBlock,
    appendStreamChunk,
    finalizeStream,
    clearStreamState,
    clearRunStreamState,
    clearAllStreamState,
    getCoordinatorStreamOverlay,
    getInstanceStreamOverlay,
    getRunStreamOverlaySnapshot,
    startThinkingBlock,
    appendThinkingChunk,
    finalizeThinking,
    appendToolCallBlock,
    updateToolResult,
    markToolInputValidationFailed,
    attachToolApprovalControls,
    markToolApprovalResolved,
    applyStreamOverlayEvent,
} from './messageRenderer/index.js';

/**
 * components/messageRenderer/index.js
 * Public API for message rendering features.
 */
export { renderMessageBlock } from './helpers.js';
export { renderHistoricalMessageList } from './history.js';
export {
    getOrCreateStreamBlock,
    appendStreamChunk,
    appendStreamOutputParts,
    finalizeStream,
    clearStreamState,
    clearRunStreamState,
    clearRenderedStreamState,
    clearAllStreamState,
    getCoordinatorStreamOverlay,
    getInstanceStreamOverlay,
    getRunStreamOverlaySnapshot,
    bindStreamOverlayToContainer,
    startThinkingBlock,
    appendThinkingChunk,
    finalizeThinking,
    appendToolCallBlock,
    updateToolResult,
    markToolInputValidationFailed,
    attachToolApprovalControls,
    markToolApprovalResolved,
    applyStreamOverlayEvent,
} from './stream.js';

/**
 * components/messageRenderer/helpers.js
 * Backward-compatible facade. New implementation lives under ./helpers/.
 */
export {
    renderMessageBlock,
    renderParts,
    labelFromRole,
    scrollBottom,
    appendMessageText,
    appendThinkingText,
    updateMessageText,
    updateThinkingText,
    syncStreamingCursor,
} from './helpers/block.js';

export {
    buildToolBlock,
    findToolBlock,
    setToolValidationFailureState,
    applyToolReturn,
    indexPendingToolBlock,
    resolvePendingToolBlock,
    findToolBlockInContainer,
} from './helpers/toolBlocks.js';

export {
    decoratePendingApprovalBlock,
    parseApprovalArgsPreview,
    syncApprovalStateFromEnvelope,
} from './helpers/approval.js';

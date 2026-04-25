/**
 * components/messageRenderer/helpers.js
 * Backward-compatible facade. New implementation lives under ./helpers/.
 */
export {
    renderMessageBlock,
    renderParts,
    labelFromRole,
    scrollBottom,
    forceScrollBottom,
    appendMessageText,
    appendThinkingText,
    updateMessageText,
    updateThinkingText,
    syncStreamingCursor,
    clearThinkingOpenState,
    clearThinkingOpenStateForRun,
} from './helpers/block.js';

export {
    renderRichContent,
    appendStructuredContentPart,
} from './helpers/content.js';

export {
    buildToolBlock,
    buildPendingToolBlock,
    findToolBlock,
    setToolValidationFailureState,
    applyToolReturn,
    setToolStatus,
    indexPendingToolBlock,
    resolvePendingToolBlock,
    findToolBlockInContainer,
} from './helpers/toolBlocks.js';

export {
    decoratePendingApprovalBlock,
    parseApprovalArgsPreview,
    syncApprovalStateFromEnvelope,
} from './helpers/approval.js';

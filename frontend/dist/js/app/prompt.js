/**
 * app/prompt.js
 * Prompt send flow: live round bootstrap and SSE stream start.
 */
import {
  createLiveRound,
} from "../components/rounds/timeline.js";
import { refreshVisibleContextIndicators } from "../components/contextIndicators.js";
import { clearAllStreamState } from "../components/messageRenderer.js";
import {
  fetchRoleConfigOptions,
  fetchCommands,
  fetchOrchestrationConfig,
  resolveCommandPrompt,
  searchWorkspacePaths,
  updateSessionTopology,
} from "../core/api.js";
import {
  applyDraftSessionTopology,
  ensureSessionForNewSessionDraft,
  isNewSessionDraftActive,
} from "../components/newSessionDraft.js";
import { hydrateSessionView, startSessionContinuity } from "./recovery.js";
import {
  applyCurrentSessionRecord,
  getCoordinatorRoleId,
  getRoleInputModalitySupport,
  getRoleOption,
  getMainAgentRoleId,
  getNormalModeRoles,
  getPrimaryRoleId,
  getRoleDisplayName,
  setCoordinatorRoleOption,
  setMainAgentRoleOption,
  setCoordinatorRoleId,
  setMainAgentRoleId,
  setNormalModeRoles,
  state,
} from "../core/state.js";
import { startIntentStream } from "../core/stream.js";
import { els } from "../utils/dom.js";
import { showToast } from "../utils/feedback.js";
import { formatMessage, t } from "../utils/i18n.js";
import { sysLog } from "../utils/logger.js";
import { renderPromptTokenChipsHtml } from "../utils/promptTokens.js";

const YOLO_STORAGE_KEY = "agent_teams_yolo";
const THINKING_MODE_STORAGE_KEY = "agent_teams_thinking_enabled";
const THINKING_EFFORT_STORAGE_KEY = "agent_teams_thinking_effort";
const DEFAULT_PROMPT_MENTION_TRIGGER = "@";
const PROMPT_COMMAND_AUTOCOMPLETE_STATUS = Object.freeze({
  IDLE: "idle",
  LOADING: "loading",
  READY: "ready",
  EMPTY: "empty",
  NO_MATCH: "no_match",
  NO_WORKSPACE: "no_workspace",
  ERROR: "error",
});
let orchestrationConfig = {
  default_orchestration_preset_id: "",
  presets: [],
};
let topologyControlsBound = false;
let promptMentionAutocompleteBound = false;
let promptMentionOptions = [];
let activePromptMentionIndex = -1;
let promptMentionQuery = "";
let promptMentionTrigger = DEFAULT_PROMPT_MENTION_TRIGGER;
let promptMentionRange = {
  start: 0,
  end: 0,
};
let promptMentionKind = null;
let promptMentionSessionKey = "";
let promptMentionPlacementSide = null;
let promptMentionPreviewSnapshot = null;
let promptMentionPreviewValue = "";
let promptCommandOptions = [];
let promptCommandWorkspaceId = "";
let promptCommandLoadingWorkspaceId = "";
let promptCommandLoadErrorWorkspaceId = "";
let promptCommandLoadErrorMessage = "";
let promptCommandAutocompleteStatus = PROMPT_COMMAND_AUTOCOMPLETE_STATUS.IDLE;
let promptCommandRequestSequence = 0;
let promptCommandActiveRequestToken = 0;
let promptSkillOptions = [];
let promptResourceOptions = [];
let promptResourceWorkspaceId = "";
let promptResourceQuery = "";
let promptResourceLoadingKey = "";
let promptResourceLoadErrorKey = "";
let promptResourceLoadErrorMessage = "";
let promptResourceRequestSequence = 0;
let promptResourceActiveRequestToken = 0;
let promptResourceCachedWorkspaceId = "";
let promptResourceCachedOptions = [];
const promptResourceQueryCache = new Map();
let promptResourceDebounceTimer = null;
const PROMPT_MENTION_MENU_MAX_HEIGHT = 420;
const PROMPT_MENTION_MENU_SAFE_MARGIN = 16;
const PROMPT_MENTION_MENU_GAP = 8;
const PROMPT_RESOURCE_SEARCH_DEBOUNCE_MS = 80;
let promptAttachments = [];
let promptAttachmentSequence = 0;
let promptComposerStatus = null;

export function initializeYoloToggle() {
  const savedYolo = readSavedYolo();
  applyYolo(savedYolo, { persist: false });
  if (!els.yoloToggle) return;
  els.yoloToggle.checked = savedYolo;
  els.yoloToggle.addEventListener("change", () => {
    applyYolo(els.yoloToggle.checked);
  });
}

export function initializeThinkingControls() {
  const savedThinking = readSavedThinkingState();
  applyThinkingState(savedThinking, { persist: false });
  if (els.thinkingModeToggle) {
    els.thinkingModeToggle.checked = savedThinking.enabled === true;
    els.thinkingModeToggle.addEventListener("change", () => {
      applyThinkingState({
        enabled: els.thinkingModeToggle.checked,
        effort: state.thinking?.effort || "medium",
      });
    });
  }
  if (els.thinkingEffortSelect) {
    els.thinkingEffortSelect.value = savedThinking.effort || "medium";
    els.thinkingEffortSelect.addEventListener("change", () => {
      applyThinkingState({
        enabled: state.thinking?.enabled === true,
        effort: String(els.thinkingEffortSelect.value || "medium"),
      });
    });
  }
}

export async function initializeSessionTopologyControls() {
  await refreshRoleConfigOptions({ refreshControls: false });
  await refreshOrchestrationConfig({ refreshControls: false });
  bindSessionTopologyControls();
  refreshSessionTopologyControls();
}

export function refreshSessionTopologyControls() {
  syncThinkingControls();
  if (
    !els.sessionModeLock ||
    !els.sessionModeNormalBtn ||
    !els.sessionModeOrchestrationBtn
  ) {
    return;
  }

  const mode =
    state.currentSessionMode === "orchestration" ? "orchestration" : "normal";
  const normalModeRoles = getNormalModeRoles();
  const presets = Array.isArray(orchestrationConfig?.presets)
    ? orchestrationConfig.presets
    : [];
  const hasNormalModeRoles = normalModeRoles.length > 0;
  const hasPresets = presets.length > 0;
  const isDraft = isNewSessionDraftActive();
  const canSwitch =
    (isDraft ||
      (!!state.currentSessionId && state.currentSessionCanSwitchMode === true)) &&
    !state.isGenerating;
  const disabledReason = resolveTopologyDisabledReason({
    canSwitch,
    hasPresets,
  });
  const orchestrationDisabled = !canSwitch || !hasPresets;

  els.sessionModeLock.title = disabledReason;
  els.sessionModeNormalBtn.disabled = !canSwitch;
  els.sessionModeOrchestrationBtn.disabled = orchestrationDisabled;
  els.sessionModeNormalBtn.classList.toggle("active", mode === "normal");
  els.sessionModeOrchestrationBtn.classList.toggle(
    "active",
    mode === "orchestration",
  );

  if (els.sessionModeLabel) {
    els.sessionModeLabel.textContent =
      mode === "orchestration"
        ? t("composer.mode_orchestration")
        : t("composer.mode_normal");
  }

  syncSessionTopologyFieldVisibility(mode);
  if (els.normalRoleSelect) {
    const selectedRoleId = resolveSelectedNormalRoleId();
    els.normalRoleSelect.innerHTML = buildNormalRoleOptions(selectedRoleId);
    els.normalRoleSelect.disabled =
      !canSwitch || mode !== "normal" || !hasNormalModeRoles;
    if (selectedRoleId) {
      els.normalRoleSelect.value = selectedRoleId;
    }
  }

  if (els.orchestrationPresetSelect) {
    const selectedPresetId = resolveSelectedPresetId();
    els.orchestrationPresetSelect.innerHTML =
      buildPresetOptions(selectedPresetId);
    els.orchestrationPresetSelect.disabled =
      !canSwitch || mode !== "orchestration" || !hasPresets;
    if (selectedPresetId) {
      els.orchestrationPresetSelect.value = selectedPresetId;
    }
  }
}

export async function refreshOrchestrationConfig({
  refreshControls = true,
} = {}) {
  try {
    const config = await fetchOrchestrationConfig();
    orchestrationConfig = normalizeOrchestrationConfig(config);
  } catch (error) {
    orchestrationConfig = normalizeOrchestrationConfig(null);
    sysLog(
      error.message || t("composer.error.orchestration_load_failed"),
      "log-error",
    );
  }
  if (refreshControls) {
    refreshSessionTopologyControls();
  }
}

export async function refreshRoleConfigOptions({ refreshControls = true } = {}) {
  try {
    const options = await fetchRoleConfigOptions();
    setCoordinatorRoleId(options?.coordinator_role_id || "");
    setMainAgentRoleId(options?.main_agent_role_id || "");
    setCoordinatorRoleOption(options?.coordinator_role || null);
    setMainAgentRoleOption(options?.main_agent_role || null);
    setNormalModeRoles(options?.normal_mode_roles || []);
    promptSkillOptions = normalizePromptSkillOptions(options?.skills || []);
  } catch (error) {
    setCoordinatorRoleId("");
    setMainAgentRoleId("");
    setCoordinatorRoleOption(null);
    setMainAgentRoleOption(null);
    setNormalModeRoles([]);
    promptSkillOptions = [];
    sysLog(error.message || t("composer.error.role_options_load_failed"), "log-error");
  }
  handlePromptComposerInput();
  if (refreshControls) {
    refreshSessionTopologyControls();
  }
}

export async function handleSend() {
  const rawText = els.promptInput.value.trim();
  const hasAttachments = promptAttachments.length > 0;
  if (!rawText && !hasAttachments) return;
  if (state.isGenerating) {
    sysLog(
      t("composer.warning.run_in_progress"),
      "log-info",
    );
    return;
  }
  if (!state.currentSessionId && !isNewSessionDraftActive()) {
    sysLog(
      t("composer.error.no_active_session"),
      "log-error",
    );
    return;
  }
  if (state.pausedSubagent) {
    const paused = state.pausedSubagent;
    sysLog(
      formatMessage("composer.error.paused_subagent", {
        agent: paused.roleId || paused.instanceId,
      }),
      "log-error",
    );
    return;
  }

  const mention = parseLeadingRoleMention(rawText);
  if (startsWithPromptMention(rawText) && mention.error) {
    sysLog(mention.error, "log-error");
    return;
  }
  const text = mention.roleId ? mention.promptText : rawText;
  if (!text && !hasAttachments) {
    sysLog(t("composer.error.empty_after_mention"), "log-error");
    return;
  }
  const targetRoleId = mention.roleId || null;
  const effectiveTargetRoleId = targetRoleId || getPrimaryRoleId();
  const imageInputBlockedMessage = resolveImageInputBlockedMessage({
    rawText,
    targetRoleId: effectiveTargetRoleId,
  });
  if (imageInputBlockedMessage) {
    setPromptComposerStatus(imageInputBlockedMessage, { tone: "danger" });
    showToast({
      title: t("composer.toast.send_blocked_title"),
      message: imageInputBlockedMessage,
      tone: "warning",
    });
    sysLog(imageInputBlockedMessage, "log-error");
    return;
  }
  state.isGenerating = true;
  if (els.sendBtn) els.sendBtn.disabled = true;
  if (els.promptInput) els.promptInput.disabled = true;
  refreshSessionTopologyControls();
  if (isNewSessionDraftActive()) {
    try {
      const sessionId = await ensureSessionForNewSessionDraft();
      if (!sessionId) {
        state.isGenerating = false;
        if (els.sendBtn) els.sendBtn.disabled = false;
        if (els.promptInput) els.promptInput.disabled = false;
        refreshSessionTopologyControls();
        sysLog(t("composer.error.no_active_session"), "log-error");
        return;
      }
    } catch (error) {
      const message = error?.message || String(error);
      state.isGenerating = false;
      if (els.sendBtn) els.sendBtn.disabled = false;
      if (els.promptInput) els.promptInput.disabled = false;
      refreshSessionTopologyControls();
      setPromptComposerStatus(message, { tone: "danger" });
      sysLog(
        formatMessage("sidebar.error.creating_session", { error: message }),
        "log-error",
      );
      return;
    }
  }
  if (!state.currentSessionId) {
    state.isGenerating = false;
    if (els.sendBtn) els.sendBtn.disabled = false;
    if (els.promptInput) els.promptInput.disabled = false;
    refreshSessionTopologyControls();
    sysLog(t("composer.error.no_active_session"), "log-error");
    return;
  }
  clearPromptComposerStatus();
  const resolvedPrompt = await resolvePromptCommandText(text);
  if (resolvedPrompt === null) {
    restorePromptComposerAfterSendAbort();
    return;
  }
  const inputParts = buildPromptInputParts(resolvedPrompt.text);
  const displayInputParts = buildPromptInputParts(text);
  const promptPreviewText = text || summarizePromptAttachments(promptAttachments);

  dismissPromptMentionAutocomplete();
  resetPromptComposer();
  state.instanceRoleMap = {};
  state.roleInstanceMap = {};
  state.taskInstanceMap = {};
  state.activeAgentRoleId = null;
  state.activeAgentInstanceId = null;
  state.autoSwitchedSubagentInstances = {};
  state.activeRunId = null;
  if (els.stopBtn) {
    els.stopBtn.style.display = "inline-flex";
    els.stopBtn.disabled = false;
  }
  refreshSessionTopologyControls();
  refreshVisibleContextIndicators({ immediate: true });
  clearAllStreamState({ preserveOverlay: true });

  sysLog(t("composer.log.sending_prompt"));
  startSessionContinuity(state.currentSessionId);
  await startIntentStream(
    promptPreviewText,
    state.currentSessionId,
    async (sid) =>
      hydrateSessionView(sid, { includeRounds: true, quiet: true }),
    {
      inputParts,
      displayInputParts,
      skills: resolvedPrompt.skills,
      yolo: state.yolo,
      thinking: state.thinking,
      targetRoleId,
      onRunCreated: (run) => {
        state.currentSessionCanSwitchMode = false;
        refreshSessionTopologyControls();
        createLiveRound(run.run_id, promptPreviewText, displayInputParts);
      },
    },
  );
}

function restorePromptComposerAfterSendAbort() {
  state.isGenerating = false;
  if (els.sendBtn) els.sendBtn.disabled = false;
  if (els.promptInput) els.promptInput.disabled = false;
  refreshSessionTopologyControls();
}

export function initializePromptMentionAutocomplete() {
  if (promptMentionAutocompleteBound) {
    return;
  }
  promptMentionAutocompleteBound = true;

  if (els.promptMentionMenu) {
    els.promptMentionMenu.addEventListener("click", (event) => {
      const optionEl = findPromptMentionOptionElement(event?.target);
      const optionIndex = Number(optionEl?.dataset?.index || -1);
      if (optionIndex < 0) {
        return;
      }
      event.preventDefault?.();
      event.stopPropagation?.();
      selectPromptMentionOption(optionIndex);
    });
  }

  if (typeof document.addEventListener === "function") {
    document.addEventListener("click", (event) => {
      if (
        containsNode(els.promptInput, event?.target) ||
        containsNode(els.promptMentionMenu, event?.target)
      ) {
        return;
      }
      dismissPromptMentionAutocomplete();
    });
    document.addEventListener("agent-teams-commands-updated", () => {
      invalidatePromptCommandsCache();
    });
    document.addEventListener("agent-teams-new-session-draft-opened", () => {
      invalidatePromptResourceCache();
      invalidatePromptCommandsCache();
      refreshPromptMentionAutocomplete();
    });
    document.addEventListener("agent-teams-draft-workspace-added", () => {
      invalidatePromptResourceCache();
      invalidatePromptCommandsCache();
      refreshPromptMentionAutocomplete();
    });
    document.addEventListener("agent-teams-draft-workspace-selected", () => {
      invalidatePromptResourceCache();
      invalidatePromptCommandsCache();
      refreshPromptMentionAutocomplete();
    });
  }
}

export function handlePromptComposerInput() {
  acceptPromptMentionPreviewIfUserEdited();
  renderPromptAttachments();
  renderPromptTokenPreview();
  refreshPromptMentionAutocomplete();
  refreshPromptComposerValidation();
}

export async function handlePromptComposerPaste(event) {
  const clipboardItems = Array.from(event?.clipboardData?.items || []);
  const imageItems = clipboardItems.filter(
    (item) => String(item?.type || "").startsWith("image/"),
  );
  if (imageItems.length === 0) {
    return;
  }
  event.preventDefault?.();
  const nextAttachments = await Promise.all(
    imageItems
      .map((item, index) => item?.getAsFile?.() || null)
      .filter(Boolean)
      .map((file, index) => normalizePastedImageAttachment(file, index)),
  );
  promptAttachments = [...promptAttachments, ...nextAttachments.filter(Boolean)];
  handlePromptComposerInput();
  els.promptInput?.focus?.();
}

export function handlePromptComposerKeydown(event) {
  if (!isPromptMentionAutocompleteOpen()) {
    return false;
  }
  if (promptMentionOptions.length === 0) {
    if (event?.key === "Escape") {
      preventPromptMentionDefault(event);
      restorePromptMentionPreviewSnapshot();
      dismissPromptMentionAutocomplete();
      return true;
    }
    return false;
  }
  if (event?.key === "ArrowDown") {
    preventPromptMentionDefault(event);
    movePromptMentionSelection(1);
    return true;
  }
  if (event?.key === "ArrowUp") {
    preventPromptMentionDefault(event);
    movePromptMentionSelection(-1);
    return true;
  }
  if (event?.key === "Enter" || event?.key === "Tab") {
    preventPromptMentionDefault(event);
    return selectPromptMentionOption(activePromptMentionIndex);
  }
  if (event?.key === "Escape") {
    preventPromptMentionDefault(event);
    restorePromptMentionPreviewSnapshot();
    dismissPromptMentionAutocomplete();
    return true;
  }
  return false;
}

function buildPromptInputParts(text) {
  const trimmedText = String(text || "").trim();
  const parts = [];
  if (trimmedText) {
    parts.push({
      kind: "text",
      text: trimmedText,
    });
  }
  promptAttachments.forEach((attachment) => {
    parts.push({
      kind: "inline_media",
      modality: "image",
      mime_type: attachment.mimeType,
      base64_data: attachment.base64Data,
      name: attachment.name,
      size_bytes: attachment.sizeBytes,
      width: attachment.width,
      height: attachment.height,
    });
  });
  return parts;
}

function summarizePromptAttachments(attachments) {
  const count = Array.isArray(attachments) ? attachments.length : 0;
  if (count <= 0) {
    return "";
  }
  return count === 1 ? "[image]" : `[${count} images]`;
}

function resetPromptComposer() {
  if (els.promptInput) {
    els.promptInput.value = "";
    els.promptInput.style.height = "auto";
  }
  promptAttachments = [];
  clearPromptComposerStatus();
  renderPromptAttachments();
  renderPromptTokenPreview();
}

function renderPromptAttachments() {
  const container = els.promptAttachments;
  if (!container) {
    return;
  }
  container.classList.toggle(
    "is-error",
    promptComposerStatus?.tone === "danger" && promptAttachments.length > 0,
  );
  if (promptAttachments.length === 0) {
    container.innerHTML = "";
    container.hidden = true;
    return;
  }
  container.hidden = false;
  container.innerHTML = promptAttachments
    .map((attachment) => {
      const label = formatAttachmentSize(attachment.sizeBytes);
      return `
        <div class="prompt-attachment" data-attachment-id="${escapeHtml(
          attachment.id,
        )}">
          <img
            class="prompt-attachment-thumb"
            src="${escapeHtml(attachment.previewUrl)}"
            alt="${escapeHtml(attachment.name)}"
            role="button"
            tabindex="0"
            title="${escapeHtml(t("media.preview_open"))}"
            data-image-preview-trigger="true"
            data-image-preview-src="${escapeHtml(attachment.previewUrl)}"
            data-image-preview-name="${escapeHtml(attachment.name)}"
          />
          <div class="prompt-attachment-copy">
            <span class="prompt-attachment-name">${escapeHtml(
              attachment.name,
            )}</span>
            <span class="prompt-attachment-meta">${escapeHtml(label)}</span>
          </div>
          <button
            type="button"
            class="prompt-attachment-remove"
            data-attachment-remove="${escapeHtml(attachment.id)}"
            aria-label="Remove image"
            title="Remove image"
          >
            <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              />
            </svg>
          </button>
        </div>
      `;
    })
    .join("");
  if (typeof container.querySelectorAll !== "function") {
    return;
  }
  container
    .querySelectorAll("[data-attachment-remove]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        const attachmentId = String(
          button.getAttribute("data-attachment-remove") || "",
        ).trim();
        if (!attachmentId) {
          return;
        }
        promptAttachments = promptAttachments.filter(
          (attachment) => attachment.id !== attachmentId,
        );
        handlePromptComposerInput();
        els.promptInput?.focus?.();
      });
    });
}

function renderPromptTokenPreview() {
  const input = els.promptInput;
  const wrapper = input?.parentElement || null;
  if (!input || !wrapper || typeof wrapper.querySelector !== "function") {
    return;
  }
  let host = wrapper.querySelector(".prompt-token-preview");
  const html = renderPromptTokenChipsHtml(
    String(input.value || ""),
    getPromptTokenRenderOptions(),
  );
  if (!html) {
    host?.remove?.();
    return;
  }
  if (!host) {
    host = document.createElement("div");
    host.className = "prompt-token-preview";
    wrapper.insertBefore(host, input);
  }
  host.innerHTML = html;
}

function getPromptTokenRenderOptions() {
  return {
    skills: promptSkillOptions.flatMap((option) => option.aliases || []),
    commands: listPromptCommandOptions().flatMap((option) => option.aliases || []),
  };
}

function refreshPromptComposerValidation() {
  const blockedMessage = resolveImageInputBlockedMessage({
    rawText: String(els.promptInput?.value || "").trim(),
  });
  if (!blockedMessage) {
    clearPromptComposerStatus();
    return;
  }
  setPromptComposerStatus(blockedMessage, { tone: "danger" });
}

function resolveImageInputBlockedMessage({
  rawText = "",
  targetRoleId = null,
} = {}) {
  if (promptAttachments.length === 0) {
    return "";
  }
  const resolvedTargetRoleId =
    String(targetRoleId || "").trim() || resolvePromptTargetRoleId(rawText);
  if (!resolvedTargetRoleId) {
    return "";
  }
  const imageSupport = getRoleInputModalitySupport(
    resolvedTargetRoleId,
    "image",
  );
  if (imageSupport === true) {
    return "";
  }
  const targetLabel = resolveImageInputTargetLabel(resolvedTargetRoleId);
  if (imageSupport === null) {
    return formatMessage("composer.error.image_input_unknown", {
      agent: targetLabel,
    });
  }
  return formatMessage("composer.error.image_input_unsupported", {
    agent: targetLabel,
  });
}

function resolveImageInputTargetLabel(roleId) {
  const roleOption = getRoleOption(roleId);
  const modelName = String(roleOption?.model_name || "").trim();
  if (modelName) {
    return modelName;
  }
  const modelProfile = String(roleOption?.model_profile || "").trim();
  if (modelProfile) {
    return modelProfile;
  }
  return getRoleDisplayName(roleId, { fallback: "Agent" });
}

function resolvePromptTargetRoleId(rawText) {
  const promptText = String(rawText || "").trim();
  const mention = parseLeadingRoleMention(promptText);
  if (startsWithPromptMention(promptText) && mention.error) {
    return "";
  }
  return mention.roleId || getPrimaryRoleId();
}

function setPromptComposerStatus(message, { tone = "danger" } = {}) {
  promptComposerStatus = message
    ? {
        message: String(message || ""),
        tone,
      }
    : null;
  const statusEl = els.promptInputStatus;
  if (statusEl) {
    statusEl.hidden = !promptComposerStatus;
    statusEl.textContent = promptComposerStatus?.message || "";
    statusEl.className = promptComposerStatus
      ? `prompt-input-status is-${promptComposerStatus.tone}`
      : "prompt-input-status";
  }
  els.promptAttachments?.classList?.toggle(
    "is-error",
    promptComposerStatus?.tone === "danger" && promptAttachments.length > 0,
  );
}

function clearPromptComposerStatus() {
  if (!promptComposerStatus && !els.promptInputStatus) {
    return;
  }
  promptComposerStatus = null;
  const statusEl = els.promptInputStatus;
  if (statusEl) {
    statusEl.hidden = true;
    statusEl.textContent = "";
    statusEl.className = "prompt-input-status";
  }
  els.promptAttachments?.classList?.toggle("is-error", false);
}

async function normalizePastedImageAttachment(file, index) {
  if (!file) {
    return null;
  }
  const previewUrl = await readFileAsDataUrl(file);
  const { base64Data, mimeType } = parseDataUrl(previewUrl);
  return {
    id: `attachment-${Date.now()}-${promptAttachmentSequence++}`,
    name: resolveAttachmentName(file, index, mimeType),
    mimeType,
    sizeBytes: Number.isFinite(file.size) ? Number(file.size) : null,
    base64Data,
    previewUrl,
    width: null,
    height: null,
  };
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () =>
      reject(reader.error || new Error("Failed to read pasted image"));
    reader.readAsDataURL(file);
  });
}

function parseDataUrl(dataUrl) {
  const match = String(dataUrl || "").match(/^data:([^;]+);base64,(.+)$/);
  if (!match) {
    return {
      mimeType: "image/png",
      base64Data: "",
    };
  }
  return {
    mimeType: match[1],
    base64Data: match[2],
  };
}

function resolveAttachmentName(file, index, mimeType) {
  const explicitName = String(file?.name || "").trim();
  if (explicitName) {
    return explicitName;
  }
  const extension = mimeType === "image/jpeg" ? "jpg" : mimeType.split("/")[1] || "png";
  return `pasted-image-${index + 1}.${extension}`;
}

function formatAttachmentSize(sizeBytes) {
  const size = Number(sizeBytes);
  if (!Number.isFinite(size) || size <= 0) {
    return "Image";
  }
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (size >= 1024) {
    return `${Math.round(size / 1024)} KB`;
  }
  return `${size} B`;
}

function bindSessionTopologyControls() {
  if (topologyControlsBound) {
    return;
  }
  topologyControlsBound = true;

  if (els.sessionModeNormalBtn) {
    els.sessionModeNormalBtn.addEventListener("click", () => {
      void handleTopologyModeChange("normal");
    });
  }
  if (els.sessionModeOrchestrationBtn) {
    els.sessionModeOrchestrationBtn.addEventListener("click", () => {
      void handleTopologyModeChange("orchestration");
    });
  }
  if (els.orchestrationPresetSelect) {
    els.orchestrationPresetSelect.addEventListener("change", (event) => {
      const nextPresetId = String(event?.target?.value || "").trim();
      if (!nextPresetId) {
        refreshSessionTopologyControls();
        return;
      }
      void persistSessionTopology("orchestration", {
        orchestrationPresetId: nextPresetId,
      });
    });
  }
  if (els.normalRoleSelect) {
    els.normalRoleSelect.addEventListener("change", (event) => {
      const nextRoleId = String(event?.target?.value || "").trim();
      if (!nextRoleId) {
        refreshSessionTopologyControls();
        return;
      }
      void persistSessionTopology("normal", {
        normalRootRoleId: nextRoleId,
      });
    });
  }
  if (typeof document.addEventListener === "function") {
    document.addEventListener("orchestration-settings-updated", () => {
      void refreshOrchestrationConfig({ refreshControls: true });
    });
    document.addEventListener("agent-teams-session-selected", () => {
      void refreshRoleConfigOptions({ refreshControls: true });
    });
    document.addEventListener("agent-teams-model-profiles-updated", () => {
      void refreshRoleConfigOptions({ refreshControls: true });
    });
    document.addEventListener("agent-teams-language-changed", () => {
      refreshSessionTopologyControls();
    });
    document.addEventListener("agent-teams-new-session-draft-opened", () => {
      refreshSessionTopologyControls();
    });
  }
}

async function handleTopologyModeChange(nextMode) {
  const normalizedMode =
    nextMode === "orchestration" ? "orchestration" : "normal";
  if (normalizedMode === state.currentSessionMode) {
    return;
  }
  if (!state.currentSessionId && !isNewSessionDraftActive()) {
    return;
  }
  if (normalizedMode === "orchestration" && !resolveSelectedPresetId()) {
    showToast({
      title: t("composer.toast.no_preset_title"),
      message: resolveMissingPresetMessage(),
      tone: "warning",
    });
    return;
  }
  if (!state.currentSessionId && isNewSessionDraftActive()) {
    applyDraftSessionTopology(normalizedMode, {
      orchestrationPresetId:
        normalizedMode === "orchestration" ? resolveSelectedPresetId() : null,
      normalRootRoleId:
        normalizedMode === "normal" ? resolveSelectedNormalRoleId() : null,
    });
    refreshSessionTopologyControls();
    return;
  }
  await persistSessionTopology(normalizedMode, {
    orchestrationPresetId:
      normalizedMode === "orchestration" ? resolveSelectedPresetId() : null,
    normalRootRoleId:
      normalizedMode === "normal" ? resolveSelectedNormalRoleId() : null,
  });
}

async function persistSessionTopology(
  sessionMode,
  { orchestrationPresetId = null, normalRootRoleId = null } = {},
) {
  if (!state.currentSessionId) {
    if (isNewSessionDraftActive()) {
      applyDraftSessionTopology(sessionMode, {
        orchestrationPresetId,
        normalRootRoleId,
      });
      refreshSessionTopologyControls();
    }
    return;
  }
  try {
    const updated = await updateSessionTopology(state.currentSessionId, {
      session_mode: sessionMode,
      normal_root_role_id:
        sessionMode === "normal" ? normalRootRoleId : undefined,
      orchestration_preset_id:
        sessionMode === "orchestration" ? orchestrationPresetId : null,
    });
    applyCurrentSessionRecord(updated);
    refreshSessionTopologyControls();
    sysLog(
      `Session mode updated: ${
        sessionMode === "orchestration"
          ? t("composer.mode_orchestration")
          : t("composer.mode_normal")
      }`,
    );
  } catch (error) {
    refreshSessionTopologyControls();
    showToast({
      title: t("composer.toast.mode_update_failed_title"),
      message: error.message || t("composer.error.mode_update_failed"),
      tone: "danger",
    });
  }
}

function resolveTopologyDisabledReason({ canSwitch, hasPresets }) {
  if (!state.currentSessionId && !isNewSessionDraftActive()) {
    return t("composer.session_mode_title");
  }
  if (state.isGenerating) {
    return t("composer.disabled.active_run");
  }
  if (!canSwitch) {
    return t("composer.disabled.started_session");
  }
  if (!hasPresets) {
    return resolveMissingPresetMessage();
  }
  return t("composer.disabled.started_session");
}

function resolveSelectedPresetId() {
  const presets = Array.isArray(orchestrationConfig?.presets)
    ? orchestrationConfig.presets
    : [];
  const currentPresetId = String(
    state.currentOrchestrationPresetId || "",
  ).trim();
  if (
    currentPresetId &&
    presets.some((preset) => preset?.preset_id === currentPresetId)
  ) {
    return currentPresetId;
  }
  const defaultPresetId = String(
    orchestrationConfig?.default_orchestration_preset_id || "",
  ).trim();
  if (
    defaultPresetId &&
    presets.some((preset) => preset?.preset_id === defaultPresetId)
  ) {
    return defaultPresetId;
  }
  return String(presets[0]?.preset_id || "").trim();
}

function resolveSelectedNormalRoleId() {
  const roles = getNormalModeRoles();
  if (roles.length === 0) {
    return "";
  }
  const currentRoleId = String(state.currentNormalRootRoleId || "").trim();
  if (currentRoleId && roles.some((role) => role?.role_id === currentRoleId)) {
    return currentRoleId;
  }
  const mainAgentRoleId = String(state.mainAgentRoleId || "").trim();
  if (
    mainAgentRoleId &&
    roles.some((role) => role?.role_id === mainAgentRoleId)
  ) {
    return mainAgentRoleId;
  }
  return String(roles[0]?.role_id || "").trim();
}

function buildNormalRoleOptions(selectedRoleId) {
  const roles = getNormalModeRoles();
  if (roles.length === 0) {
    return `<option value="">${escapeHtml(t("composer.no_roles"))}</option>`;
  }
  return roles
    .map((role) => {
      const roleId = String(role?.role_id || "").trim();
      const name = String(role?.name || roleId || "Role");
      const selected = roleId === selectedRoleId ? " selected" : "";
      return `<option value="${escapeHtml(roleId)}"${selected}>${escapeHtml(name)}</option>`;
    })
    .join("");
}

function buildPresetOptions(selectedPresetId) {
  const presets = Array.isArray(orchestrationConfig?.presets)
    ? orchestrationConfig.presets
    : [];
  if (presets.length === 0) {
    return `<option value="">${escapeHtml(t("composer.no_presets"))}</option>`;
  }
  return presets
    .map((preset) => {
      const presetId = String(preset?.preset_id || "").trim();
      const name = String(preset?.name || presetId || "Preset");
      const selected = presetId === selectedPresetId ? " selected" : "";
      return `<option value="${escapeHtml(presetId)}"${selected}>${escapeHtml(name)}</option>`;
    })
    .join("");
}

function resolveMissingPresetMessage() {
  return t("composer.disabled.no_preset");
}

function syncSessionTopologyFieldVisibility(mode) {
  const safeMode = mode === "orchestration" ? "orchestration" : "normal";
  if (els.normalRoleField) {
    const showNormalRole = safeMode === "normal";
    els.normalRoleField.hidden = !showNormalRole;
    els.normalRoleField.style.display = showNormalRole ? "inline-flex" : "none";
  }
  if (els.orchestrationPresetField) {
    const showPreset = safeMode === "orchestration";
    els.orchestrationPresetField.hidden = !showPreset;
    els.orchestrationPresetField.style.display = showPreset
      ? "inline-flex"
      : "none";
  }
}

function normalizeOrchestrationConfig(config) {
  const presets = Array.isArray(config?.presets)
    ? config.presets
        .map((preset) => ({
          preset_id: String(preset?.preset_id || "").trim(),
          name: String(preset?.name || "").trim(),
          description: String(preset?.description || "").trim(),
          role_ids: Array.isArray(preset?.role_ids)
            ? preset.role_ids
                .map((roleId) => String(roleId || "").trim())
                .filter(Boolean)
            : [],
          orchestration_prompt: String(
            preset?.orchestration_prompt || "",
          ).trim(),
        }))
        .filter((preset) => preset.preset_id)
    : [];
  return {
    default_orchestration_preset_id: String(
      config?.default_orchestration_preset_id || "",
    ).trim(),
    presets,
  };
}

function ensurePromptMentionSession(kind, context) {
  const nextSessionKey = getPromptMentionSessionKey(kind, context);
  if (promptMentionSessionKey === nextSessionKey) {
    return;
  }
  promptMentionSessionKey = nextSessionKey;
  promptMentionPlacementSide = null;
  clearPromptMentionPreviewSnapshot();
}

function getPromptMentionSessionKey(kind, context) {
  return [
    kind,
    context?.trigger || "",
    Number(context?.start || 0),
  ].join(":");
}

function refreshPromptMentionAutocomplete() {
  const commandContext = getPromptCommandContext();
  if (commandContext) {
    ensurePromptMentionSession("action", commandContext);
    void ensurePromptCommandsLoaded();
    const workspaceId = String(state.currentWorkspaceId || "").trim();
    const nextOptions = findPromptActionOptions(commandContext.query);
    const previousKey =
      getPromptOptionKey(promptMentionOptions[activePromptMentionIndex]) || "";
    promptMentionOptions = nextOptions;
    promptMentionQuery = commandContext.query;
    promptMentionTrigger = "/";
    promptMentionKind = "action";
    promptMentionRange = {
      start: commandContext.start,
      end: commandContext.end,
    };
    promptCommandAutocompleteStatus = resolvePromptCommandAutocompleteStatus({
      workspaceId,
      query: commandContext.query,
      optionCount: nextOptions.length,
    });
    const preservedIndex = promptMentionOptions.findIndex(
      (option) => getPromptOptionKey(option) === previousKey,
    );
    activePromptMentionIndex = nextOptions.length > 0
      ? preservedIndex >= 0 ? preservedIndex : 0
      : -1;
    renderPromptMentionAutocomplete();
    return;
  }

  const mentionContext = getPromptMentionContext();
  if (!mentionContext) {
    dismissPromptMentionAutocomplete();
    return;
  }
  ensurePromptMentionSession("resource", mentionContext);
  const mentionWorkspaceId = String(state.currentWorkspaceId || "").trim();
  promptResourceOptions = getLocalPromptResourceOptions(
    mentionWorkspaceId,
    mentionContext.query,
  );
  schedulePromptResourcesLoaded(mentionContext.query);
  const nextOptions = findPromptResourceOptions(mentionContext.query);

  const previousKey =
    getPromptOptionKey(promptMentionOptions[activePromptMentionIndex]) || "";
  promptMentionOptions = nextOptions;
  promptMentionQuery = mentionContext.query;
  promptMentionTrigger = mentionContext.trigger;
  promptMentionKind = "resource";
  promptMentionRange = {
    start: mentionContext.start,
    end: mentionContext.end,
  };
  const preservedIndex = promptMentionOptions.findIndex(
    (option) => getPromptOptionKey(option) === previousKey,
  );
  activePromptMentionIndex = nextOptions.length > 0
    ? preservedIndex >= 0 ? preservedIndex : 0
    : -1;
  renderPromptMentionAutocomplete();
}

function parseLeadingRoleMention(text) {
  const source = String(text || "").trim();
  const trigger = getPromptMentionTrigger(source);
  if (!trigger) {
    return { roleId: null, promptText: source, error: "" };
  }
  const candidates = listMentionableRoleCandidates();
  const matched = [];
  const normalizedSource = normalizePromptMentionSource(source).toLowerCase();
  candidates.forEach((candidate) => {
    const prefix = `@${candidate.term}`.toLowerCase();
    if (!normalizedSource.startsWith(prefix)) {
      return;
    }
    const nextChar = source.charAt(prefix.length);
    if (nextChar && !/\s/.test(nextChar)) {
      return;
    }
    matched.push(candidate);
  });
  if (matched.length === 0) {
    return {
      roleId: null,
      promptText: source,
      error: "",
    };
  }
  matched.sort((left, right) => right.term.length - left.term.length);
  const best = matched[0];
  const conflicts = matched.filter(
    (item) => item.term.length === best.term.length,
  );
  if (conflicts.length > 1) {
    return {
      roleId: null,
      promptText: source,
      error: formatMessage("composer.error.mention_ambiguous", {
        roles: conflicts.map((item) => item.term).join(", "),
      }),
    };
  }
  return {
    roleId: best.roleId,
    promptText: source.slice(best.term.length + 1).trim(),
    error: "",
  };
}

function listMentionableRoleCandidates() {
  const seen = new Set();
  const entries = [];
  const pushCandidate = (roleId, term) => {
    const safeRoleId = String(roleId || "").trim();
    const safeTerm = String(term || "").trim();
    if (!safeRoleId || !safeTerm) {
      return;
    }
    const key = `${safeRoleId}::${safeTerm.toLowerCase()}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    entries.push({ roleId: safeRoleId, term: safeTerm });
  };

  const coordinatorRoleId = getCoordinatorRoleId();
  const mainAgentRoleId = getMainAgentRoleId();
  if (coordinatorRoleId) {
    pushCandidate(coordinatorRoleId, "Coordinator");
    pushCandidate(coordinatorRoleId, coordinatorRoleId);
  }
  if (mainAgentRoleId) {
    pushCandidate(mainAgentRoleId, getRoleDisplayName(mainAgentRoleId));
    pushCandidate(mainAgentRoleId, mainAgentRoleId);
  }
  getNormalModeRoles().forEach((role) => {
    pushCandidate(role.role_id, role.name);
    pushCandidate(role.role_id, role.role_id);
  });
  return entries;
}

function listMentionableRoleOptions() {
  const entries = [];
  const byRoleId = new Map();
  const upsertOption = (
    roleId,
    displayName,
    { aliases = [], description = "" } = {},
  ) => {
    const safeRoleId = String(roleId || "").trim();
    const safeDisplayName = String(displayName || safeRoleId).trim();
    const safeDescription = String(description || "").trim();
    if (!safeRoleId || !safeDisplayName) {
      return;
    }
    const existing = byRoleId.get(safeRoleId);
    const nextAliases = [safeDisplayName, safeRoleId, ...aliases]
      .map((item) => String(item || "").trim())
      .filter(Boolean);
    if (existing) {
      if (
        existing.displayName.toLowerCase() === existing.roleId.toLowerCase() &&
        safeDisplayName.toLowerCase() !== safeRoleId.toLowerCase()
      ) {
        existing.displayName = safeDisplayName;
        existing.insertTerm = safeDisplayName;
      }
      if (!existing.description && safeDescription) {
        existing.description = safeDescription;
      }
      nextAliases.forEach((alias) => existing.aliasSet.add(alias));
      return;
    }
    const entry = {
      roleId: safeRoleId,
      displayName: safeDisplayName,
      insertTerm: safeDisplayName,
      description: safeDescription,
      aliasSet: new Set(nextAliases),
    };
    byRoleId.set(safeRoleId, entry);
    entries.push(entry);
  };

  const coordinatorRoleId = getCoordinatorRoleId();
  const mainAgentRoleId = getMainAgentRoleId();
  if (coordinatorRoleId) {
    upsertOption(coordinatorRoleId, "Coordinator");
  }
  if (mainAgentRoleId) {
    upsertOption(
      mainAgentRoleId,
      getRoleDisplayName(mainAgentRoleId, { fallback: mainAgentRoleId }),
    );
  }
  getNormalModeRoles().forEach((role) => {
    upsertOption(role?.role_id, role?.name || role?.role_id, {
      aliases: [role?.role_id],
      description: role?.description,
    });
  });

  return entries.map((entry) => ({
    roleId: entry.roleId,
    displayName: entry.displayName,
    insertTerm: entry.insertTerm,
    description: entry.description,
    aliases: Array.from(entry.aliasSet),
  }));
}

function findPromptMentionOptions(query) {
  const safeQuery = String(query || "")
    .trim()
    .toLowerCase();
  return listMentionableRoleOptions()
    .map((option, index) => ({
      option,
      index,
      score: getPromptMentionOptionScore(option, safeQuery),
    }))
    .filter((item) => item.score < Number.POSITIVE_INFINITY)
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .map((item) => item.option);
}

function listPromptCommandOptions() {
  if (
    String(state.currentWorkspaceId || "").trim() !== promptCommandWorkspaceId
  ) {
    return [];
  }
  return promptCommandOptions.map((command) => {
    const name = String(command?.name || "").trim();
    const aliases = Array.isArray(command?.aliases)
      ? command.aliases.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const preferredName =
      aliases.find((item) => item.includes(":")) || name;
    const terms = [name, ...aliases].filter(Boolean);
    return {
      kind: "command",
      commandName: name,
      displayName: preferredName || name,
      insertTerm: name,
      description: String(command?.description || "").trim(),
      argumentHint: String(command?.argument_hint || "").trim(),
      source: normalizePromptCommandSource(command),
      aliases: terms,
    };
  }).filter((option) => option.commandName && option.displayName);
}

function normalizePromptCommandSource(command) {
  const explicitSource = String(command?.source || command?.discovery_source || "")
    .trim()
    .toLowerCase();
  if (explicitSource.includes("mcp")) {
    return "mcp";
  }
  if (explicitSource.includes("opencode")) {
    return "opencode";
  }
  if (explicitSource.includes("codex")) {
    return "codex";
  }
  if (explicitSource.includes("claude")) {
    return "claude";
  }
  if (explicitSource.includes("relay")) {
    return "relay";
  }
  const scope = String(command?.scope || "").trim().toLowerCase();
  if (scope === "app") {
    return "builtin";
  }
  return "custom";
}

function normalizePromptSkillOptions(skills) {
  return (Array.isArray(skills) ? skills : [])
    .map((skill) => {
      const name = String(skill?.name || skill?.ref || "").trim();
      const ref = String(skill?.ref || name).trim();
      if (!name) {
        return null;
      }
      const insertTerm = getSlashSafeSkillInsertTerm(name, ref);
      return {
        kind: "skill",
        skillName: name,
        displayName: name,
        insertTerm,
        description: String(skill?.description || "").trim(),
        source: String(skill?.source || "").trim(),
        aliases: Array.from(new Set([name, ref, insertTerm].filter(Boolean))),
      };
    })
    .filter(Boolean);
}

function getSlashSafeSkillInsertTerm(name, ref) {
  const safeRef = String(ref || "").trim();
  if (safeRef && !/\s/.test(safeRef)) {
    return safeRef;
  }
  const safeName = String(name || "").trim();
  if (safeName && !/\s/.test(safeName)) {
    return safeName;
  }
  return safeName.replace(/\s+/g, "-").toLowerCase();
}

function findPromptActionOptions(query) {
  const safeQuery = String(query || "")
    .trim()
    .toLowerCase();
  const commandOptions = listPromptCommandOptions();
  const commandTerms = new Set(
    commandOptions.flatMap((option) => option.aliases || [])
      .map((term) => String(term || "").trim().toLowerCase())
      .filter(Boolean),
  );
  const skillOptions = promptSkillOptions.filter((option) =>
    !option.aliases.some((alias) =>
      commandTerms.has(String(alias || "").trim().toLowerCase())
    )
  );
  return [...commandOptions, ...skillOptions]
    .map((option, index) => ({
      option,
      index,
      score: getPromptMentionOptionScore(option, safeQuery),
    }))
    .filter((item) => item.score < Number.POSITIVE_INFINITY)
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .slice(0, 10)
    .map((item) => item.option);
}

function findPromptResourceOptions(query) {
  const roleOptions = findPromptMentionOptions(query).map((option) => ({
    ...option,
    kind: "agent",
  }));
  const fileOptions = promptResourceOptions
    .map((option, index) => ({
      option,
      index,
      score: getPromptMentionOptionScore(option, String(query || "").trim().toLowerCase()),
    }))
    .filter((item) => item.score < Number.POSITIVE_INFINITY)
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .slice(0, 20)
    .map((item) => item.option);
  return [...roleOptions, ...fileOptions].slice(0, 20);
}

function normalizePromptResourceResponse(response) {
  return (Array.isArray(response?.results) ? response.results : [])
    .map((item) => {
      const path = String(item?.path || "").trim();
      const name = String(item?.name || path).trim();
      const kind = String(item?.kind || "").trim() === "directory"
        ? "directory"
        : "file";
      if (!path || !name) {
        return null;
      }
      return {
        kind,
        displayName: name,
        insertTerm: kind === "directory" && !path.endsWith("/") ? `${path}/` : path,
        description: path,
        path,
        mountName: String(item?.mount_name || "").trim(),
        aliases: [name, path],
      };
    })
    .filter(Boolean);
}

function getPromptMentionOptionScore(option, query) {
  if (!query) {
    return 0;
  }
  let best = Number.POSITIVE_INFINITY;
  option.aliases.forEach((alias) => {
    const normalizedAlias = String(alias || "")
      .trim()
      .toLowerCase();
    if (!normalizedAlias) {
      return;
    }
    if (normalizedAlias === query) {
      best = Math.min(best, 0);
      return;
    }
    if (normalizedAlias.startsWith(query)) {
      best = Math.min(best, 1);
      return;
    }
    if (normalizedAlias.includes(query)) {
      best = Math.min(best, 2);
    }
  });
  return best;
}

function getPromptCommandContext() {
  const input = els.promptInput;
  if (!input) {
    return null;
  }
  const source = String(input.value || "");
  const selectionStart = Number.isFinite(input.selectionStart)
    ? Number(input.selectionStart)
    : source.length;
  const beforeCursor = source.slice(0, selectionStart);
  const commandTokenMatch = beforeCursor.match(/(^|\s)\/([^\s]*)$/);
  if (!commandTokenMatch) {
    return null;
  }
  const separator = commandTokenMatch[1] || "";
  const tokenText = commandTokenMatch[2] || "";
  const start = beforeCursor.length - tokenText.length - 1;
  const afterCursor = source.slice(selectionStart);
  const tokenTail = afterCursor.match(/^[^\s]*/)?.[0] || "";
  return {
    start,
    end: selectionStart + tokenTail.length,
    trigger: "/",
    query: tokenText.trim(),
    separator,
  };
}

function getPromptMentionContext() {
  const input = els.promptInput;
  if (!input) {
    return null;
  }
  const source = String(input.value || "");
  const selectionStart = Number.isFinite(input.selectionStart)
    ? Number(input.selectionStart)
    : source.length;
  const beforeCursor = source.slice(0, selectionStart);
  const mentionTokenMatch = beforeCursor.match(/(^|\s)([@＠])([^\s]*)$/);
  if (!mentionTokenMatch) {
    return null;
  }
  const trigger = mentionTokenMatch[2];
  const tokenText = mentionTokenMatch[3] || "";
  const start = beforeCursor.length - tokenText.length - 1;
  const afterCursor = source.slice(selectionStart);
  const tokenTail = afterCursor.match(/^[^\s]*/)?.[0] || "";
  return {
    start,
    end: selectionStart + tokenTail.length,
    trigger,
    query: tokenText.trim(),
  };
}

function renderPromptMentionAutocomplete() {
  const menu = els.promptMentionMenu;
  if (!menu) {
    return;
  }
  if (!promptMentionKind) {
    hidePromptMentionMenu(menu);
    return;
  }
  const shouldShowCommandStatus =
    promptMentionKind === "action" &&
    promptCommandAutocompleteStatus !== PROMPT_COMMAND_AUTOCOMPLETE_STATUS.IDLE;
  const shouldShowResourceOptions =
    promptMentionKind === "resource" &&
    promptMentionOptions.length > 0 &&
    activePromptMentionIndex >= 0;
  if (promptMentionKind === "resource" && !shouldShowResourceOptions) {
    hidePromptMentionMenu(menu);
    return;
  }
  if (
    (promptMentionOptions.length === 0 || activePromptMentionIndex < 0) &&
    !shouldShowCommandStatus
  ) {
    hidePromptMentionMenu(menu);
    return;
  }
  menu.hidden = false;
  menu.style.display = "flex";
  if (promptMentionKind === "action") {
    renderPromptCommandAutocomplete(menu);
    applyPromptMentionMenuPlacement(menu);
    return;
  }
  renderPromptResourceAutocomplete(menu);
  applyPromptMentionMenuPlacement(menu);
}

function renderPromptResourceAutocomplete(menu) {
  menu.innerHTML = `
        <div class="prompt-mention-menu-header">
            <span class="prompt-mention-menu-title">@ 引用</span>
            <span class="prompt-mention-menu-summary">${escapeHtml(
              `${promptMentionOptions.length}`,
            )}</span>
        </div>
        <div class="prompt-mention-menu-list">
            ${renderPromptOptionSections(promptMentionOptions)}
        </div>
    `;
  syncPromptMentionActiveOptionIntoView(menu);
}

function hidePromptMentionMenu(menu) {
  menu.innerHTML = "";
  menu.hidden = true;
  menu.style.display = "none";
}

function renderPromptCommandAutocomplete(menu) {
  const hasOptions = promptMentionOptions.length > 0;
  menu.innerHTML = `
        <div class="prompt-mention-menu-header">
            <span class="prompt-mention-menu-title">/ 命令</span>
            <span class="prompt-mention-menu-summary">${escapeHtml(
              hasOptions ? `${promptMentionOptions.length}` : "",
            )}</span>
        </div>
        <div class="prompt-mention-menu-list">
            ${
              hasOptions
                ? renderPromptOptionSections(promptMentionOptions)
                : renderPromptCommandStatus()
            }
        </div>
    `;
  syncPromptMentionActiveOptionIntoView(menu);
}

function renderPromptOptionSections(options) {
  const sections = [];
  const pushSection = (title, items) => {
    if (items.length === 0) {
      return;
    }
    sections.push(`
        <section class="prompt-mention-section">
            <div class="prompt-mention-section-title">${escapeHtml(title)}</div>
            <div class="prompt-mention-section-list">
                ${items.map((item) => renderPromptOption(item.option, item.index)).join("")}
            </div>
        </section>
    `);
  };
  pushSection(
    "Agent",
    options
      .map((option, index) => ({ option, index }))
      .filter((item) => item.option.kind === "agent"),
  );
  pushSection(
    "Files",
    options
      .map((option, index) => ({ option, index }))
      .filter((item) => item.option.kind === "file" || item.option.kind === "directory"),
  );
  pushSection(
    "Commands",
    options
      .map((option, index) => ({ option, index }))
      .filter((item) => item.option.kind === "command"),
  );
  pushSection(
    "Skills",
    options
      .map((option, index) => ({ option, index }))
      .filter((item) => item.option.kind === "skill"),
  );
  return sections.join("");
}

function renderPromptOption(option, index) {
  const isActive = index === activePromptMentionIndex;
  const optionType = getPromptOptionType(option);
  const hintText = getPromptOptionHint(option);
  const descriptionText = getPromptOptionDescription(option);
  return `
            <button
            type="button"
            class="prompt-mention-item prompt-mention-item-${escapeHtml(optionType)}${isActive ? " active" : ""}"
            data-index="${index}"
            data-kind="${escapeHtml(option.kind || "")}"
            data-source="${escapeHtml(option.source || "")}"
            role="option"
            aria-selected="${isActive ? "true" : "false"}"
        >
            <span class="prompt-mention-item-accent prompt-mention-type-${escapeHtml(optionType)}" aria-hidden="true">${escapeHtml(
              getPromptOptionIcon(option),
            )}</span>
            <span class="prompt-mention-item-main">
                <span class="prompt-mention-item-row">
                    <span class="prompt-mention-item-name">${renderPromptOptionName(option)}</span>
                    ${descriptionText ? `<span class="prompt-mention-item-description">${escapeHtml(descriptionText)}</span>` : ""}
                    ${hintText ? `<span class="prompt-mention-item-id">${escapeHtml(hintText)}</span>` : ""}
                </span>
            </span>
        </button>
    `;
}

function renderPromptCommandStatus() {
  const status = resolvePromptCommandStatusCopy();
  return `
        <div class="prompt-mention-empty" role="status">
            <span class="prompt-mention-empty-title">${escapeHtml(status.title)}</span>
            <span class="prompt-mention-empty-copy">${escapeHtml(status.copy)}</span>
        </div>
    `;
}

function renderPromptResourceStatus() {
  const status = resolvePromptResourceStatusCopy();
  return `
        <div class="prompt-mention-empty" role="status">
            <span class="prompt-mention-empty-title">${escapeHtml(status.title)}</span>
            <span class="prompt-mention-empty-copy">${escapeHtml(status.copy)}</span>
        </div>
    `;
}

function shouldRenderPromptResourceStatus() {
  const workspaceId = String(state.currentWorkspaceId || "").trim();
  if (!workspaceId) {
    return true;
  }
  const cacheKey = `${workspaceId}\n${String(promptMentionQuery || "").trim()}`;
  return (
    promptResourceLoadingKey === cacheKey ||
    promptResourceLoadErrorKey === cacheKey ||
    promptMentionOptions.length === 0
  );
}

function resolvePromptResourceStatusCopy() {
  const workspaceId = String(state.currentWorkspaceId || "").trim();
  const cacheKey = `${workspaceId}\n${String(promptMentionQuery || "").trim()}`;
  if (!workspaceId) {
    return {
      title: "没有可搜索的 workspace",
      copy: "先选择或创建 workspace 后再引用文件。",
    };
  }
  if (promptResourceLoadingKey === cacheKey) {
    return {
      title: "正在搜索",
      copy: "查找当前 workspace 的文件和目录。",
    };
  }
  if (promptResourceLoadErrorKey === cacheKey) {
    return {
      title: "搜索失败",
      copy: promptResourceLoadErrorMessage || "无法搜索当前 workspace。",
    };
  }
  return {
    title: "没有匹配项",
    copy: "继续输入以搜索 agent、文件或目录。",
  };
}

function resolvePromptCommandStatusCopy() {
  if (promptCommandAutocompleteStatus === PROMPT_COMMAND_AUTOCOMPLETE_STATUS.LOADING) {
    return {
      title: t("composer.command_loading"),
      copy: t("composer.command_loading_copy"),
    };
  }
  if (promptCommandAutocompleteStatus === PROMPT_COMMAND_AUTOCOMPLETE_STATUS.NO_WORKSPACE) {
    return {
      title: t("composer.command_no_workspace"),
      copy: t("composer.command_no_workspace_copy"),
    };
  }
  if (promptCommandAutocompleteStatus === PROMPT_COMMAND_AUTOCOMPLETE_STATUS.ERROR) {
    return {
      title: t("composer.command_load_failed"),
      copy: promptCommandLoadErrorMessage || t("composer.command_load_failed_copy"),
    };
  }
  if (promptCommandAutocompleteStatus === PROMPT_COMMAND_AUTOCOMPLETE_STATUS.NO_MATCH) {
    return {
      title: t("composer.command_no_match"),
      copy: t("composer.command_no_match_copy"),
    };
  }
  return {
    title: t("composer.command_empty"),
    copy: t("composer.command_empty_copy"),
  };
}

function resolvePromptCommandAutocompleteStatus({
  workspaceId,
  query,
  optionCount,
}) {
  if (!workspaceId) {
    return PROMPT_COMMAND_AUTOCOMPLETE_STATUS.NO_WORKSPACE;
  }
  if (
    workspaceId === promptCommandLoadingWorkspaceId &&
    workspaceId !== promptCommandWorkspaceId
  ) {
    return PROMPT_COMMAND_AUTOCOMPLETE_STATUS.LOADING;
  }
  if (workspaceId === promptCommandLoadErrorWorkspaceId) {
    return PROMPT_COMMAND_AUTOCOMPLETE_STATUS.ERROR;
  }
  if (optionCount > 0) {
    return PROMPT_COMMAND_AUTOCOMPLETE_STATUS.READY;
  }
  if (String(query || "").trim() && listPromptCommandOptions().length > 0) {
    return PROMPT_COMMAND_AUTOCOMPLETE_STATUS.NO_MATCH;
  }
  return PROMPT_COMMAND_AUTOCOMPLETE_STATUS.EMPTY;
}

function movePromptMentionSelection(direction) {
  if (promptMentionOptions.length === 0) {
    dismissPromptMentionAutocomplete();
    return;
  }
  const maxIndex = promptMentionOptions.length - 1;
  if (activePromptMentionIndex < 0) {
    activePromptMentionIndex = 0;
  } else if (direction > 0) {
    activePromptMentionIndex =
      activePromptMentionIndex >= maxIndex ? 0 : activePromptMentionIndex + 1;
  } else {
    activePromptMentionIndex =
      activePromptMentionIndex <= 0 ? maxIndex : activePromptMentionIndex - 1;
  }
  previewPromptMentionOption(activePromptMentionIndex);
  renderPromptMentionAutocomplete();
}

function selectPromptMentionOption(index) {
  const option = promptMentionOptions[index];
  if (!option || !els.promptInput) {
    return false;
  }
  const keepMenuOpen =
    promptMentionKind === "resource" && option.kind === "directory";
  applyPromptMentionOptionToInput(option, { commit: true });
  if (keepMenuOpen) {
    setPromptMentionPreviewBaseline();
    void ensurePromptResourcesLoaded(promptMentionQuery);
    refreshPromptMentionAutocomplete();
    return true;
  }
  dismissPromptMentionAutocomplete();
  return true;
}

function previewPromptMentionOption(index) {
  const option = promptMentionOptions[index];
  if (!option || !els.promptInput) {
    return;
  }
  ensurePromptMentionPreviewSnapshot();
  applyPromptMentionOptionToInput(option, { commit: false });
}

function applyPromptMentionOptionToInput(option, { commit }) {
  const source = String(els.promptInput.value || "");
  const before = source.slice(0, promptMentionRange.start);
  const after = source.slice(promptMentionRange.end);
  const spacer = after.length === 0 || /^\s/.test(after) ? "" : " ";
  const appendTrailingSpace = commit &&
    !(promptMentionKind === "resource" && option.kind === "directory");
  const mentionTrigger = promptMentionKind === "action"
    ? "/"
    : getPromptMentionTrigger(source.slice(promptMentionRange.start))
      || promptMentionTrigger;
  const insertedMention = `${mentionTrigger}${option.insertTerm}`;
  const trailingText = after ? spacer : appendTrailingSpace ? " " : "";
  const nextValue = `${before}${insertedMention}${trailingText}${after}`;

  els.promptInput.value = nextValue;
  const caretPosition =
    before.length + insertedMention.length + trailingText.length;
  if ("selectionStart" in els.promptInput) {
    els.promptInput.selectionStart = caretPosition;
  }
  if ("selectionEnd" in els.promptInput) {
    els.promptInput.selectionEnd = caretPosition;
  }
  promptMentionRange = {
    start: before.length,
    end: before.length + insertedMention.length,
  };
  promptMentionQuery = String(option.insertTerm || "");
  promptMentionPreviewValue = nextValue;
  els.promptInput.style.height = "auto";
  if (Number.isFinite(els.promptInput.scrollHeight)) {
    els.promptInput.style.height = `${els.promptInput.scrollHeight}px`;
  }
  els.promptInput.focus?.();
  renderPromptTokenPreview();
}

function dismissPromptMentionAutocomplete() {
  clearPromptResourceSearchTimer();
  promptMentionOptions = [];
  activePromptMentionIndex = -1;
  promptMentionQuery = "";
  promptMentionTrigger = DEFAULT_PROMPT_MENTION_TRIGGER;
  promptMentionKind = null;
  promptMentionSessionKey = "";
  promptMentionPlacementSide = null;
  clearPromptMentionPreviewSnapshot();
  promptCommandAutocompleteStatus = PROMPT_COMMAND_AUTOCOMPLETE_STATUS.IDLE;
  promptMentionRange = {
    start: 0,
    end: 0,
  };
  renderPromptMentionAutocomplete();
}

function ensurePromptMentionPreviewSnapshot() {
  if (promptMentionPreviewSnapshot || !els.promptInput) {
    return;
  }
  promptMentionPreviewSnapshot = {
    value: String(els.promptInput.value || ""),
    selectionStart: Number.isFinite(els.promptInput.selectionStart)
      ? Number(els.promptInput.selectionStart)
      : String(els.promptInput.value || "").length,
    selectionEnd: Number.isFinite(els.promptInput.selectionEnd)
      ? Number(els.promptInput.selectionEnd)
      : String(els.promptInput.value || "").length,
  };
}

function setPromptMentionPreviewBaseline() {
  clearPromptMentionPreviewSnapshot();
  ensurePromptMentionPreviewSnapshot();
  promptMentionPreviewValue = String(els.promptInput?.value || "");
}

function restorePromptMentionPreviewSnapshot() {
  if (!promptMentionPreviewSnapshot || !els.promptInput) {
    return;
  }
  els.promptInput.value = promptMentionPreviewSnapshot.value;
  els.promptInput.selectionStart = promptMentionPreviewSnapshot.selectionStart;
  els.promptInput.selectionEnd = promptMentionPreviewSnapshot.selectionEnd;
  els.promptInput.style.height = "auto";
  if (Number.isFinite(els.promptInput.scrollHeight)) {
    els.promptInput.style.height = `${els.promptInput.scrollHeight}px`;
  }
}

function clearPromptMentionPreviewSnapshot() {
  promptMentionPreviewSnapshot = null;
  promptMentionPreviewValue = "";
}

function acceptPromptMentionPreviewIfUserEdited() {
  if (
    !promptMentionPreviewSnapshot ||
    !promptMentionPreviewValue ||
    !els.promptInput
  ) {
    return;
  }
  if (String(els.promptInput.value || "") === promptMentionPreviewValue) {
    return;
  }
  clearPromptMentionPreviewSnapshot();
}

async function ensurePromptCommandsLoaded() {
  const workspaceId = String(state.currentWorkspaceId || "").trim();
  if (!workspaceId || workspaceId === promptCommandWorkspaceId) {
    return;
  }
  if (workspaceId === promptCommandLoadingWorkspaceId) {
    return;
  }
  promptCommandLoadingWorkspaceId = workspaceId;
  const requestToken = ++promptCommandRequestSequence;
  promptCommandActiveRequestToken = requestToken;
  try {
    const commandResponse = await fetchCommands(workspaceId);
    if (
      requestToken !== promptCommandActiveRequestToken ||
      workspaceId !== String(state.currentWorkspaceId || "").trim()
    ) {
      return;
    }
    promptCommandOptions = normalizePromptCommandResponse(commandResponse);
    promptCommandWorkspaceId = workspaceId;
    promptCommandLoadErrorWorkspaceId = "";
    promptCommandLoadErrorMessage = "";
    refreshPromptMentionAutocomplete();
  } catch (error) {
    if (
      requestToken !== promptCommandActiveRequestToken ||
      workspaceId !== String(state.currentWorkspaceId || "").trim()
    ) {
      return;
    }
    promptCommandOptions = [];
    promptCommandWorkspaceId = "";
    promptCommandLoadErrorWorkspaceId = workspaceId;
    promptCommandLoadErrorMessage = error.message || t("composer.command_load_failed_copy");
    sysLog(error.message || "Failed to load commands", "log-error");
    if (promptCommandLoadingWorkspaceId === workspaceId) {
      promptCommandLoadingWorkspaceId = "";
    }
    promptCommandAutocompleteStatus = PROMPT_COMMAND_AUTOCOMPLETE_STATUS.ERROR;
    renderPromptMentionAutocomplete();
  } finally {
    if (
      requestToken === promptCommandActiveRequestToken &&
      promptCommandLoadingWorkspaceId === workspaceId
    ) {
      promptCommandLoadingWorkspaceId = "";
    }
  }
}

export function invalidatePromptCommandsCache() {
  promptCommandOptions = [];
  promptCommandWorkspaceId = "";
  promptCommandLoadingWorkspaceId = "";
  promptCommandLoadErrorWorkspaceId = "";
  promptCommandLoadErrorMessage = "";
  promptCommandAutocompleteStatus = PROMPT_COMMAND_AUTOCOMPLETE_STATUS.IDLE;
  promptCommandActiveRequestToken = ++promptCommandRequestSequence;
  if (promptMentionKind === "action") {
    refreshPromptMentionAutocomplete();
  }
}

function invalidatePromptResourceCache() {
  clearPromptResourceSearchTimer();
  promptResourceOptions = [];
  promptResourceWorkspaceId = "";
  promptResourceQuery = "";
  promptResourceLoadingKey = "";
  promptResourceLoadErrorKey = "";
  promptResourceLoadErrorMessage = "";
  promptResourceActiveRequestToken = ++promptResourceRequestSequence;
  promptResourceCachedWorkspaceId = "";
  promptResourceCachedOptions = [];
  promptResourceQueryCache.clear();
}

function getPromptOptionKey(option) {
  if (!option) {
    return "";
  }
  if (option.kind === "agent") {
    return `agent:${option.roleId || ""}`;
  }
  if (option.kind === "skill") {
    return `skill:${option.skillName || ""}`;
  }
  if (option.kind === "command") {
    return `command:${option.commandName || ""}`;
  }
  return `${option.kind || "resource"}:${option.path || option.displayName || ""}`;
}

function getPromptOptionIcon(option) {
  if (option.kind === "agent") {
    return "agent";
  }
  if (option.kind === "directory") {
    return "dir";
  }
  if (option.kind === "file") {
    return "file";
  }
  if (option.kind === "skill") {
    return "skill";
  }
  const source = String(option.source || "custom").trim().toLowerCase();
  if (source === "builtin") {
    return "built";
  }
  if (source === "mcp") {
    return "mcp";
  }
  return "cmd";
}

function getPromptOptionHint(option) {
  if (option.kind === "agent") {
    return option.roleId ? `@${option.roleId}` : "";
  }
  if (option.kind === "skill") {
    return "";
  }
  if (option.kind === "directory" || option.kind === "file") {
    return "";
  }
  return option.argumentHint || "";
}

function getPromptOptionType(option) {
  if (option.kind === "command") {
    const source = String(option.source || "custom").trim().toLowerCase();
    if (source === "builtin") {
      return "builtin";
    }
    if (source === "mcp") {
      return "mcp";
    }
    return "command";
  }
  return String(option.kind || "command").trim().toLowerCase();
}

function getPromptOptionDescription(option) {
  if (option.kind === "file" || option.kind === "directory") {
    return option.path || "";
  }
  return option.description || "";
}

function renderPromptOptionName(option) {
  if (option.kind === "file" || option.kind === "directory") {
    const path = String(option.path || option.displayName || "");
    const normalizedPath = option.kind === "directory" && !path.endsWith("/")
      ? `${path}/`
      : path;
    const lastSlash = normalizedPath.lastIndexOf("/", normalizedPath.endsWith("/") ? normalizedPath.length - 2 : normalizedPath.length);
    if (lastSlash >= 0) {
      const directory = normalizedPath.slice(0, lastSlash + 1);
      const name = normalizedPath.slice(lastSlash + 1);
      return `<span class="prompt-mention-path-dir">${escapeHtml(directory)}</span><span class="prompt-mention-path-name">${highlightPromptMentionText(name, promptMentionQuery)}</span>`;
    }
  }
  const prefix = option.kind === "command" || option.kind === "skill" ? "/" : option.kind === "agent" ? "@" : "";
  return `${escapeHtml(prefix)}${highlightPromptMentionText(option.displayName, promptMentionQuery)}`;
}

async function ensurePromptResourcesLoaded(query) {
  const workspaceId = String(state.currentWorkspaceId || "").trim();
  const safeQuery = String(query || "").trim();
  const cacheKey = `${workspaceId}\n${safeQuery}`;
  if (!workspaceId) {
    clearPromptResourceSearchTimer();
    promptResourceOptions = [];
    promptResourceWorkspaceId = "";
    promptResourceQuery = "";
    promptResourceLoadErrorKey = "";
    promptResourceLoadErrorMessage = "";
    return;
  }
  const cachedOptions = getCachedPromptResourceOptions(workspaceId, safeQuery);
  if (cachedOptions) {
    promptResourceOptions = cachedOptions;
    promptResourceWorkspaceId = workspaceId;
    promptResourceQuery = safeQuery;
    promptResourceLoadErrorKey = "";
    promptResourceLoadErrorMessage = "";
    return;
  }
  if (
    workspaceId === promptResourceWorkspaceId &&
    safeQuery === promptResourceQuery
  ) {
    return;
  }
  if (cacheKey === promptResourceLoadingKey) {
    return;
  }
  promptResourceLoadingKey = cacheKey;
  const requestToken = ++promptResourceRequestSequence;
  promptResourceActiveRequestToken = requestToken;
  try {
    const resourceResponse = await searchWorkspacePaths(workspaceId, safeQuery, 500);
    if (
      requestToken !== promptResourceActiveRequestToken ||
      workspaceId !== String(state.currentWorkspaceId || "").trim()
    ) {
      return;
    }
    promptResourceOptions = normalizePromptResourceResponse(resourceResponse);
    cachePromptResourceOptions(workspaceId, safeQuery, promptResourceOptions);
    promptResourceWorkspaceId = workspaceId;
    promptResourceQuery = safeQuery;
    promptResourceLoadErrorKey = "";
    promptResourceLoadErrorMessage = "";
    refreshPromptMentionAutocomplete();
  } catch (error) {
    if (
      requestToken !== promptResourceActiveRequestToken ||
      workspaceId !== String(state.currentWorkspaceId || "").trim()
    ) {
      return;
    }
    promptResourceOptions = [];
    promptResourceWorkspaceId = "";
    promptResourceQuery = "";
    promptResourceLoadErrorKey = cacheKey;
    promptResourceLoadErrorMessage = error.message || "Failed to search workspace files.";
    sysLog(promptResourceLoadErrorMessage, "log-error");
    renderPromptMentionAutocomplete();
  } finally {
    if (
      requestToken === promptResourceActiveRequestToken &&
      promptResourceLoadingKey === cacheKey
    ) {
      promptResourceLoadingKey = "";
    }
  }
}

function schedulePromptResourcesLoaded(query) {
  clearPromptResourceSearchTimer();
  promptResourceDebounceTimer = globalThis.setTimeout?.(() => {
    promptResourceDebounceTimer = null;
    void ensurePromptResourcesLoaded(query);
  }, PROMPT_RESOURCE_SEARCH_DEBOUNCE_MS) || null;
}

function clearPromptResourceSearchTimer() {
  if (promptResourceDebounceTimer == null) {
    return;
  }
  globalThis.clearTimeout?.(promptResourceDebounceTimer);
  promptResourceDebounceTimer = null;
}

function normalizePromptCommandResponse(response) {
  if (Array.isArray(response)) {
    return response;
  }
  if (Array.isArray(response?.commands)) {
    return response.commands;
  }
  return [];
}

function getPromptResourceCacheKey(workspaceId, query) {
  return `${String(workspaceId || "").trim()}\n${String(query || "").trim()}`;
}

function getCachedPromptResourceOptions(workspaceId, query) {
  const cacheKey = getPromptResourceCacheKey(workspaceId, query);
  const cached = promptResourceQueryCache.get(cacheKey);
  return Array.isArray(cached) ? cached : null;
}

function getLocalPromptResourceOptions(workspaceId, query) {
  const safeWorkspaceId = String(workspaceId || "").trim();
  if (!safeWorkspaceId) {
    return [];
  }
  const cachedOptions = getCachedPromptResourceOptions(safeWorkspaceId, query);
  if (cachedOptions) {
    return cachedOptions;
  }
  if (
    promptResourceCachedWorkspaceId === safeWorkspaceId &&
    promptResourceCachedOptions.length > 0
  ) {
    return promptResourceCachedOptions;
  }
  if (
    promptResourceWorkspaceId === safeWorkspaceId &&
    Array.isArray(promptResourceOptions)
  ) {
    return promptResourceOptions;
  }
  return [];
}

function cachePromptResourceOptions(workspaceId, query, options) {
  const safeWorkspaceId = String(workspaceId || "").trim();
  if (!safeWorkspaceId) {
    return;
  }
  const cacheKey = getPromptResourceCacheKey(safeWorkspaceId, query);
  promptResourceQueryCache.set(cacheKey, Array.isArray(options) ? options : []);
  if (safeWorkspaceId !== promptResourceCachedWorkspaceId) {
    promptResourceCachedWorkspaceId = safeWorkspaceId;
    promptResourceCachedOptions = [];
  }
  promptResourceCachedOptions = mergePromptResourceOptions(
    promptResourceCachedOptions,
    options,
  );
  if (promptResourceQueryCache.size > 160) {
    const firstKey = promptResourceQueryCache.keys().next().value;
    if (firstKey) {
      promptResourceQueryCache.delete(firstKey);
    }
  }
}

function mergePromptResourceOptions(existingOptions, nextOptions) {
  const byKey = new Map();
  [...(existingOptions || []), ...(nextOptions || [])].forEach((option) => {
    const key = getPromptOptionKey(option);
    if (key) {
      byKey.set(key, option);
    }
  });
  return Array.from(byKey.values());
}

async function resolvePromptCommandText(text) {
  const promptText = String(text || "").trim();
  const invocation = extractPromptActionInvocation(promptText);
  if (!invocation) {
    return { text: promptText, skills: [] };
  }
  const workspaceId = String(state.currentWorkspaceId || "").trim();
  if (workspaceId) {
    try {
      const result = await resolveCommandPrompt({
        workspace_id: workspaceId,
        raw_text: `/${invocation.name}${invocation.args ? ` ${invocation.args}` : ""}`,
        mode: state.currentSessionMode || "normal",
      });
      if (result?.matched) {
        const expandedPrompt = String(result.expanded_prompt || "").trim();
        return {
          text: combinePromptActionText(invocation.prefix, expandedPrompt || promptText),
          skills: [],
        };
      }
    } catch (error) {
      const message = error.message || "Failed to resolve command.";
      setPromptComposerStatus(message, { tone: "danger" });
      showToast({
        title: "Command blocked",
        message,
        tone: "warning",
      });
      sysLog(message, "log-error");
      return null;
    }
  }
  const skillMatch = matchPromptSkillInvocation(invocation.name);
  if (skillMatch) {
    const skillPromptText = invocation.args || `Use the ${skillMatch.skillName} skill.`;
    return {
      text: combinePromptActionText(invocation.prefix, skillPromptText),
      skills: [skillMatch.skillName],
    };
  }
  if (!workspaceId) {
    const message = "Cannot resolve command without an active workspace.";
    setPromptComposerStatus(message, { tone: "danger" });
    sysLog(message, "log-error");
    return null;
  }
  return { text: promptText, skills: [] };
}

function extractPromptActionInvocation(promptText) {
  const source = String(promptText || "").trim();
  const match = source.match(/^(\/(\S+)|([@＠]\S+(?:\s+[A-Z][A-Za-z0-9_-]*)?)\s+\/(\S+))/);
  if (!match) {
    return null;
  }
  const prefix = String(match[3] || "").trim();
  const name = String(match[2] || match[4] || "").trim();
  if (!name) {
    return null;
  }
  const argsStart = String(match[1] || "").length;
  return {
    prefix,
    name,
    args: source.slice(argsStart).trim(),
  };
}

function combinePromptActionText(prefix, resolvedText) {
  const safePrefix = String(prefix || "").trim();
  const safeText = String(resolvedText || "").trim();
  if (!safePrefix) {
    return safeText;
  }
  if (!safeText) {
    return safePrefix;
  }
  return `${safePrefix}\n\n${safeText}`;
}

function matchPromptSkillInvocation(name) {
  const token = String(name || "").trim().toLowerCase();
  const skill = promptSkillOptions.find((option) =>
    option.aliases.some((alias) => String(alias || "").trim().toLowerCase() === token)
  );
  if (!skill) {
    return null;
  }
  return {
    skillName: skill.skillName,
  };
}

function isPromptMentionAutocompleteOpen() {
  return (
    !!els.promptMentionMenu &&
    els.promptMentionMenu.hidden !== true &&
    (
      promptMentionOptions.length > 0 ||
      (
        !!promptMentionKind &&
        promptMentionKind === "action" &&
        promptCommandAutocompleteStatus !== PROMPT_COMMAND_AUTOCOMPLETE_STATUS.IDLE
      ) ||
      (
        !!promptMentionKind &&
        promptMentionKind === "resource" &&
        promptMentionOptions.length > 0
      )
    )
  );
}

function preventPromptMentionDefault(event) {
  event?.preventDefault?.();
  event?.stopImmediatePropagation?.();
  event?.stopPropagation?.();
}

function findPromptMentionOptionElement(target) {
  let node = target;
  while (node) {
    if (node?.dataset?.index != null) {
      return node;
    }
    node = node.parentElement || null;
  }
  return null;
}

function syncPromptMentionActiveOptionIntoView(menu) {
  if (!menu || typeof menu.querySelector !== "function") {
    return;
  }
  const activeOption = menu.querySelector(".prompt-mention-item.active");
  const list = menu.querySelector(".prompt-mention-menu-list");
  if (
    !activeOption ||
    !list ||
    !Number.isFinite(activeOption.offsetTop) ||
    !Number.isFinite(activeOption.offsetHeight) ||
    !Number.isFinite(list.scrollTop) ||
    !Number.isFinite(list.clientHeight)
  ) {
    return;
  }
  const optionTop = activeOption.offsetTop;
  const optionBottom = optionTop + activeOption.offsetHeight;
  const visibleTop = list.scrollTop;
  const visibleBottom = visibleTop + list.clientHeight;
  if (optionTop < visibleTop) {
    list.scrollTop = optionTop;
    return;
  }
  if (optionBottom > visibleBottom) {
    list.scrollTop = optionBottom - list.clientHeight;
  }
}

function applyPromptMentionMenuPlacement(menu) {
  const input = els.promptInput;
  if (
    !menu ||
    !input ||
    typeof input.getBoundingClientRect !== "function"
  ) {
    return;
  }
  const viewportHeight = Number(
    globalThis.window?.innerHeight ||
      globalThis.document?.documentElement?.clientHeight ||
      0,
  );
  const viewportWidth = Number(
    globalThis.window?.innerWidth ||
      globalThis.document?.documentElement?.clientWidth ||
      0,
  );
  if (!viewportHeight || !viewportWidth) {
    return;
  }
  const inputRect = input.getBoundingClientRect();
  const anchor = typeof input.closest === "function"
    ? input.closest(".input-wrapper") || input
    : input;
  const anchorRect = typeof anchor?.getBoundingClientRect === "function"
    ? anchor.getBoundingClientRect()
    : inputRect;
  const topBoundary = getPromptMentionTopBoundary();
  const preferredHeight = Math.min(
    Number(menu.scrollHeight || 0) || PROMPT_MENTION_MENU_MAX_HEIGHT,
    PROMPT_MENTION_MENU_MAX_HEIGHT,
  );
  const spaceAbove = Math.max(
    0,
    anchorRect.top - topBoundary - PROMPT_MENTION_MENU_GAP,
  );
  const spaceBelow = Math.max(
    0,
    viewportHeight - anchorRect.bottom - PROMPT_MENTION_MENU_SAFE_MARGIN - PROMPT_MENTION_MENU_GAP,
  );
  if (!promptMentionPlacementSide) {
    promptMentionPlacementSide =
      spaceAbove >= preferredHeight || spaceAbove >= spaceBelow
        ? "above"
        : "below";
  }
  const placeAbove = promptMentionPlacementSide === "above";
  const availableSpace = Math.floor(placeAbove ? spaceAbove : spaceBelow);
  const availableHeight = Math.max(
    0,
    Math.min(PROMPT_MENTION_MENU_MAX_HEIGHT, availableSpace),
  );
  const list = menu.querySelector?.(".prompt-mention-menu-list");

  menu.style.maxHeight = `${availableHeight}px`;
  if (list?.style) {
    list.style.maxHeight = `${Math.max(0, availableHeight - 46)}px`;
  }
  const anchorWidth = Math.max(240, Number(anchorRect.width || inputRect.width || 0));
  const menuWidth = Math.min(
    Math.max(240, anchorWidth - 24),
    608,
    Math.max(240, viewportWidth - PROMPT_MENTION_MENU_SAFE_MARGIN * 2),
  );
  const left = Math.min(
    Math.max(PROMPT_MENTION_MENU_SAFE_MARGIN, anchorRect.left + 12),
    Math.max(PROMPT_MENTION_MENU_SAFE_MARGIN, viewportWidth - menuWidth - PROMPT_MENTION_MENU_SAFE_MARGIN),
  );
  menu.style.position = "fixed";
  menu.style.left = `${Math.round(left)}px`;
  menu.style.width = `${Math.round(menuWidth)}px`;
  if (placeAbove) {
    menu.style.top = "auto";
    menu.style.bottom = `${Math.max(
      PROMPT_MENTION_MENU_SAFE_MARGIN,
      viewportHeight - anchorRect.top + PROMPT_MENTION_MENU_GAP,
    )}px`;
    return;
  }
  menu.style.bottom = "auto";
  menu.style.top = `${Math.max(
    PROMPT_MENTION_MENU_SAFE_MARGIN,
    anchorRect.bottom + PROMPT_MENTION_MENU_GAP,
  )}px`;
}

function getPromptMentionTopBoundary() {
  const topbar = globalThis.document?.querySelector?.(".topbar");
  const topbarRect = typeof topbar?.getBoundingClientRect === "function"
    ? topbar.getBoundingClientRect()
    : null;
  return Math.max(
    PROMPT_MENTION_MENU_SAFE_MARGIN,
    Number(topbarRect?.bottom || 0) + PROMPT_MENTION_MENU_SAFE_MARGIN,
  );
}

function containsNode(node, target) {
  if (!node || !target) {
    return false;
  }
  if (node === target) {
    return true;
  }
  return typeof node.contains === "function" ? node.contains(target) : false;
}

function getPromptMentionTrigger(value) {
  const firstChar = String(value || "").charAt(0);
  return firstChar === "@" || firstChar === "＠" ? firstChar : "";
}

function startsWithPromptMention(value) {
  return getPromptMentionTrigger(value) !== "";
}

function normalizePromptMentionSource(value) {
  return String(value || "").replace(/^＠/, "@");
}

function getPromptMentionMonogram(value) {
  const words = String(value || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) {
    return "@";
  }
  if (words.length === 1) {
    return words[0].slice(0, 2).toUpperCase();
  }
  return `${words[0].charAt(0)}${words[1].charAt(0)}`.toUpperCase();
}

function highlightPromptMentionText(text, query) {
  const safeText = String(text || "");
  const safeQuery = String(query || "").trim();
  if (!safeText) {
    return "";
  }
  if (!safeQuery) {
    return escapeHtml(safeText);
  }
  const normalizedText = safeText.toLowerCase();
  const normalizedQuery = safeQuery.toLowerCase();
  const matchIndex = normalizedText.indexOf(normalizedQuery);
  if (matchIndex < 0) {
    return escapeHtml(safeText);
  }
  const before = safeText.slice(0, matchIndex);
  const match = safeText.slice(matchIndex, matchIndex + safeQuery.length);
  const after = safeText.slice(matchIndex + safeQuery.length);
  return `${escapeHtml(before)}<mark class="prompt-mention-match">${escapeHtml(
    match,
  )}</mark>${escapeHtml(after)}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function readSavedYolo() {
  try {
    return localStorage.getItem(YOLO_STORAGE_KEY) !== "false";
  } catch (_error) {
    return true;
  }
}

function applyYolo(nextValue, { persist = true } = {}) {
  const safeYolo = nextValue === true;
  state.yolo = safeYolo;
  if (els.yoloToggle) {
    els.yoloToggle.checked = safeYolo;
  }
  if (!persist) return;
  try {
    localStorage.setItem(YOLO_STORAGE_KEY, safeYolo ? "true" : "false");
  } catch (_error) {
    return;
  }
}

function readSavedThinkingState() {
  try {
    const enabled = localStorage.getItem(THINKING_MODE_STORAGE_KEY) === "true";
    const effort = String(
      localStorage.getItem(THINKING_EFFORT_STORAGE_KEY) || "medium",
    );
    return {
      enabled,
      effort: normalizeThinkingEffort(effort),
    };
  } catch (_error) {
    return {
      enabled: false,
      effort: "medium",
    };
  }
}

function applyThinkingState(nextState, { persist = true } = {}) {
  const enabled = nextState?.enabled === true;
  const effort = normalizeThinkingEffort(nextState?.effort);
  state.thinking = {
    enabled,
    effort,
  };
  if (els.thinkingModeToggle) {
    els.thinkingModeToggle.checked = enabled;
  }
  if (els.thinkingEffortSelect) {
    els.thinkingEffortSelect.value = effort;
  }
  syncThinkingControls();
  if (!persist) return;
  try {
    localStorage.setItem(THINKING_MODE_STORAGE_KEY, enabled ? "true" : "false");
    localStorage.setItem(THINKING_EFFORT_STORAGE_KEY, effort);
  } catch (_error) {
    return;
  }
}

function normalizeThinkingEffort(value) {
  const safeValue = String(value || "")
    .trim()
    .toLowerCase();
  if (safeValue === "minimal" || safeValue === "low" || safeValue === "high") {
    return safeValue;
  }
  return "medium";
}

function syncThinkingControls() {
  const enabled = state.thinking?.enabled === true;
  if (els.thinkingEffortField) {
    els.thinkingEffortField.hidden = !enabled;
    els.thinkingEffortField.style.display = enabled ? "inline-flex" : "none";
  }
  if (els.thinkingEffortSelect) {
    els.thinkingEffortSelect.disabled = state.isGenerating || !enabled;
  }
}

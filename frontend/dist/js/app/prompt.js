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
  fetchOrchestrationConfig,
  updateSessionTopology,
} from "../core/api.js";
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

const YOLO_STORAGE_KEY = "agent_teams_yolo";
const THINKING_MODE_STORAGE_KEY = "agent_teams_thinking_enabled";
const THINKING_EFFORT_STORAGE_KEY = "agent_teams_thinking_effort";
const DEFAULT_PROMPT_MENTION_TRIGGER = "@";
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
  const canSwitch =
    !!state.currentSessionId &&
    state.currentSessionCanSwitchMode === true &&
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
  } catch (error) {
    setCoordinatorRoleId("");
    setMainAgentRoleId("");
    setCoordinatorRoleOption(null);
    setMainAgentRoleOption(null);
    setNormalModeRoles([]);
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
  if (!state.currentSessionId) {
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
  clearPromptComposerStatus();
  const inputParts = buildPromptInputParts(text);
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
  state.isGenerating = true;
  if (els.sendBtn) els.sendBtn.disabled = true;
  if (els.promptInput) els.promptInput.disabled = true;
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
      yolo: state.yolo,
      thinking: state.thinking,
      targetRoleId,
      onRunCreated: (run) => {
        state.currentSessionCanSwitchMode = false;
        refreshSessionTopologyControls();
        createLiveRound(run.run_id, promptPreviewText, inputParts);
      },
    },
  );
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
  }
}

export function handlePromptComposerInput() {
  renderPromptAttachments();
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
  }
}

async function handleTopologyModeChange(nextMode) {
  const normalizedMode =
    nextMode === "orchestration" ? "orchestration" : "normal";
  if (normalizedMode === state.currentSessionMode) {
    return;
  }
  if (!state.currentSessionId) {
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
  if (!state.currentSessionId) {
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

function refreshPromptMentionAutocomplete() {
  const mentionContext = getPromptMentionContext();
  if (!mentionContext) {
    dismissPromptMentionAutocomplete();
    return;
  }
  const nextOptions = findPromptMentionOptions(mentionContext.query);
  if (nextOptions.length === 0) {
    dismissPromptMentionAutocomplete();
    return;
  }

  const previousRoleId =
    promptMentionOptions[activePromptMentionIndex]?.roleId || "";
  promptMentionOptions = nextOptions;
  promptMentionQuery = mentionContext.query;
  promptMentionTrigger = mentionContext.trigger;
  promptMentionRange = {
    start: mentionContext.start,
    end: mentionContext.end,
  };
  const preservedIndex = promptMentionOptions.findIndex(
    (option) => option.roleId === previousRoleId,
  );
  activePromptMentionIndex = preservedIndex >= 0 ? preservedIndex : 0;
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
      error: t("composer.error.mention_not_found"),
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

function getPromptMentionContext() {
  const input = els.promptInput;
  if (!input) {
    return null;
  }
  const source = String(input.value || "");
  const selectionStart = Number.isFinite(input.selectionStart)
    ? Number(input.selectionStart)
    : source.length;
  const leadingWhitespace = source.match(/^\s*/)?.[0] || "";
  const mentionStart = leadingWhitespace.length;
  if (selectionStart < mentionStart) {
    return null;
  }
  const sourceAfterLeadingWhitespace = source.slice(mentionStart);
  const trigger = getPromptMentionTrigger(sourceAfterLeadingWhitespace);
  if (!trigger) {
    return null;
  }
  const mentionTokenMatch = sourceAfterLeadingWhitespace.match(/^[@＠](\S*)/);
  if (!mentionTokenMatch) {
    return null;
  }
  const mentionEnd = mentionStart + mentionTokenMatch[0].length;
  if (selectionStart > mentionEnd) {
    return null;
  }
  const prefix = source.slice(mentionStart, selectionStart);
  if (!startsWithPromptMention(prefix) || /\s/.test(prefix.slice(1))) {
    return null;
  }
  return {
    start: mentionStart,
    end: mentionEnd,
    trigger,
    query: prefix.slice(1).trim(),
  };
}

function renderPromptMentionAutocomplete() {
  const menu = els.promptMentionMenu;
  if (!menu) {
    return;
  }
  if (promptMentionOptions.length === 0 || activePromptMentionIndex < 0) {
    menu.innerHTML = "";
    menu.hidden = true;
    menu.style.display = "none";
    return;
  }
  menu.hidden = false;
  menu.style.display = "flex";
  menu.innerHTML = `
        <div class="prompt-mention-menu-header">
            <span class="prompt-mention-menu-title">@agent</span>
            <span class="prompt-mention-menu-summary">${escapeHtml(
              t("composer.mention_keys"),
            )}</span>
        </div>
        <div class="prompt-mention-menu-list">
            ${promptMentionOptions
              .map((option, index) => {
                const isActive = index === activePromptMentionIndex;
                const roleIdMeta =
                  option.displayName.toLowerCase() === option.roleId.toLowerCase()
                    ? ""
                    : `<span class="prompt-mention-item-id">@${highlightPromptMentionText(option.roleId, promptMentionQuery)}</span>`;
                const descriptionMeta = option.description
                  ? `<span class="prompt-mention-item-description">${escapeHtml(
                      option.description,
                    )}</span>`
                  : "";
                return `
                    <button
                        type="button"
                        class="prompt-mention-item${isActive ? " active" : ""}"
                        data-index="${index}"
                        data-role-id="${escapeHtml(option.roleId)}"
                        role="option"
                        aria-selected="${isActive ? "true" : "false"}"
                    >
                        <span class="prompt-mention-item-accent" aria-hidden="true">${escapeHtml(
                          getPromptMentionMonogram(option.displayName),
                        )}</span>
                        <span class="prompt-mention-item-main">
                            <span class="prompt-mention-item-row">
                                <span class="prompt-mention-item-name">${highlightPromptMentionText(
                                  option.displayName,
                                  promptMentionQuery,
                                )}</span>
                                <span class="prompt-mention-item-enter" aria-hidden="true">${escapeHtml(t("composer.mention_action_enter"))}</span>
                            </span>
                            ${descriptionMeta}
                            ${roleIdMeta}
                        </span>
                    </button>
                `;
              })
              .join("")}
        </div>
        <div class="prompt-mention-menu-footer" aria-hidden="true">
            <span class="prompt-mention-menu-key">↑↓</span>
            <span class="prompt-mention-menu-key">Tab</span>
            <span class="prompt-mention-menu-key">Esc</span>
        </div>
    `;
  syncPromptMentionActiveOptionIntoView(menu);
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
  renderPromptMentionAutocomplete();
}

function selectPromptMentionOption(index) {
  const option = promptMentionOptions[index];
  if (!option || !els.promptInput) {
    return false;
  }
  const source = String(els.promptInput.value || "");
  const before = source.slice(0, promptMentionRange.start);
  const after = source.slice(promptMentionRange.end);
  const spacer = after.length === 0 || /^\s/.test(after) ? "" : " ";
  const mentionTrigger =
    getPromptMentionTrigger(source.slice(promptMentionRange.start))
    || promptMentionTrigger;
  const insertedMention = `${mentionTrigger}${option.insertTerm}`;
  const nextValue = `${before}${insertedMention}${spacer}${after || " "}`;

  els.promptInput.value = nextValue;
  const caretPosition =
    before.length + insertedMention.length + (after ? spacer.length : 1);
  if ("selectionStart" in els.promptInput) {
    els.promptInput.selectionStart = caretPosition;
  }
  if ("selectionEnd" in els.promptInput) {
    els.promptInput.selectionEnd = caretPosition;
  }
  els.promptInput.style.height = "auto";
  if (Number.isFinite(els.promptInput.scrollHeight)) {
    els.promptInput.style.height = `${els.promptInput.scrollHeight}px`;
  }
  els.promptInput.focus?.();
  dismissPromptMentionAutocomplete();
  return true;
}

function dismissPromptMentionAutocomplete() {
  promptMentionOptions = [];
  activePromptMentionIndex = -1;
  promptMentionQuery = "";
  promptMentionTrigger = DEFAULT_PROMPT_MENTION_TRIGGER;
  promptMentionRange = {
    start: 0,
    end: 0,
  };
  renderPromptMentionAutocomplete();
}

function isPromptMentionAutocompleteOpen() {
  return (
    !!els.promptMentionMenu &&
    els.promptMentionMenu.hidden !== true &&
    promptMentionOptions.length > 0
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
  if (!activeOption || typeof activeOption.scrollIntoView !== "function") {
    return;
  }
  activeOption.scrollIntoView({
    block: "nearest",
  });
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

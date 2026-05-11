# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def read_frontend(path: str) -> str:
    return (ROOT / "frontend" / "dist" / path).read_text(encoding="utf-8")


def test_voice_input_button_and_assets_are_linked() -> None:
    index = read_frontend("index.html")
    style = read_frontend("style.css")
    dom = read_frontend("js/utils/dom.js")
    bootstrap = read_frontend("js/app/bootstrap.js")

    assert 'id="voice-input-btn"' in index
    voice_button_start = index.index('id="voice-input-btn"')
    voice_button_end = index.index("</button>", voice_button_start)
    voice_button = index[voice_button_start:voice_button_end]
    assert "hidden" in voice_button
    assert "disabled" in voice_button
    assert 'class="composer-actions"' in index
    composer_actions_start = index.index('class="composer-actions"')
    composer_actions_end = index.index('id="prompt-input-hint"')
    composer_actions = index[composer_actions_start:composer_actions_end]
    assert 'id="resume-run-btn"' in composer_actions
    assert 'id="stop-btn"' in composer_actions
    assert 'id="voice-input-btn"' in composer_actions
    assert 'id="send-btn"' in composer_actions
    assert composer_actions.index('id="resume-run-btn"') < composer_actions.index(
        'id="voice-input-btn"'
    )
    assert composer_actions.index('id="stop-btn"') < composer_actions.index(
        'id="voice-input-btn"'
    )
    assert composer_actions.index('id="voice-input-btn"') < composer_actions.index(
        'id="send-btn"'
    )
    assert "/css/components/voice.css" in index
    assert "css/components/voice.css" in style
    assert "voiceInputBtn" in dom
    assert "initializeVoiceInput" in bootstrap
    voice_input = read_frontend("js/components/voiceInput.js")
    assert "const TARGET_SAMPLE_RATE = 16000;" in voice_input
    assert "waitForVoiceReady" in voice_input
    assert "VOICE_WORKLET_MODULE_URL" in voice_input
    assert "AudioWorkletNode" in voice_input
    assert "VOICE_SPACE_HOLD_MS = 350" in voice_input
    assert "VOICE_NO_SPEECH_TIMEOUT_MS = 5000" in voice_input
    assert "VOICE_SILENCE_AUTO_STOP_MS = 1600" in voice_input
    assert "VOICE_AUDIO_FRAME_TIMEOUT_MS = 1800" in voice_input
    assert "VOICE_BUFFER_MAX_BYTES = 512000" in voice_input
    assert "VOICE_SOCKET_BACKPRESSURE_SOFT_BYTES = 512000" in voice_input
    assert "VOICE_SOCKET_BACKPRESSURE_HARD_BYTES = 2097152" in voice_input
    assert "void closeVoiceInput({ keepText: true });" in voice_input
    assert "VOICE_STATES = Object.freeze" in voice_input
    assert "setVoiceState(VOICE_STATES.STARTING)" in voice_input
    assert "setVoiceState(VOICE_STATES.CONNECTING)" in voice_input
    assert "setVoiceState(VOICE_STATES.LISTENING)" in voice_input
    assert "serverSpeechActive" in voice_input
    assert "handleVoiceSpaceKeydown" in voice_input
    assert (
        "document.addEventListener('keydown', handleVoiceSpaceKeydown)" in voice_input
    )
    assert "focusPromptInputForVoice" in voice_input
    assert "suppressSilenceDetection" in voice_input
    assert "releaseVoiceSpaceSilenceSuppression" in voice_input
    assert "armNoSpeechTimer" in voice_input
    assert "scheduleVoiceAutoStop" in voice_input
    assert "renderVoiceState" in voice_input
    assert "isCurrentSession(session)" in voice_input
    assert "agent-teams-model-profiles-updated" in voice_input
    assert "waitForFirstAudioFrame(session)" in voice_input
    assert "enqueueAudioFrame(session" in voice_input
    assert "session.audioFrameCount += 1" in voice_input
    assert "session.audioByteCount += frame.byteLength" in voice_input
    assert "trimAudioBuffer(session)" in voice_input
    assert "isVoiceSocketBackpressured(session)" in voice_input
    assert "session.socket?.bufferedAmount" in voice_input
    assert "stopVoiceInputForBackpressure(session)" in voice_input
    assert "session.readyForAudio = true" in voice_input
    assert "session.socket.send(merged.buffer)" in voice_input
    assert "await resumeAudioContext(session)" in voice_input
    assert "syncVoiceWorkletTargetSampleRate(session)" in voice_input
    assert "targetSampleRate: session.targetSampleRate" in voice_input
    assert (
        "const sampleRateChanged = sampleRate !== session.targetSampleRate"
        in voice_input
    )
    assert "clearBufferedAudio(session)" in voice_input
    assert "function clearBufferedAudio(session)" in voice_input
    assert "setDatasetValueIfChanged(button, 'voiceState'" in voice_input
    assert "voiceState === VOICE_STATES.CONNECTING" in voice_input
    assert "voiceState !== VOICE_STATES.LISTENING" in voice_input
    assert "session.serverSpeechActive || normalizedLevel" in voice_input
    assert "observeComposerRunControls" in voice_input
    assert "isComposerRunControlVisible" in voice_input
    assert "listComposerRunControls" in voice_input
    assert "scheduleVoiceButtonRender" in voice_input
    assert "subtree: true" not in voice_input
    assert "syncComposerRunActionClass" in voice_input
    assert "setVoiceButtonHiddenForRunControl" not in voice_input
    assert "const nextDisplay = hidden ? 'none' : ''" not in voice_input
    assert "button.toggleAttribute('hidden', !configured && !busy)" in voice_input
    assert "isVoiceInputConfigured" in voice_input
    assert "hasVoiceRuntimeSupport" in voice_input
    assert "!voiceFlow && !activeSession && !canStartVoiceInput()" in voice_input
    assert "VOICE_LEVEL_UI_MIN_INTERVAL_MS = 80" in voice_input
    assert "requestVoiceLevelFrame" in voice_input
    assert "renderVoiceMeterLevel" in voice_input
    assert "globalThis.__relayTeamsVoiceInputActive = false" in voice_input
    assert "setVoiceLevelProperty(button, normalizedLevel.toFixed(2))" in voice_input
    assert "payload.mode === 'replace'" in voice_input
    assert "errorShown" in voice_input
    assert "showVoiceSessionError" in voice_input
    assert "dedupeKey: 'voice-input-error'" in voice_input
    assert "session.finalizePromise" in voice_input
    assert "session.finalizeResolve?.()" in voice_input
    assert (
        "async function finalizeVoiceSession(session, { closeSocket = false } = {})"
        in voice_input
    )
    assert "void finalizeVoiceSession(session, { closeSocket: true });" in voice_input
    assert "await disposeSession(session, { closeSocket });" in voice_input
    assert "globalThis.__relayTeamsStopVoiceInput = stopVoiceInput;" in voice_input
    assert "flushAudio(session, { allowBackpressure: true });" in voice_input
    assert (
        "function flushAudio(session, { allowBackpressure = false } = {})"
        in voice_input
    )
    assert (
        "if (!allowBackpressure && isVoiceSocketBackpressured(session))" in voice_input
    )
    prompt = read_frontend("js/app/prompt.js")
    assert "stopActiveVoiceInputBeforeSend" in prompt
    assert "await globalThis.__relayTeamsStopVoiceInput({ keepText: true });" in prompt
    assert prompt.index("await stopActiveVoiceInputBeforeSend();") < prompt.index(
        "const rawText = els.promptInput.value.trim();"
    )
    voice_css = read_frontend("css/components/voice.css")
    interface_css = read_frontend("css/components/interface.css")
    new_session_css = read_frontend("css/components/new-session-draft-composer.css")
    assert "#input-container .composer-actions" in interface_css
    assert "display: flex;" in interface_css
    assert "right: auto;" in interface_css
    assert "bottom: auto;" in interface_css
    assert "padding: 18px 312px 16px 18px;" in interface_css
    assert "padding: 18px 204px 18px 16px;" in interface_css
    assert "padding: 22px 420px 64px 22px;" in new_session_css
    assert "padding: 18px 204px 58px 18px;" in new_session_css
    assert (
        "#input-container:not(.is-new-session-draft-composer) #prompt-input"
        in voice_css
    )
    assert "has-run-composer-action .composer-voice-btn" not in voice_css
    assert ".composer-voice-status" not in voice_css
    assert '.composer-voice-btn[data-voice-state="connecting"]::before' in voice_css
    assert "@keyframes voice-button-pulse" in voice_css
    voice_worklet = read_frontend("js/components/voiceInputWorklet.js")
    assert "registerProcessor('relay-teams-voice-input'" in voice_worklet
    assert "payload.type !== 'configure'" in voice_worklet
    assert "this.targetSampleRate = nextTargetSampleRate" in voice_worklet
    assert "this.port.postMessage({ type: 'level', level });" not in voice_worklet
    assert "this.maxLevel = Math.max(this.maxLevel, level);" in voice_worklet
    assert "this.port.postMessage({ type: 'audio'" in voice_worklet


def test_speech_settings_panel_is_registered() -> None:
    settings = read_frontend("js/components/settings/index.js")
    speech_settings = read_frontend("js/components/settings/speechSettings.js")
    api = read_frontend("js/core/api.js")

    assert 'data-tab="general"' in settings
    assert "renderGeneralSettingsPanelMarkup" in settings
    assert "renderSpeechSettingsSectionMarkup" in settings
    assert 'id="save-general-btn"' in settings
    assert "fetchSpeechConfig" in speech_settings
    assert "saveSpeechConfig" in speech_settings
    assert "renderSpeechSettingsSectionMarkup" in speech_settings
    assert 'id="save-speech-btn"' not in speech_settings
    assert "formatMessage" in speech_settings
    assert "settings.speech.load_failed" in speech_settings
    assert "settings.speech.load_failed_detail" in speech_settings
    assert "Promise.allSettled" in speech_settings
    assert "speechConfig = speechConfig || {};" in speech_settings
    assert "let speechConfigLoaded = false;" in speech_settings
    assert "let modelProfilesLoaded = false;" in speech_settings
    assert "modelProfiles = profilesResult.value || {};" in speech_settings
    assert "modelProfilesLoaded = true;" in speech_settings
    assert "renderSpeechSettingsPanel();" in speech_settings
    assert "export function canSaveSpeechConfig()" in speech_settings
    assert "return speechConfigLoaded && modelProfilesLoaded;" in speech_settings
    assert "createSpeechSttWebSocketUrl" in api
    assert "resolveSpeechCapability" in speech_settings
    assert '<select id="speech-language"' in speech_settings
    assert "SPEECH_LANGUAGE_OPTIONS" in speech_settings
    assert "buildLanguageOptions" in speech_settings
    assert "let hasSelectedOption = !selected;" in speech_settings
    assert "options.unshift(" in speech_settings
    assert "options.push(" in speech_settings
    assert "hasSpeechRealtimeModelOverride" in speech_settings
    assert (
        "return normalizeOptionalValue(profile?.speech_realtime?.model) !== null;"
        in speech_settings
    )
    assert "resolveRealtimeSpeechModel(profile)" in speech_settings
    assert "vad_threshold: speechConfig?.vad_threshold" in speech_settings
    assert (
        "vad_prefix_padding_ms: speechConfig?.vad_prefix_padding_ms" in speech_settings
    )
    assert (
        "vad_silence_duration_ms: speechConfig?.vad_silence_duration_ms"
        in speech_settings
    )
    assert "noise_reduction: speechConfig?.noise_reduction" in speech_settings
    assert "speech-profile-notes" not in speech_settings
    assert "speech-settings-status" not in speech_settings
    i18n = read_frontend("js/utils/i18n.js")
    assert "'settings.speech.load_failed'" in i18n
    assert "'settings.speech.load_failed_detail'" in i18n


def test_model_profile_speech_capability_controls_are_registered() -> None:
    template = read_frontend("js/components/settings/modelProfiles/template.js")
    model_profiles = read_frontend("js/components/settings/modelProfiles.js")
    i18n = read_frontend("js/utils/i18n.js")

    assert 'id="profile-speech-capability"' in template
    assert 'id="profile-speech-realtime-model"' not in template
    assert 'id="profile-speech-realtime-url-template"' not in template
    assert "SPEECH_CAPABILITY_MODES" in model_profiles
    assert "buildDraftProfileCapabilities" in model_profiles
    assert "readDraftSpeechRealtimeConfig" not in model_profiles
    assert "capability_stt" in i18n


def test_message_read_aloud_reuses_message_actions() -> None:
    actions = read_frontend("js/components/messageRenderer/messageActions.js")
    speech = read_frontend("js/components/messageSpeech.js")

    assert "bindReadAloudButton" in actions
    assert "supportsMessageSpeech" in actions
    assert "SpeechSynthesisUtterance" in speech


def test_toast_helper_supports_dedupe_key() -> None:
    feedback = read_frontend("js/utils/feedback.js")

    assert "dedupeKey = ''" in feedback
    assert "data-feedback-toast-key" in feedback

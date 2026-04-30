/**
 * components/voiceInput.js
 * Realtime model-backed speech-to-text for the prompt composer.
 */
import {
    createSpeechSttWebSocketUrl,
    fetchSpeechConfig,
} from '../core/api.js';
import { els } from '../utils/dom.js';
import { showToast } from '../utils/feedback.js';
import { t } from '../utils/i18n.js';
import { errorToPayload, logError } from '../utils/logger.js';

const TARGET_SAMPLE_RATE = 16000;
const SEND_INTERVAL_MS = 80;
const VOICE_ERROR_DEDUPE_MS = 5000;
const VOICE_READY_TIMEOUT_MS = 12000;
const VOICE_WORKLET_MODULE_URL = '/js/components/voiceInputWorklet.js';
const VOICE_WORKLET_PROCESSOR_NAME = 'relay-teams-voice-input';
const VOICE_SILENCE_LEVEL_THRESHOLD = 0.035;
const VOICE_NO_SPEECH_TIMEOUT_MS = 5000;
const VOICE_SILENCE_AUTO_STOP_MS = 1600;
const VOICE_SPEECH_STOP_GRACE_MS = 900;
const VOICE_FINALIZE_TIMEOUT_MS = 8000;
const VOICE_SPACE_HOLD_MS = 350;
const VOICE_LEVEL_UI_MIN_INTERVAL_MS = 80;
const VOICE_AUDIO_FRAME_TIMEOUT_MS = 1800;
const VOICE_BUFFER_MAX_BYTES = 512000;
const VOICE_SOCKET_BACKPRESSURE_SOFT_BYTES = 512000;
const VOICE_SOCKET_BACKPRESSURE_HARD_BYTES = 2097152;
const VOICE_SOCKET_BACKPRESSURE_TIMEOUT_MS = 2500;
const COMPOSER_RUN_ACTION_ACTIVE_CLASS = 'has-run-composer-action';

const VOICE_STATES = Object.freeze({
    IDLE: 'idle',
    STARTING: 'starting',
    CONNECTING: 'connecting',
    LISTENING: 'listening',
    TRANSCRIBING: 'transcribing',
    ERROR: 'error',
});

let initialized = false;
let config = null;
let activeSession = null;
let voiceFlow = null;
let voiceState = VOICE_STATES.IDLE;
let nextVoiceToken = 1;
let suppressInputEvent = false;
let lastVoiceErrorKey = '';
let lastVoiceErrorAt = 0;
let spacePressState = null;
let runControlObserver = null;
let voiceButtonSyncFrame = null;
globalThis.__relayTeamsVoiceInputActive = false;
globalThis.__relayTeamsStopVoiceInput = stopVoiceInput;

export function initializeVoiceInput() {
    if (initialized) return;
    initialized = true;
    if (els.voiceInputBtn) {
        els.voiceInputBtn.onclick = () => {
            void toggleVoiceInput();
        };
    }
    if (els.promptInput) {
        els.promptInput.addEventListener('input', () => {
            if (activeSession && !suppressInputEvent) {
                void stopVoiceInput({ keepText: true });
            }
        });
    }
    document.addEventListener('keydown', handleVoiceSpaceKeydown);
    document.addEventListener('keyup', handleVoiceSpaceKeyup);
    observeComposerRunControls();
    window.addEventListener('blur', clearVoiceSpacePress);
    document.addEventListener('agent-teams-session-selected', () => {
        void closeVoiceInput({ keepText: true });
    });
    document.addEventListener('agent-teams-new-session-draft-opened', () => {
        void closeVoiceInput({ keepText: true });
    });
    document.addEventListener('agent-teams-language-changed', () => {
        renderVoiceState();
    });
    document.addEventListener('agent-teams-speech-config-updated', () => {
        void refreshVoiceInputConfig();
    });
    document.addEventListener('agent-teams-model-profiles-updated', () => {
        void refreshVoiceInputConfig();
    });
    document.addEventListener('agent-teams-stop-voice-input', () => {
        void stopVoiceInput({ keepText: true });
    });
    void refreshVoiceInputConfig();
}

export async function refreshVoiceInputConfig() {
    try {
        config = await fetchSpeechConfig();
    } catch (error) {
        config = null;
        logError('frontend.voice.config_failed', 'Failed to load speech config', errorToPayload(error));
    }
    renderVoiceState();
}

export async function stopVoiceInput(options = {}) {
    if (voiceFlow && !activeSession) {
        await cancelVoiceStart();
        return;
    }
    const session = activeSession;
    if (!session) {
        resetVoiceState();
        return;
    }
    if (session.stopping || voiceState === VOICE_STATES.TRANSCRIBING) {
        await session.finalizePromise;
        return;
    }
    if (voiceState === VOICE_STATES.CONNECTING || voiceState === VOICE_STATES.STARTING) {
        await closeVoiceInput({ keepText: true });
        return;
    }
    session.stopping = true;
    stopAudioCapture(session);
    flushAudio(session, { allowBackpressure: true });
    if (session.socket?.readyState === WebSocket.OPEN) {
        try {
            session.socket.send(JSON.stringify({ type: 'stop' }));
        } catch (error) {
            logError('frontend.voice.stop_send_failed', 'Failed to send voice stop', errorToPayload(error));
        }
    }
    armVoiceFinalizeTimer(session);
    setVoiceState(VOICE_STATES.TRANSCRIBING);
    await session.finalizePromise;
}

async function closeVoiceInput(options = {}) {
    const session = activeSession;
    if (!session) {
        await cancelVoiceStart();
        resetVoiceState();
        return;
    }
    invalidateVoiceFlow();
    activeSession = null;
    await disposeSession(session, { closeSocket: true });
    if (options.keepText !== true) {
        restorePromptText(session);
    }
    resetVoiceState();
}

async function toggleVoiceInput() {
    if (voiceFlow || activeSession) {
        await stopVoiceInput({ keepText: true });
        return;
    }
    await startVoiceInput();
}

async function startVoiceInput(options = {}) {
    if (voiceFlow || activeSession) {
        return;
    }
    if (!canStartVoiceInput()) {
        showToast({
            title: t('voice.unavailable_title'),
            message: resolveUnavailableMessage(),
            tone: 'warning',
        });
        renderVoiceState();
        return;
    }
    const flow = beginVoiceFlow({
        suppressSilenceDetection: options.suppressSilenceDetection === true,
    });
    setVoiceState(VOICE_STATES.STARTING);
    let session = null;
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (!isCurrentFlow(flow)) {
            stopStream(stream);
            return;
        }
        const AudioContextCtor = resolveAudioContextCtor();
        const audioContext = new AudioContextCtor();
        const socket = new WebSocket(createSpeechSttWebSocketUrl());
        socket.binaryType = 'arraybuffer';
        session = createSession({
            token: flow.token,
            stream,
            audioContext,
            socket,
            suppressSilenceDetection: flow.suppressSilenceDetection,
        });
        activeSession = session;
        bindSocket(session);
        setVoiceState(VOICE_STATES.CONNECTING);
        await startAudioPipeline(session);
        if (!isCurrentSession(session)) {
            return;
        }
        await waitForSocketOpen(session);
        if (!isCurrentSession(session)) {
            return;
        }
        session.socket.send(JSON.stringify({ type: 'start' }));
        await waitForVoiceReady(session);
        if (!isCurrentSession(session)) {
            return;
        }
        session.readyForAudio = true;
        flushAudio(session);
        setVoiceState(VOICE_STATES.LISTENING);
        if (!session.suppressSilenceDetection) {
            armNoSpeechTimer(session);
        }
    } catch (error) {
        if (!isCurrentFlow(flow) && (!session || !isCurrentSession(session))) {
            return;
        }
        const failedSession = session || activeSession;
        await cleanupFailedVoiceStart(failedSession);
        showVoiceSessionError(failedSession, {
            title: t('voice.start_failed_title'),
            message: error?.message || t('voice.start_failed_message'),
        });
    }
}

function beginVoiceFlow({ suppressSilenceDetection }) {
    const flow = {
        token: nextVoiceToken,
        suppressSilenceDetection,
    };
    nextVoiceToken += 1;
    voiceFlow = flow;
    globalThis.__relayTeamsVoiceInputActive = true;
    return flow;
}

function invalidateVoiceFlow() {
    if (voiceFlow) {
        voiceFlow = null;
    }
    nextVoiceToken += 1;
}

async function cancelVoiceStart() {
    const session = activeSession;
    invalidateVoiceFlow();
    if (session) {
        activeSession = null;
        await disposeSession(session, { closeSocket: true });
    }
    resetVoiceState();
}

function createSession({ token, stream, audioContext, socket, suppressSilenceDetection }) {
    const input = els.promptInput;
    const selectionStart = Number.isFinite(input?.selectionStart) ? input.selectionStart : input.value.length;
    const selectionEnd = Number.isFinite(input?.selectionEnd) ? input.selectionEnd : selectionStart;
    const session = {
        token,
        stream,
        audioContext,
        socket,
        source: null,
        processor: null,
        workletNode: null,
        flushTimer: null,
        buffer: [],
        prefix: input.value.slice(0, selectionStart),
        suffix: input.value.slice(selectionEnd),
        completedText: '',
        deltaText: '',
        targetSampleRate: TARGET_SAMPLE_RATE,
        readySettled: false,
        readyResolve: null,
        readyReject: null,
        closed: false,
        errorShown: false,
        socketErrored: false,
        stopping: false,
        finalizing: false,
        finalizeResolve: null,
        audioClosed: false,
        hasDetectedSpeech: false,
        serverSpeechActive: false,
        lastSpeechAt: 0,
        silenceTimer: null,
        noSpeechTimer: null,
        finalizeTimer: null,
        audioFrameSettled: false,
        audioFrameResolve: null,
        audioFrameReject: null,
        audioFrameTimer: null,
        audioFrameCount: 0,
        audioByteCount: 0,
        bufferedBytes: 0,
        droppedAudioBytes: 0,
        readyForAudio: false,
        backpressureStartedAt: 0,
        backpressureErrorShown: false,
        lastAudioFrameAt: 0,
        suppressSilenceDetection,
        levelTimer: null,
        levelFrame: null,
        pendingLevel: 0,
        lastLevelUiAt: 0,
    };
    session.readyPromise = new Promise((resolve, reject) => {
        session.readyResolve = resolve;
        session.readyReject = reject;
    });
    session.finalizePromise = new Promise(resolve => {
        session.finalizeResolve = resolve;
    });
    resetAudioFramePromise(session);
    return session;
}

function bindSocket(session) {
    session.socket.onmessage = event => {
        handleVoiceMessage(session, event);
    };
    session.socket.onerror = () => {
        session.socketErrored = true;
    };
    session.socket.onclose = () => {
        if (!isCurrentSession(session)) return;
        rejectVoiceReady(session, new Error(t('voice.stream_failed_message')));
        if (session.socketErrored) {
            showVoiceSessionError(session, {
                title: t('voice.stream_failed_title'),
                message: t('voice.stream_failed_message'),
            });
        }
        void finalizeVoiceSession(session);
    };
}

async function finalizeVoiceSession(session, { closeSocket = false } = {}) {
    if (session.finalizing) {
        return session.finalizePromise;
    }
    session.finalizing = true;
    try {
        if (activeSession === session) {
            activeSession = null;
        }
        invalidateVoiceFlow();
        await disposeSession(session, { closeSocket });
        resetVoiceState();
    } finally {
        session.finalizeResolve?.();
    }
    return session.finalizePromise;
}

async function cleanupFailedVoiceStart(session) {
    if (session) {
        activeSession = null;
        await disposeSession(session, { closeSocket: true });
    }
    invalidateVoiceFlow();
    setVoiceState(VOICE_STATES.ERROR);
    window.setTimeout(() => {
        if (!activeSession && !voiceFlow && voiceState === VOICE_STATES.ERROR) {
            resetVoiceState();
        }
    }, 900);
}

async function disposeSession(session, { closeSocket }) {
    session.closed = true;
    clearVoiceTimers(session);
    stopAudioCapture(session);
    if (closeSocket) {
        try {
            session.socket?.close();
        } catch (error) {
            logError('frontend.voice.socket_close_failed', 'Failed to close voice socket', errorToPayload(error));
        }
    }
    await closeAudioContext(session);
}

function stopAudioCapture(session) {
    stopAudioNodes(session);
    stopStream(session.stream);
}

function stopAudioNodes(session) {
    if (session.flushTimer) {
        window.clearInterval(session.flushTimer);
        session.flushTimer = null;
    }
    session.processor?.disconnect();
    session.workletNode?.port?.close?.();
    session.workletNode?.disconnect();
    session.source?.disconnect();
    session.processor = null;
    session.workletNode = null;
    session.source = null;
}

function stopStream(stream) {
    stream?.getTracks?.().forEach(track => track.stop());
}

function clearVoiceTimers(session) {
    if (session.silenceTimer) {
        window.clearTimeout(session.silenceTimer);
        session.silenceTimer = null;
    }
    if (session.noSpeechTimer) {
        window.clearTimeout(session.noSpeechTimer);
        session.noSpeechTimer = null;
    }
    if (session.finalizeTimer) {
        window.clearTimeout(session.finalizeTimer);
        session.finalizeTimer = null;
    }
    if (session.audioFrameTimer) {
        window.clearTimeout(session.audioFrameTimer);
        session.audioFrameTimer = null;
    }
    if (session.levelTimer) {
        window.clearTimeout(session.levelTimer);
        session.levelTimer = null;
    }
    if (session.levelFrame) {
        window.cancelAnimationFrame(session.levelFrame);
        session.levelFrame = null;
    }
}

async function closeAudioContext(session) {
    if (session.audioClosed) return;
    session.audioClosed = true;
    await session.audioContext?.close?.();
}

async function startAudioPipeline(session) {
    await resumeAudioContext(session);
    if (!isCurrentSession(session)) return;
    if (session.audioContext.audioWorklet && window.AudioWorkletNode) {
        try {
            resetAudioFramePromise(session);
            await startAudioWorkletPipeline(session);
            await waitForFirstAudioFrame(session);
            return;
        } catch (error) {
            logError('frontend.voice.worklet_failed', 'Failed to start voice audio worklet', errorToPayload(error));
            stopAudioNodes(session);
        }
    }
    resetAudioFramePromise(session);
    startScriptProcessorPipeline(session);
    await waitForFirstAudioFrame(session);
}

async function resumeAudioContext(session) {
    if (session.audioContext?.state === 'suspended') {
        await session.audioContext.resume();
    }
}

async function startAudioWorkletPipeline(session) {
    await session.audioContext.audioWorklet.addModule(VOICE_WORKLET_MODULE_URL);
    if (!isCurrentSession(session)) return;
    const source = session.audioContext.createMediaStreamSource(session.stream);
    const workletNode = new AudioWorkletNode(session.audioContext, VOICE_WORKLET_PROCESSOR_NAME, {
        processorOptions: {
            targetSampleRate: session.targetSampleRate,
        },
    });
    session.source = source;
    session.workletNode = workletNode;
    workletNode.port.onmessage = event => {
        if (!isCurrentSession(session) || session.stopping) return;
        const payload = event.data || {};
        const level = Number(payload.level);
        if (Number.isFinite(level)) {
            updateVoiceMeterLevel(session, level);
        }
        if (payload.type === 'audio' && payload.audio instanceof ArrayBuffer) {
            enqueueAudioFrame(session, payload.audio);
        }
    };
    workletNode.port.start?.();
    source.connect(workletNode);
    workletNode.connect(session.audioContext.destination);
    session.flushTimer = window.setInterval(() => flushAudio(session), SEND_INTERVAL_MS);
}

function startScriptProcessorPipeline(session) {
    const source = session.audioContext.createMediaStreamSource(session.stream);
    const processor = session.audioContext.createScriptProcessor(4096, 1, 1);
    session.source = source;
    session.processor = processor;
    processor.onaudioprocess = event => {
        if (!isCurrentSession(session) || session.stopping) return;
        const inputData = event.inputBuffer.getChannelData(0);
        updateVoiceLevel(session, inputData);
        const pcm = floatToPcm16(downsample(inputData, session.audioContext.sampleRate, session.targetSampleRate));
        enqueueAudioFrame(session, pcm);
    };
    source.connect(processor);
    processor.connect(session.audioContext.destination);
    session.flushTimer = window.setInterval(() => flushAudio(session), SEND_INTERVAL_MS);
}

function flushAudio(session, { allowBackpressure = false } = {}) {
    if (!isCurrentSession(session)
        || session.socket.readyState !== WebSocket.OPEN
        || !session.readyForAudio
        || session.buffer.length === 0) {
        return;
    }
    if (!allowBackpressure && isVoiceSocketBackpressured(session)) {
        return;
    }
    const totalLength = session.buffer.reduce((sum, item) => sum + item.byteLength, 0);
    const merged = new Uint8Array(totalLength);
    let offset = 0;
    session.buffer.forEach(item => {
        merged.set(new Uint8Array(item), offset);
        offset += item.byteLength;
    });
    session.buffer = [];
    session.bufferedBytes = 0;
    session.socket.send(merged.buffer);
}

function enqueueAudioFrame(session, frame) {
    if (!isCurrentSession(session) || !(frame instanceof ArrayBuffer) || frame.byteLength === 0) {
        return;
    }
    session.audioFrameCount += 1;
    session.audioByteCount += frame.byteLength;
    session.lastAudioFrameAt = Date.now();
    resolveFirstAudioFrame(session);
    session.buffer.push(frame);
    session.bufferedBytes += frame.byteLength;
    trimAudioBuffer(session);
}

function trimAudioBuffer(session) {
    while (session.bufferedBytes > VOICE_BUFFER_MAX_BYTES && session.buffer.length > 0) {
        const removed = session.buffer.shift();
        const removedBytes = removed?.byteLength || 0;
        session.bufferedBytes -= removedBytes;
        session.droppedAudioBytes += removedBytes;
    }
}

function clearBufferedAudio(session) {
    if (session.bufferedBytes > 0) {
        session.droppedAudioBytes += session.bufferedBytes;
    }
    session.buffer = [];
    session.bufferedBytes = 0;
}

function isVoiceSocketBackpressured(session) {
    const bufferedAmount = Number(session.socket?.bufferedAmount || 0);
    if (!Number.isFinite(bufferedAmount) || bufferedAmount < VOICE_SOCKET_BACKPRESSURE_SOFT_BYTES) {
        session.backpressureStartedAt = 0;
        return false;
    }
    const now = Date.now();
    if (!session.backpressureStartedAt) {
        session.backpressureStartedAt = now;
    }
    const hardBlocked = bufferedAmount >= VOICE_SOCKET_BACKPRESSURE_HARD_BYTES
        && now - session.backpressureStartedAt >= VOICE_SOCKET_BACKPRESSURE_TIMEOUT_MS;
    if (hardBlocked) {
        stopVoiceInputForBackpressure(session);
    }
    return true;
}

function stopVoiceInputForBackpressure(session) {
    if (!isCurrentSession(session) || session.backpressureErrorShown) {
        return;
    }
    session.backpressureErrorShown = true;
    showVoiceSessionError(session, {
        title: t('voice.stream_failed_title'),
        message: t('voice.stream_failed_message'),
    });
    void stopVoiceInput({ keepText: true });
}

function handleVoiceMessage(session, event) {
    if (!isCurrentSession(session)) return;
    let payload = null;
    try {
        payload = JSON.parse(String(event.data || '{}'));
    } catch (error) {
        logError('frontend.voice.event_parse_failed', 'Failed to parse voice event', errorToPayload(error));
        return;
    }
    if (payload.type === 'status') {
        handleVoiceStatusMessage(session, payload);
        return;
    }
    if (payload.type === 'speech') {
        handleVoiceSpeechMessage(session, payload);
        return;
    }
    if (payload.type === 'delta') {
        handleVoiceDeltaMessage(session, payload);
        return;
    }
    if (payload.type === 'completed') {
        handleVoiceCompletedMessage(session, payload);
        return;
    }
    if (payload.type === 'error') {
        rejectVoiceReady(session, new Error(String(payload.message || t('voice.stream_failed_message'))));
        showVoiceSessionError(session, {
            title: t('voice.stream_failed_title'),
            message: String(payload.message || t('voice.stream_failed_message')),
        });
        setVoiceState(VOICE_STATES.ERROR);
        void closeVoiceInput({ keepText: true });
    }
}

function handleVoiceStatusMessage(session, payload) {
    if (!isCurrentSession(session)) return;
    const status = String(payload.status || '');
    if (status === 'ready' || status === 'connected') {
        const sampleRate = Number(payload.sample_rate);
        if (Number.isFinite(sampleRate) && sampleRate > 0) {
            const sampleRateChanged = sampleRate !== session.targetSampleRate;
            session.targetSampleRate = sampleRate;
            if (sampleRateChanged && !session.readyForAudio) {
                clearBufferedAudio(session);
            }
            syncVoiceWorkletTargetSampleRate(session);
        }
        resolveVoiceReady(session);
        return;
    }
    if (status === 'speech_started') {
        session.serverSpeechActive = true;
        markVoiceSpeechDetected(session);
        setVoiceState(VOICE_STATES.LISTENING);
        return;
    }
    if (status === 'speech_stopped') {
        session.serverSpeechActive = false;
        renderVoiceState();
        scheduleVoiceAutoStop(session, VOICE_SPEECH_STOP_GRACE_MS);
    }
}

function syncVoiceWorkletTargetSampleRate(session) {
    session.workletNode?.port?.postMessage?.({
        type: 'configure',
        targetSampleRate: session.targetSampleRate,
    });
}

function handleVoiceSpeechMessage(session, payload) {
    if (!isCurrentSession(session)) return;
    const status = String(payload.status || '');
    if (status === 'started') {
        session.serverSpeechActive = true;
        markVoiceSpeechDetected(session);
        setVoiceState(VOICE_STATES.LISTENING);
        return;
    }
    if (status === 'stopped') {
        session.serverSpeechActive = false;
        renderVoiceState();
        scheduleVoiceAutoStop(session, VOICE_SPEECH_STOP_GRACE_MS);
    }
}

function handleVoiceDeltaMessage(session, payload) {
    if (!isCurrentSession(session)) return;
    const text = String(payload.text || '');
    session.deltaText = payload.mode === 'replace'
        ? text
        : session.deltaText + text;
    if (voiceState !== VOICE_STATES.LISTENING) {
        setVoiceState(VOICE_STATES.TRANSCRIBING);
    }
    renderPromptText(session);
}

function handleVoiceCompletedMessage(session, payload) {
    if (!isCurrentSession(session)) return;
    const completed = String(payload.text || '').trim();
    session.completedText = joinVoiceText(session.completedText, completed || session.deltaText);
    session.deltaText = '';
    if (session.stopping) {
        clearVoiceTimers(session);
        setVoiceState(VOICE_STATES.TRANSCRIBING);
    } else if (voiceState !== VOICE_STATES.LISTENING) {
        setVoiceState(VOICE_STATES.TRANSCRIBING);
    }
    renderPromptText(session);
    if (session.stopping) {
        void finalizeVoiceSession(session, { closeSocket: true });
    }
}

function showVoiceSessionError(session, { title, message }) {
    if (session && session.errorShown) {
        return;
    }
    const errorKey = `${title}\n${message}`;
    const now = Date.now();
    if (errorKey === lastVoiceErrorKey && now - lastVoiceErrorAt < VOICE_ERROR_DEDUPE_MS) {
        if (session) {
            session.errorShown = true;
        }
        return;
    }
    lastVoiceErrorKey = errorKey;
    lastVoiceErrorAt = now;
    if (session) {
        session.errorShown = true;
    }
    showToast({
        title,
        message,
        tone: 'danger',
        dedupeKey: 'voice-input-error',
    });
}

function renderPromptText(session) {
    if (!isCurrentSession(session)) return;
    const spokenText = joinVoiceText(session.completedText, session.deltaText);
    const nextValue = `${session.prefix}${spokenText}${session.suffix}`;
    suppressInputEvent = true;
    els.promptInput.value = nextValue;
    const cursor = session.prefix.length + spokenText.length;
    els.promptInput.selectionStart = cursor;
    els.promptInput.selectionEnd = cursor;
    els.promptInput.dispatchEvent(new Event('input', { bubbles: true }));
    suppressInputEvent = false;
}

function restorePromptText(session) {
    suppressInputEvent = true;
    els.promptInput.value = `${session.prefix}${session.suffix}`;
    els.promptInput.dispatchEvent(new Event('input', { bubbles: true }));
    suppressInputEvent = false;
}

function waitForSocketOpen(session) {
    return new Promise((resolve, reject) => {
        if (session.socket.readyState === WebSocket.OPEN) {
            resolve();
            return;
        }
        session.socket.addEventListener('open', () => resolve(), { once: true });
        session.socket.addEventListener('error', () => reject(new Error(t('voice.stream_failed_message'))), { once: true });
        session.socket.addEventListener('close', () => reject(new Error(t('voice.stream_failed_message'))), { once: true });
    });
}

function waitForVoiceReady(session) {
    return new Promise((resolve, reject) => {
        const timeout = window.setTimeout(() => {
            rejectVoiceReady(session, new Error(t('voice.start_failed_message')));
        }, VOICE_READY_TIMEOUT_MS);
        session.readyPromise.then(
            () => {
                window.clearTimeout(timeout);
                resolve();
            },
            error => {
                window.clearTimeout(timeout);
                reject(error);
            },
        );
    });
}

function resolveVoiceReady(session) {
    if (session.readySettled) return;
    session.readySettled = true;
    session.readyResolve?.();
}

function rejectVoiceReady(session, error) {
    if (session.readySettled) return;
    session.readySettled = true;
    session.readyReject?.(error);
}

function resetAudioFramePromise(session) {
    if (session.audioFrameTimer) {
        window.clearTimeout(session.audioFrameTimer);
        session.audioFrameTimer = null;
    }
    session.audioFrameSettled = false;
    session.audioFramePromise = new Promise((resolve, reject) => {
        session.audioFrameResolve = resolve;
        session.audioFrameReject = reject;
    });
}

function waitForFirstAudioFrame(session) {
    return new Promise((resolve, reject) => {
        session.audioFrameTimer = window.setTimeout(() => {
            rejectFirstAudioFrame(session, new Error(t('voice.start_failed_message')));
        }, VOICE_AUDIO_FRAME_TIMEOUT_MS);
        session.audioFramePromise.then(
            () => {
                window.clearTimeout(session.audioFrameTimer);
                session.audioFrameTimer = null;
                resolve();
            },
            error => {
                window.clearTimeout(session.audioFrameTimer);
                session.audioFrameTimer = null;
                reject(error);
            },
        );
    });
}

function resolveFirstAudioFrame(session) {
    if (session.audioFrameSettled) return;
    session.audioFrameSettled = true;
    session.audioFrameResolve?.();
}

function rejectFirstAudioFrame(session, error) {
    if (session.audioFrameSettled) return;
    session.audioFrameSettled = true;
    session.audioFrameReject?.(error);
}

function canStartVoiceInput() {
    return Boolean(
        els.voiceInputBtn
        && els.promptInput
        && hasVoiceRuntimeSupport()
        && isVoiceInputConfigured(),
    );
}

function hasVoiceRuntimeSupport() {
    return Boolean(
        navigator.mediaDevices?.getUserMedia
        && resolveAudioContextCtor()
        && window.WebSocket,
    );
}

function isVoiceInputConfigured() {
    return config?.configured === true;
}

function setVoiceState(nextState) {
    if (voiceState === nextState) {
        renderVoiceState();
        return;
    }
    voiceState = nextState;
    renderVoiceState();
}

function resetVoiceState() {
    voiceFlow = null;
    activeSession = null;
    voiceState = VOICE_STATES.IDLE;
    globalThis.__relayTeamsVoiceInputActive = false;
    renderVoiceState();
}

function renderVoiceState() {
    const button = els.voiceInputBtn;
    if (!button) return;
    const runControlVisible = isComposerRunControlVisible();
    const busy = voiceState !== VOICE_STATES.IDLE && voiceState !== VOICE_STATES.ERROR;
    const configured = isVoiceInputConfigured();
    const recording = [
        VOICE_STATES.STARTING,
        VOICE_STATES.CONNECTING,
        VOICE_STATES.LISTENING,
        VOICE_STATES.TRANSCRIBING,
    ].includes(voiceState);
    button.toggleAttribute('hidden', !configured && !busy);
    syncComposerRunActionClass(runControlVisible);
    toggleClassIfChanged(button, 'is-recording', recording);
    toggleClassIfChanged(button, 'is-stopping', activeSession?.stopping === true);
    toggleClassIfChanged(button, 'is-speaking', voiceState === VOICE_STATES.LISTENING && activeSession?.serverSpeechActive === true);
    setDatasetValueIfChanged(button, 'voiceState', voiceState);
    if (!recording) {
        setVoiceLevelProperty(button, '0');
    }
    const shouldDisable = voiceState === VOICE_STATES.TRANSCRIBING
        || (!busy && (!configured || !hasVoiceRuntimeSupport()));
    if (button.disabled !== shouldDisable) {
        button.disabled = shouldDisable;
    }
    globalThis.__relayTeamsVoiceInputActive = busy;
    button.setAttribute('title', busy ? t('voice.stop_title') : resolveButtonTitle());
    button.setAttribute('aria-label', busy ? resolveVoiceStatusText(voiceState) : resolveButtonTitle());
}

function observeComposerRunControls() {
    if (runControlObserver || typeof MutationObserver !== 'function') {
        return;
    }
    const controls = listComposerRunControls();
    if (!controls.length) {
        return;
    }
    runControlObserver = new MutationObserver(() => {
        scheduleVoiceButtonRender();
    });
    controls.forEach(control => {
        runControlObserver.observe(control, {
            attributes: true,
            attributeFilter: ['style', 'hidden', 'disabled', 'class'],
        });
    });
}

function scheduleVoiceButtonRender() {
    if (voiceButtonSyncFrame) return;
    voiceButtonSyncFrame = window.requestAnimationFrame(() => {
        voiceButtonSyncFrame = null;
        renderVoiceState();
    });
}

function isComposerRunControlVisible() {
    return listComposerRunControls().some(element => isVisibleRunControl(element));
}

function listComposerRunControls() {
    const controls = [];
    [els.stopBtn, els.resumeRunBtn].forEach(element => {
        if (element && !controls.includes(element)) {
            controls.push(element);
        }
    });
    const wrapper = els.promptInput?.closest?.('.input-wrapper') || null;
    if (!wrapper || typeof wrapper.querySelectorAll !== 'function') {
        return controls;
    }
    wrapper.querySelectorAll('#stop-btn, #resume-run-btn, .composer-resume-btn').forEach(element => {
        if (element !== els.voiceInputBtn && element !== els.sendBtn && !controls.includes(element)) {
            controls.push(element);
        }
    });
    return controls;
}

function syncComposerRunActionClass(active) {
    if (els.inputContainer?.classList) {
        const hasRunActionClass = els.inputContainer.classList.contains(COMPOSER_RUN_ACTION_ACTIVE_CLASS);
        if (hasRunActionClass !== active) {
            els.inputContainer.classList.toggle(COMPOSER_RUN_ACTION_ACTIVE_CLASS, active);
        }
    }
}

function toggleClassIfChanged(element, className, enabled) {
    const hasClass = element.classList.contains(className);
    if (hasClass !== enabled) {
        element.classList.toggle(className, enabled);
    }
}

function setDatasetValueIfChanged(element, key, value) {
    if (element.dataset[key] !== value) {
        element.dataset[key] = value;
    }
}

function setVoiceLevelProperty(button, value) {
    if (button.style.getPropertyValue('--voice-level') !== value) {
        button.style.setProperty('--voice-level', value);
    }
}

function isVisibleRunControl(element) {
    if (!element || element.hidden === true) {
        return false;
    }
    if (element.style?.display === 'none') {
        return false;
    }
    if (typeof window !== 'undefined' && typeof window.getComputedStyle === 'function') {
        const style = window.getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }
    return true;
}

function resolveButtonTitle() {
    if (!hasVoiceRuntimeSupport()) {
        return t('voice.unsupported_title');
    }
    if (!isVoiceInputConfigured()) {
        return t('voice.configure_title');
    }
    return t('voice.input_title');
}

function resolveUnavailableMessage() {
    if (!hasVoiceRuntimeSupport()) {
        return t('voice.unsupported_message');
    }
    if (!isVoiceInputConfigured()) {
        return t('voice.configure_message');
    }
    return t('voice.start_failed_message');
}

function resolveVoiceStatusText(state) {
    if (state === VOICE_STATES.STARTING || state === VOICE_STATES.CONNECTING) return t('voice.status_connecting');
    if (state === VOICE_STATES.TRANSCRIBING) return t('voice.status_transcribing');
    if (state === VOICE_STATES.ERROR) return t('voice.status_error');
    return t('voice.status_listening');
}

function updateVoiceLevel(session, inputData) {
    let sum = 0;
    for (let index = 0; index < inputData.length; index += 1) {
        sum += inputData[index] * inputData[index];
    }
    const rms = Math.sqrt(sum / Math.max(1, inputData.length));
    updateVoiceMeterLevel(session, Math.min(1, rms * 8));
}

function updateVoiceMeterLevel(session, level) {
    if (!isCurrentSession(session)) return;
    const normalizedLevel = Math.max(0, Math.min(1, level));
    updateVoiceSilenceState(session, normalizedLevel);
    session.pendingLevel = normalizedLevel;
    if (session.levelTimer || session.levelFrame) return;
    const now = performance.now();
    const elapsed = now - session.lastLevelUiAt;
    const delay = Math.max(0, VOICE_LEVEL_UI_MIN_INTERVAL_MS - elapsed);
    if (delay > 0) {
        session.levelTimer = window.setTimeout(() => {
            session.levelTimer = null;
            requestVoiceLevelFrame(session);
        }, delay);
        return;
    }
    requestVoiceLevelFrame(session);
}

function requestVoiceLevelFrame(session) {
    if (session.levelFrame) return;
    session.levelFrame = window.requestAnimationFrame(() => {
        session.levelFrame = null;
        renderVoiceMeterLevel(session);
    });
}

function renderVoiceMeterLevel(session) {
    if (!isCurrentSession(session)) return;
    session.lastLevelUiAt = performance.now();
    const normalizedLevel = Math.max(0, Math.min(1, session.pendingLevel));
    const button = els.voiceInputBtn;
    if (!button) return;
    setVoiceLevelProperty(button, normalizedLevel.toFixed(2));
    toggleClassIfChanged(button, 'is-speaking', session.serverSpeechActive || normalizedLevel >= VOICE_SILENCE_LEVEL_THRESHOLD);
}

function updateVoiceSilenceState(session, level) {
    if (!isCurrentSession(session) || session.stopping) {
        return;
    }
    const now = Date.now();
    if (level >= VOICE_SILENCE_LEVEL_THRESHOLD) {
        markVoiceSpeechDetected(session, now);
        return;
    }
    if (voiceState !== VOICE_STATES.LISTENING || !session.hasDetectedSpeech || session.suppressSilenceDetection) {
        return;
    }
    if (now - session.lastSpeechAt >= VOICE_SILENCE_AUTO_STOP_MS) {
        void stopVoiceInput({ keepText: true });
    }
}

function markVoiceSpeechDetected(session, timestamp = Date.now()) {
    session.hasDetectedSpeech = true;
    session.lastSpeechAt = timestamp;
    if (session.noSpeechTimer) {
        window.clearTimeout(session.noSpeechTimer);
        session.noSpeechTimer = null;
    }
    if (session.silenceTimer) {
        window.clearTimeout(session.silenceTimer);
        session.silenceTimer = null;
    }
}

function armNoSpeechTimer(session) {
    if (session.suppressSilenceDetection) {
        return;
    }
    if (session.noSpeechTimer) {
        window.clearTimeout(session.noSpeechTimer);
    }
    session.noSpeechTimer = window.setTimeout(() => {
        if (!isCurrentSession(session) || session.stopping || session.hasDetectedSpeech || voiceState !== VOICE_STATES.LISTENING) {
            return;
        }
        void stopVoiceInput({ keepText: true });
    }, VOICE_NO_SPEECH_TIMEOUT_MS);
}

function scheduleVoiceAutoStop(session, delayMs) {
    if (!isCurrentSession(session) || session.stopping || session.suppressSilenceDetection) {
        return;
    }
    if (session.silenceTimer) {
        window.clearTimeout(session.silenceTimer);
    }
    session.silenceTimer = window.setTimeout(() => {
        if (!isCurrentSession(session) || session.stopping) {
            return;
        }
        void stopVoiceInput({ keepText: true });
    }, delayMs);
}

function armVoiceFinalizeTimer(session) {
    if (session.finalizeTimer) {
        window.clearTimeout(session.finalizeTimer);
    }
    session.finalizeTimer = window.setTimeout(() => {
        if (isCurrentSession(session) && session.stopping) {
            void finalizeVoiceSession(session, { closeSocket: true });
        }
    }, VOICE_FINALIZE_TIMEOUT_MS);
}

function handleVoiceSpaceKeydown(event) {
    if (!isVoiceSpaceEvent(event)) return;
    if (!voiceFlow && !activeSession && !canStartVoiceInput()) {
        return;
    }
    event.preventDefault();
    if (spacePressState || voiceFlow || activeSession) {
        return;
    }
    const input = els.promptInput;
    const startedFromPrompt = event.target === input;
    focusPromptInputForVoice(input);
    spacePressState = {
        startedVoice: false,
        startedFromPrompt,
        selectionStart: Number.isFinite(input?.selectionStart) ? input.selectionStart : 0,
        selectionEnd: Number.isFinite(input?.selectionEnd) ? input.selectionEnd : 0,
        timer: window.setTimeout(() => {
            if (!spacePressState || voiceFlow || activeSession) return;
            spacePressState.startedVoice = true;
            void startVoiceInput({ suppressSilenceDetection: true });
        }, VOICE_SPACE_HOLD_MS),
    };
}

function handleVoiceSpaceKeyup(event) {
    if (!spacePressState || !isVoiceSpaceEvent(event)) return;
    event.preventDefault();
    const state = spacePressState;
    clearVoiceSpacePress();
    if (state.startedVoice) {
        releaseVoiceSpaceSilenceSuppression();
        return;
    }
    if (state.startedFromPrompt && !activeSession) {
        insertPromptSpace(state.selectionStart, state.selectionEnd);
    }
}

function clearVoiceSpacePress() {
    if (!spacePressState) return;
    window.clearTimeout(spacePressState.timer);
    if (spacePressState.startedVoice) {
        releaseVoiceSpaceSilenceSuppression();
    }
    spacePressState = null;
}

function isVoiceSpaceEvent(event) {
    if (!event
        || (event.key !== ' ' && event.code !== 'Space')
        || event.ctrlKey === true
        || event.altKey === true
        || event.metaKey === true
        || event.shiftKey === true
        || event.isComposing === true) {
        return false;
    }
    if (!els.promptInput) {
        return false;
    }
    if (event.target === els.promptInput) {
        return true;
    }
    return !isEditableVoiceSpaceTarget(event.target);
}

function isEditableVoiceSpaceTarget(target) {
    if (!target || target === document.body || target === document.documentElement) {
        return false;
    }
    if (target.isContentEditable === true) {
        return true;
    }
    const tagName = String(target.tagName || '').toUpperCase();
    return ['A', 'BUTTON', 'INPUT', 'SELECT', 'SUMMARY', 'TEXTAREA'].includes(tagName);
}

function focusPromptInputForVoice(input) {
    if (!input || document.activeElement === input) {
        return;
    }
    input.focus({ preventScroll: true });
    const cursor = Number.isFinite(input.selectionEnd) ? input.selectionEnd : input.value.length;
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
}

function releaseVoiceSpaceSilenceSuppression() {
    const session = activeSession;
    if (!session || !session.suppressSilenceDetection) {
        return;
    }
    session.suppressSilenceDetection = false;
    if (!session.hasDetectedSpeech && voiceState === VOICE_STATES.LISTENING) {
        armNoSpeechTimer(session);
    }
}

function insertPromptSpace(selectionStart, selectionEnd) {
    const input = els.promptInput;
    if (!input) return;
    const value = String(input.value || '');
    const start = Number.isFinite(input.selectionStart) ? input.selectionStart : selectionStart;
    const end = Number.isFinite(input.selectionEnd) ? input.selectionEnd : selectionEnd;
    const nextStart = Math.max(0, Math.min(value.length, start));
    const nextEnd = Math.max(nextStart, Math.min(value.length, end));
    input.value = `${value.slice(0, nextStart)} ${value.slice(nextEnd)}`;
    const cursor = nextStart + 1;
    input.selectionStart = cursor;
    input.selectionEnd = cursor;
    input.dispatchEvent(new Event('input', { bubbles: true }));
}

function isCurrentFlow(flow) {
    return Boolean(flow && voiceFlow && flow.token === voiceFlow.token);
}

function isCurrentSession(session) {
    return Boolean(
        session
        && activeSession === session
        && voiceFlow
        && session.token === voiceFlow.token
        && !session.closed,
    );
}

function joinVoiceText(left, right) {
    const first = String(left || '').trim();
    const second = String(right || '').trim();
    if (!first) return second;
    if (!second) return first;
    return `${first} ${second}`;
}

function downsample(input, sourceRate, targetRate) {
    if (sourceRate === targetRate) {
        return input;
    }
    const ratio = sourceRate / targetRate;
    const outputLength = Math.max(1, Math.round(input.length / ratio));
    const output = new Float32Array(outputLength);
    for (let index = 0; index < outputLength; index += 1) {
        output[index] = input[Math.min(input.length - 1, Math.floor(index * ratio))];
    }
    return output;
}

function floatToPcm16(input) {
    const buffer = new ArrayBuffer(input.length * 2);
    const view = new DataView(buffer);
    for (let index = 0; index < input.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, input[index]));
        view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return buffer;
}

function resolveAudioContextCtor() {
    return window.AudioContext || window.webkitAudioContext || null;
}

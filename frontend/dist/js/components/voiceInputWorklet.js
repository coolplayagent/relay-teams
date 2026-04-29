class RelayTeamsVoiceInputProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        const targetSampleRate = Number(options?.processorOptions?.targetSampleRate);
        this.targetSampleRate = Number.isFinite(targetSampleRate) && targetSampleRate > 0
            ? targetSampleRate
            : 16000;
        this.sourceSampleRate = sampleRate;
        this.minBufferedFrames = Math.max(128, Math.round(this.sourceSampleRate * 0.08));
        this.chunks = [];
        this.bufferedFrames = 0;
        this.maxLevel = 0;
        this.port.onmessage = event => {
            const payload = event.data || {};
            if (payload.type !== 'configure') {
                return;
            }
            const nextTargetSampleRate = Number(payload.targetSampleRate);
            if (Number.isFinite(nextTargetSampleRate) && nextTargetSampleRate > 0) {
                this.targetSampleRate = nextTargetSampleRate;
            }
        };
    }

    process(inputs) {
        const channel = inputs[0]?.[0];
        if (!channel || channel.length === 0) {
            return true;
        }
        let sum = 0;
        for (let index = 0; index < channel.length; index += 1) {
            sum += channel[index] * channel[index];
        }
        const level = Math.min(1, Math.sqrt(sum / channel.length) * 8);
        this.maxLevel = Math.max(this.maxLevel, level);
        this.chunks.push(new Float32Array(channel));
        this.bufferedFrames += channel.length;
        if (this.bufferedFrames < this.minBufferedFrames) {
            return true;
        }
        const merged = new Float32Array(this.bufferedFrames);
        let offset = 0;
        for (const chunk of this.chunks) {
            merged.set(chunk, offset);
            offset += chunk.length;
        }
        this.chunks = [];
        this.bufferedFrames = 0;
        const pcm = this.floatToPcm16(this.downsample(merged));
        this.port.postMessage({ type: 'audio', audio: pcm.buffer, level: this.maxLevel }, [pcm.buffer]);
        this.maxLevel = 0;
        return true;
    }

    downsample(input) {
        if (this.sourceSampleRate === this.targetSampleRate) {
            return input;
        }
        const ratio = this.sourceSampleRate / this.targetSampleRate;
        const outputLength = Math.max(1, Math.round(input.length / ratio));
        const output = new Float32Array(outputLength);
        for (let index = 0; index < outputLength; index += 1) {
            output[index] = input[Math.min(input.length - 1, Math.floor(index * ratio))];
        }
        return output;
    }

    floatToPcm16(input) {
        const pcm = new Int16Array(input.length);
        for (let index = 0; index < input.length; index += 1) {
            const sample = Math.max(-1, Math.min(1, input[index]));
            pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        }
        return pcm;
    }
}

registerProcessor('relay-teams-voice-input', RelayTeamsVoiceInputProcessor);

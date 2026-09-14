// Everything that touches raw audio, kept out of React so it can be replayed and tested on its own.
// The gate below was written twice before it worked; `vad_sim.js` at the project root replays it.

export const OUT_RATE = 16_000;
export const BLOCK = 2048; // ~46 ms at 44.1 kHz: fine enough to catch a short word
export const SILENCE_MS = 700; // pause that ends your turn
export const MIN_SPEECH_MS = 200; // shorter than this is a cough, not a sentence
export const MAX_TURN_MS = 30_000; // a stuck gate must not upload minutes of audio
export const PREROLL_MS = 400; // kept before speech is detected, so the first word survives
const FLOOR_WIN = 40; // ~1.8 s of history behind the noise-floor estimate

export type Sensitivity = "high" | "normal" | "low";

// Two thresholds, not one. Opening a turn should take a clear signal; continuing it should not, or the
// gaps between words end the turn early.
export const SENSITIVITY: Record<Sensitivity, { start: number; keep: number; abs: number }> = {
  high: { start: 1.9, keep: 1.35, abs: 0.0006 },
  normal: { start: 2.6, keep: 1.7, abs: 0.001 },
  low: { start: 4.0, keep: 2.4, abs: 0.002 },
};

export type GateEvent =
  | { type: "level"; rms: number; normalised: number; speaking: boolean }
  | { type: "open" }
  | { type: "turn"; blob: Blob; seconds: number };

/** The speech gate: feed it blocks of PCM, it tells you when a turn started and hands you the audio. */
export function createGate(sampleRate: number, onEvent: (e: GateEvent) => void) {
  let sens = SENSITIVITY.normal;
  let floorHist: number[] = [];
  let noiseFloor = 0.003;
  let startT = 0.02;
  let speaking = false;
  let speechMs = 0;
  let silenceMs = 0;
  let preroll: Float32Array[] = [];
  let prerollFrames = 0;
  let utterance: Float32Array[] = [];
  let utteranceFrames = 0;
  let paused = false;

  const reset = () => {
    speaking = false;
    speechMs = silenceMs = 0;
    preroll = [];
    utterance = [];
    prerollFrames = utteranceFrames = 0;
  };

  return {
    setSensitivity(s: Sensitivity) {
      sens = SENSITIVITY[s] ?? SENSITIVITY.normal;
    },
    /** While the reply plays, keep measuring levels but never open a turn — it must not answer itself. */
    setPaused(p: boolean) {
      paused = p;
      if (p) reset();
    },
    get threshold() {
      return startT;
    },
    get floor() {
      return noiseFloor;
    },
    push(buf: Float32Array) {
      const ms = (buf.length / sampleRate) * 1000;
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);

      // The floor is the 20th percentile of recent history: what the room sounds like when nobody is
      // talking. A running average was tried first and is wrong — too slow in the first second, and
      // dragged upward by the very speech trying to clear it.
      floorHist.push(rms);
      if (floorHist.length > FLOOR_WIN) floorHist.shift();
      const sorted = [...floorHist].sort((a, b) => a - b);
      noiseFloor = Math.max(0.0008, sorted[Math.floor(sorted.length * 0.2)] ?? 0.003);
      startT = noiseFloor * sens.start + sens.abs;
      const keepT = noiseFloor * sens.keep + sens.abs * 0.5;

      onEvent({ type: "level", rms, normalised: rms / (noiseFloor * 9), speaking });
      if (paused) return;

      const copy = new Float32Array(buf);
      if (!speaking) {
        preroll.push(copy);
        prerollFrames += copy.length;
        const maxFrames = (PREROLL_MS / 1000) * sampleRate;
        while (prerollFrames > maxFrames) prerollFrames -= preroll.shift()!.length;
        // Decay rather than reset: speech is not continuous, and zeroing this on the first quiet block
        // meant only an uninterrupted shout could ever open the gate.
        if (rms > startT) speechMs += ms;
        else speechMs = Math.max(0, speechMs - ms * 0.6);
        if (speechMs >= MIN_SPEECH_MS) {
          speaking = true;
          silenceMs = 0;
          utterance = preroll.slice();
          utteranceFrames = prerollFrames;
          preroll = [];
          prerollFrames = 0;
          onEvent({ type: "open" });
        }
        return;
      }

      utterance.push(copy);
      utteranceFrames += copy.length;
      silenceMs = rms > keepT ? 0 : silenceMs + ms;
      const turnMs = (utteranceFrames / sampleRate) * 1000;
      if (silenceMs >= SILENCE_MS || turnMs >= MAX_TURN_MS) {
        const blocks = utterance;
        const frames = utteranceFrames;
        reset();
        if ((frames / sampleRate) * 1000 >= MIN_SPEECH_MS) {
          const { blob, seconds } = encodeWav(blocks, frames, sampleRate);
          onEvent({ type: "turn", blob, seconds });
        }
      }
    },
  };
}

/** Downsample to 16 kHz mono and wrap in a WAV header, so the server never decodes a container. */
export function encodeWav(blocks: Float32Array[], frames: number, inRate: number) {
  const joined = new Float32Array(frames);
  let at = 0;
  for (const b of blocks) {
    joined.set(b, at);
    at += b.length;
  }
  const ratio = inRate / OUT_RATE;
  const n = Math.floor(joined.length / ratio);
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, joined[Math.floor(i * ratio)]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  const head = new ArrayBuffer(44);
  const dv = new DataView(head);
  const str = (o: number, t: string) => [...t].forEach((c, i) => dv.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  dv.setUint32(4, 36 + pcm.byteLength, true);
  str(8, "WAVEfmt ");
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true);
  dv.setUint32(24, OUT_RATE, true);
  dv.setUint32(28, OUT_RATE * 2, true);
  dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true);
  str(36, "data");
  dv.setUint32(40, pcm.byteLength, true);
  return { blob: new Blob([head, pcm], { type: "audio/wav" }), seconds: n / OUT_RATE };
}

/**
 * Amplitude per ~46 ms, read straight from WAV bytes.
 *
 * The reply is played by a plain <audio> element and deliberately never routed through WebAudio: on
 * iOS that path obeys the hardware silent switch and outputs nothing from a suspended context, which
 * made replies silent on phones while transcription kept working. So the picture comes from the file.
 */
export function envelopeOf(buf: ArrayBuffer): { env: number[]; seconds: number } | null {
  const dv = new DataView(buf);
  if (dv.getUint32(0, false) !== 0x52494646) return null; // "RIFF"
  let off = 12;
  let rate = 24_000;
  let channels = 1;
  let bits = 16;
  let dataAt = -1;
  let dataLen = 0;
  while (off + 8 <= dv.byteLength) {
    const id = String.fromCharCode(dv.getUint8(off), dv.getUint8(off + 1), dv.getUint8(off + 2), dv.getUint8(off + 3));
    const size = dv.getUint32(off + 4, true);
    if (id === "fmt ") {
      channels = dv.getUint16(off + 10, true);
      rate = dv.getUint32(off + 12, true);
      bits = dv.getUint16(off + 22, true);
    } else if (id === "data") {
      dataAt = off + 8;
      dataLen = size;
      break;
    }
    off += 8 + size + (size % 2);
  }
  if (dataAt < 0 || bits !== 16) return null;
  const samples = new Int16Array(buf, dataAt, Math.floor(dataLen / 2));
  const step = Math.max(1, Math.round(rate * 0.046) * channels);
  const env: number[] = [];
  for (let i = 0; i < samples.length; i += step) {
    let sum = 0;
    let n = 0;
    for (let j = i; j < i + step && j < samples.length; j += channels) {
      const v = samples[j] / 32768;
      sum += v * v;
      n++;
    }
    env.push(n ? Math.sqrt(sum / n) : 0);
  }
  return { env, seconds: samples.length / channels / rate };
}

/** A moment of silence played inside a tap is what marks an element as user-permitted on iOS. */
export const SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=";

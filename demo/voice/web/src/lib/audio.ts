// Everything that touches raw audio, kept out of React so it can be replayed and tested on its own.
// The gate below was written twice before it worked; `vad_sim.js` at the project root replays it.

export const OUT_RATE = 16_000;
export const BLOCK = 2048; // ~46 ms at 44.1 kHz: fine enough to catch a short word
export const SILENCE_MS = 700; // default pause that ends your turn; adjustable per conversation
export const MIN_SPEECH_MS = 200; // shorter than this is a cough, not a sentence
export const MAX_TURN_MS = 30_000; // a stuck gate must not upload minutes of audio
export const PREROLL_MS = 400; // kept before speech is detected, so the first word survives
const FLOOR_WIN = 40; // ~1.8 s of history behind the noise-floor estimate
const BARGE_MS = 260; // talking over the reply takes a little more than starting a turn does
const BARGE_MARGIN = 2.2; // how far above the reply's expected echo your voice has to be

/**
 * listen — normal: open a turn when you speak.
 * paused — measure levels only; used while the reply plays when interrupting is off.
 * barge  — the reply is playing and you may talk over it. The gate is told how loud the reply's own echo is
 *          expected to be right now and only opens for sound clearly above that.
 */
export type GateMode = "listen" | "paused" | "barge";

export type Sensitivity = "high" | "normal" | "low";

// Two thresholds, not one. Opening a turn should take a clear signal; continuing it should not, or the
// gaps between words end the turn early.
export const SENSITIVITY: Record<Sensitivity, { start: number; keep: number; abs: number }> = {
  high: { start: 1.9, keep: 1.35, abs: 0.0006 },
  normal: { start: 2.6, keep: 1.7, abs: 0.001 },
  low: { start: 4.0, keep: 2.4, abs: 0.002 },
};

export const PAUSE_MS = 300; // a pause worth asking Smart Turn about
const RESUME_MS = 120; // voice after a pause that means you carried on
const SMART_MAX_SILENCE_MS = 2500; // smart mode: an utterance nobody committed closes itself after this

/**
 * pause  — "turn" fires after a fixed silence (the original behaviour).
 * smart  — no "turn" event; the page asks Smart Turn at every "pause" and commits the utterance itself.
 */
export type EndMode = "pause" | "smart";

export type GateEvent =
  | { type: "level"; rms: number; normalised: number; speaking: boolean }
  | { type: "open" }
  | { type: "pause"; blob: Blob; seconds: number }
  | { type: "resume" }
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
  let mode: GateMode = "listen";
  let endMode: EndMode = "pause";
  let voicedMs = 0;
  let pauseSent = false;
  let resumeMs = 0;
  let silenceLimit = SILENCE_MS;
  let echo: () => number = () => 0;

  const reset = () => {
    speaking = false;
    speechMs = silenceMs = voicedMs = resumeMs = 0;
    pauseSent = false;
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
      this.setMode(p ? "paused" : "listen");
    },
    setMode(m: GateMode) {
      if (m === mode) return;
      // Never throw away a turn that is already open: in barge mode that is you, talking over the reply.
      if (m === "paused" || !speaking) reset();
      mode = m;
    },
    get mode() {
      return mode;
    },
    setSilence(ms: number) {
      silenceLimit = ms;
    },
    setEndMode(m: EndMode) {
      endMode = m;
    },
    /** Close the current utterance: what follows is a new one. Used once a smart turn can no longer reopen. */
    commit() {
      if (speaking) reset();
    },
    /** How much of the open utterance was actually voice, in ms — a backchannel is short. */
    get voicedMs() {
      return voicedMs;
    },
    get speaking() {
      return speaking;
    },
    /** Expected echo of the reply at this instant, in the same units as the microphone's RMS. */
    setEcho(fn: () => number) {
      echo = fn;
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
      if (mode === "paused") return;
      const barge = mode === "barge";
      const openAt = barge ? startT + echo() * BARGE_MARGIN : startT;

      const copy = new Float32Array(buf);
      if (!speaking) {
        preroll.push(copy);
        prerollFrames += copy.length;
        const maxFrames = (PREROLL_MS / 1000) * sampleRate;
        while (prerollFrames > maxFrames) prerollFrames -= preroll.shift()!.length;
        // Decay rather than reset: speech is not continuous, and zeroing this on the first quiet block
        // meant only an uninterrupted shout could ever open the gate.
        if (rms > openAt) speechMs += ms;
        else speechMs = Math.max(0, speechMs - ms * 0.6);
        if (speechMs >= (barge ? BARGE_MS : MIN_SPEECH_MS)) {
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
      const voiced = rms > keepT + (barge ? echo() * BARGE_MARGIN * 0.6 : 0);
      silenceMs = voiced ? 0 : silenceMs + ms;
      if (voiced) voicedMs += ms;
      const turnMs = (utteranceFrames / sampleRate) * 1000;

      // A pause long enough to ask about. Sent once per pause; the utterance stays open.
      if (!pauseSent && silenceMs >= PAUSE_MS) {
        pauseSent = true;
        resumeMs = 0;
        const { blob, seconds } = encodeWav(utterance, utteranceFrames, sampleRate);
        onEvent({ type: "pause", blob, seconds });
      } else if (pauseSent) {
        // Carrying on after a pause needs a clear signal, not a breath.
        resumeMs = rms > openAt ? resumeMs + ms : Math.max(0, resumeMs - ms);
        if (resumeMs >= RESUME_MS) {
          pauseSent = false;
          resumeMs = 0;
          onEvent({ type: "resume" });
        }
      }

      if (endMode === "smart") {
        if (silenceMs >= SMART_MAX_SILENCE_MS || turnMs >= MAX_TURN_MS) reset();
        return;
      }
      if (silenceMs >= silenceLimit || turnMs >= MAX_TURN_MS) {
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

/** Amplitude per ~46 ms from any decoded clip (the replies arrive as MP3, so they are decoded first). */
export function envelopeOfBuffer(b: AudioBuffer): number[] {
  const data = b.getChannelData(0);
  const step = Math.max(1, Math.round(b.sampleRate * 0.046));
  const env: number[] = [];
  for (let i = 0; i < data.length; i += step) {
    let sum = 0;
    const end = Math.min(data.length, i + step);
    for (let j = i; j < end; j++) sum += data[j] * data[j];
    env.push(Math.sqrt(sum / Math.max(1, end - i)));
  }
  return env;
}

/**
 * A short rising two-note chime, made here rather than fetched. It says "listening" when a conversation
 * starts, and — played through the same element as the replies — it is how much of the Mac's own voice
 * comes back into the microphone is measured before the first reply rather than during it.
 */
export function chime(): { url: string; env: number[] } {
  const rate = 24_000;
  const notes = [
    { f: 660, at: 0, len: 0.22 },
    { f: 880, at: 0.16, len: 0.42 },
  ];
  const n = Math.round(rate * 0.62);
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / rate;
    let v = 0;
    for (const { f, at, len } of notes) {
      const u = t - at;
      if (u < 0 || u > len) continue;
      const attack = Math.min(1, u / 0.012);
      const decay = Math.exp(-u * 5.5);
      v += Math.sin(2 * Math.PI * f * u) * attack * decay * 0.32 + Math.sin(4 * Math.PI * f * u) * attack * decay * 0.05;
    }
    pcm[i] = Math.max(-1, Math.min(1, v)) * 0x7fff;
  }
  const head = new ArrayBuffer(44);
  const dv = new DataView(head);
  const str = (o: number, t: string) => [...t].forEach((c, k) => dv.setUint8(o + k, c.charCodeAt(0)));
  str(0, "RIFF");
  dv.setUint32(4, 36 + pcm.byteLength, true);
  str(8, "WAVEfmt ");
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true);
  dv.setUint32(24, rate, true);
  dv.setUint32(28, rate * 2, true);
  dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true);
  str(36, "data");
  dv.setUint32(40, pcm.byteLength, true);
  const shape = envelopeOf(concat(head, pcm.buffer));
  return { url: URL.createObjectURL(new Blob([head, pcm], { type: "audio/wav" })), env: shape?.env ?? [] };
}

function concat(a: ArrayBuffer, b: ArrayBuffer): ArrayBuffer {
  const out = new Uint8Array(a.byteLength + b.byteLength);
  out.set(new Uint8Array(a), 0);
  out.set(new Uint8Array(b), a.byteLength);
  return out.buffer;
}

/** A moment of silence played inside a tap is what marks an element as user-permitted on iOS. */
export const SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=";

import { envelopeOfBuffer } from "./audio";

// Plays reply sentences and fillers one after another on a single <audio> element, and stops dead when
// you talk over it.
//
// One element, reused, is deliberate: iOS only lets an element play without a tap once a tap has played it,
// and the reply must not be routed through WebAudio (see envelopeOf in audio.ts). The shape of each clip is
// decoded separately so the ring can draw it and the speech gate can predict its echo.

export type Clip = {
  kind: "reply" | "filler" | "chime";
  url: string;
  env: number[] | null;
  text: string;
  i?: number;
  /** Where the voice starts and stops inside the file, in seconds (see speechBounds). */
  start?: number;
  end?: number;
  /** When the first word is heard, in seconds into the file. */
  voiceAt?: number;
};

const STEP = 0.046;

/**
 * Where the voice actually is in a clip. Kokoro pads every clip with ~0.4 s of silence before the first word
 * and 0.5-0.7 s after the last; played as-is that put 0.4 s of nothing in front of every reply, made a
 * one-word filler last 1.3-1.7 s, and doubled the gap between sentences. Keep ~50 ms before and ~140 ms after.
 */
export function speechBounds(env: number[] | null): { start: number; end: number; voiceAt: number } | undefined {
  if (!env?.length) return undefined;
  const peak = Math.max(...env);
  if (peak <= 0) return undefined;
  const floor = peak * 0.06;
  const first = env.findIndex((v) => v > floor);
  let last = env.length - 1;
  while (last > first && env[last] <= floor) last--;
  return { start: Math.max(0, (first - 1) * STEP), end: Math.min(env.length, last + 4) * STEP, voiceAt: first * STEP };
}

export function createSpeaker(el: HTMLAudioElement) {
  const queue: Clip[] = [];
  let current: Clip | null = null;
  let generation = 0;
  let ducked = false;
  let endWatch = 0;
  let pausedForDuck = false;
  const DUCK_VOLUME = 0.3;
  let onStart: (c: Clip) => void = () => undefined;
  let onIdle: () => void = () => undefined;
  let onError: (msg: string) => void = () => undefined;

  const next = () => {
    const clip = queue.shift();
    if (!clip) {
      current = null;
      onIdle();
      return;
    }
    current = clip;
    const gen = generation;
    el.src = clip.url;
    el.volume = ducked ? DUCK_VOLUME : 1;
    window.clearInterval(endWatch);
    // Stop at the end of the voice rather than the end of the file. `ended` is still the fallback.
    if (clip.end) {
      endWatch = window.setInterval(() => {
        if (gen !== generation) return window.clearInterval(endWatch);
        if (!el.paused && el.currentTime >= clip.end!) {
          window.clearInterval(endWatch);
          el.pause();
          next();
        }
      }, 25);
    }
    el.onended = () => {
      window.clearInterval(endWatch);
      if (gen === generation) next();
    };
    el.onerror = () => {
      if (gen !== generation) return;
      onError("A piece of the reply could not be played on this device.");
      next();
    };
    // Skip the silence in front of the voice. A seek before the metadata is known would be ignored.
    if (clip.start) {
      if (el.readyState >= 1) el.currentTime = clip.start;
      else el.addEventListener("loadedmetadata", () => gen === generation && (el.currentTime = clip.start!), { once: true });
    }
    el.play().then(
      () => gen === generation && onStart(clip),
      () => {
        if (gen !== generation) return;
        onError("This browser blocked the reply from playing. Press Start again to allow audio.");
        next();
      }
    );
  };

  return {
    enqueue(clip: Clip) {
      queue.push(clip);
      if (!current) next();
    },
    /** Cut everything, now. Returns what had started playing so the transcript can say where it stopped. */
    stop() {
      generation++;
      queue.length = 0;
      const was = current;
      current = null;
      ducked = pausedForDuck = false;
      el.onended = el.onerror = null;
      window.clearInterval(endWatch);
      el.pause();
      return was;
    },
    /**
     * Turn the reply down while we find out whether you meant to interrupt. iOS ignores `volume` on media
     * elements (it always reads back 1), so there the reply pauses instead and picks up where it left off.
     */
    duck(on: boolean) {
      if (on === ducked) return;
      ducked = on;
      if (!current) return;
      if (on) {
        el.volume = DUCK_VOLUME;
        if (el.volume > DUCK_VOLUME + 0.01) {
          pausedForDuck = true;
          el.pause();
        }
      } else {
        el.volume = 1;
        if (pausedForDuck) {
          pausedForDuck = false;
          void el.play().catch(() => undefined);
        }
      }
    },
    get ducked() {
      return ducked;
    },
    get busy() {
      return current !== null || queue.length > 0;
    },
    get playingKind() {
      return current?.kind ?? null;
    },
    /** Loudness of what is playing, ~now, 0..1. */
    level(ahead = 0) {
      if (!current?.env) return current ? 0.08 : 0;
      return current.env[Math.floor((el.currentTime + ahead) / 0.046)] ?? 0;
    },
    /** Loudest point in a short window around now — what could still be echoing in the room. */
    recentPeak(backSeconds = 0.3) {
      if (!current?.env) return current ? 0.15 : 0;
      const hi = Math.floor((el.currentTime + 0.05) / 0.046);
      const lo = Math.max(0, Math.floor((el.currentTime - backSeconds) / 0.046));
      let m = 0;
      for (let k = lo; k <= hi && k < current.env.length; k++) m = Math.max(m, current.env[k]);
      return m;
    },
    on(handlers: { start?: (c: Clip) => void; idle?: () => void; error?: (msg: string) => void }) {
      if (handlers.start) onStart = handlers.start;
      if (handlers.idle) onIdle = handlers.idle;
      if (handlers.error) onError = handlers.error;
    },
  };
}

/** Decode a clip's bytes just to measure its shape. Nothing is played through WebAudio. */
export async function shapeOf(ctx: AudioContext | null, bytes: ArrayBuffer): Promise<number[] | null> {
  if (!ctx) return null;
  try {
    return envelopeOfBuffer(await ctx.decodeAudioData(bytes.slice(0)));
  } catch {
    return null;
  }
}

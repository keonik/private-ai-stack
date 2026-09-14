import { envelopeOfBuffer } from "./audio";

// Plays reply sentences and fillers one after another on a single <audio> element, and stops dead when
// you talk over it.
//
// One element, reused, is deliberate: iOS only lets an element play without a tap once a tap has played it,
// and the reply must not be routed through WebAudio (see envelopeOf in audio.ts). The shape of each clip is
// decoded separately so the ring can draw it and the speech gate can predict its echo.

export type Clip = {
  kind: "reply" | "filler";
  url: string;
  env: number[] | null;
  text: string;
  i?: number;
};

export function createSpeaker(el: HTMLAudioElement) {
  const queue: Clip[] = [];
  let current: Clip | null = null;
  let generation = 0;
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
    el.onended = () => gen === generation && next();
    el.onerror = () => {
      if (gen !== generation) return;
      onError("A piece of the reply could not be played on this device.");
      next();
    };
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
      el.onended = el.onerror = null;
      el.pause();
      return was;
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

import { useCallback, useEffect, useRef, useState } from "react";
import {
  MicSelector,
  MicSelectorContent,
  MicSelectorEmpty,
  MicSelectorInput,
  MicSelectorItem,
  MicSelectorList,
  MicSelectorTrigger,
  MicSelectorValue,
} from "@/components/ai-elements/mic-selector";
import {
  VoiceSelector,
  VoiceSelectorContent,
  VoiceSelectorDescription,
  VoiceSelectorEmpty,
  VoiceSelectorGender,
  VoiceSelectorGroup,
  VoiceSelectorInput,
  VoiceSelectorItem,
  VoiceSelectorList,
  VoiceSelectorName,
  VoiceSelectorPreview,
  VoiceSelectorTrigger,
} from "@/components/ai-elements/voice-selector";
import {
  AudioPlayer,
  AudioPlayerControlBar,
  AudioPlayerElement,
  AudioPlayerPlayButton,
  AudioPlayerTimeDisplay,
  AudioPlayerTimeRange,
} from "@/components/ai-elements/audio-player";
import { Transcription, TranscriptionSegment } from "@/components/ai-elements/transcription";
import { Button } from "@/components/ui/button";
import { Ring, type Phase } from "@/components/Ring";
import { SILENT_WAV, chime, thinkingTone, createGate, BLOCK, PAUSE_MS, type GateEvent, type Sensitivity } from "@/lib/audio";
import { createSpeaker, shapeOf, speechBounds, type Clip } from "@/lib/speaker";

type Segment = { text: string; startSecond: number; endSecond: number };
type Turn = {
  id: number;
  who: "you" | "mac";
  text: string;
  meta: string;
  audio?: string;
  segments?: Segment[];
};
type ModelChoice = { id: string; label: string; note: string };

// The switches the lab panel flips. Each one is a separate experiment, so any combination is allowed.
type ReplyMode = "eager" | "stream" | "whole";
// Canned spoken fillers turned out to be the goofiest thing in the demo: five phrases drawn at random,
// mismatched to the question ("Good question." after "what time is it") and repeating across a conversation.
// They were worth it when a reply took 2.8 s; Kokoro now starts at ~2.1 s, so one fired on nearly every turn.
// Default off; a quiet tone covers a wait without pretending to be speech; words only for the slow engines.
type FillerMode = "off" | "tone" | "words";
type BargeMode = "smart" | "instant" | "off";
type TurnMode = "smart" | "pause";
type TranscriptMode = "spoken" | "written";
type Lab = {
  reply: ReplyMode;
  barge: BargeMode;
  fillers: FillerMode;
  endOfTurn: number;
  turn: TurnMode;
  transcript: TranscriptMode;
  chime: boolean;
};
const LAB_DEFAULT: Lab = {
  reply: "eager", barge: "smart", fillers: "off", endOfTurn: 700, turn: "smart", transcript: "spoken", chime: true,
};
const FILLER_AFTER: Record<Exclude<FillerMode, "off">, number> = { tone: 600, words: 1200 };

// Smart Turn, after Hugging Face speech-to-speech: a turn that sounds finished starts at once and can still be
// reopened for a moment; one that does not waits on the server and stays reopenable for longer.
const REOPEN_COMPLETE_MS = 800;
const REOPEN_INCOMPLETE_MS = 2000;
// Talking over the reply: a sound this long is an interruption without asking. Counted from when the gate
// opened, which already took ~260 ms of voice, so this is ~0.9 s of voice in all.
const LONG_VOICED_MS = 650;
const BARGE_DECIDE_MS = 2500; // no verdict by then: assume you meant it
const WATCHDOG_MS = 12_000; // the Qwen runtime's figure for "the model never started answering"; only counts
                            // while nothing is being said — see kick()

/** One row of the latency table. Times are seconds from the moment the gate decided you had finished. */
type Run = {
  id: number;
  reply: ReplyMode;
  model: string;
  engine: string;
  silence: number;
  turnMode: TurnMode;
  barge: BargeMode;
  fillers: FillerMode;
  heard?: number;
  firstWords?: number;
  firstSound?: number;
  filler?: number;
  total?: number;
  turnP?: number | null;
  note?: string;
  outcome?: "done" | "interrupted" | "continued" | "reopened" | "ignored" | "timeout" | "error";
};

/** Something you said while the Mac was talking, until we know whether it was "mm-hmm" or "stop". */
type Barge = {
  cur: Current;
  checkId: number;
  checking: boolean;
  verdict: "" | "ignored";
  blob?: Blob;
  seconds?: number;
  pendingTurn?: { blob: Blob; seconds: number };
  timer: number;
};

/** The turn in flight: enough to cancel it cleanly and to say how far it got. */
type Current = {
  ctl: AbortController;
  tEnd: number;
  runId: number;
  youId: number;
  macId: number;
  heard: string;
  question: string;
  texts: Record<number, string>;
  spoken: string[];
  written: string;
  replyQueued: boolean;
  replyStarted: boolean;
  fillerTimer: number;
  speculative: boolean;
  committed: boolean;
  commitTimer: number;
  watchdog: number;
  shown: Set<number>;
};
type VoiceChoice = { id: string; label: string; language: string; gender: string };
type EngineChoice = { id: string; label: string; note: string; default: string };

const BARS = 72;
const remembered = (k: string) => {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
};
const remember = (k: string, v: string) => {
  try {
    localStorage.setItem(k, v);
  } catch {
    /* private browsing; the choice simply will not persist */
  }
};

// A tester pass arrives once as ?pass=… in a link, is kept in this browser, and is removed from the
// address bar straight away so it does not end up in a screenshot or a shared URL.
const PASS = (() => {
  const url = new URL(window.location.href);
  const fromLink = url.searchParams.get("pass");
  if (fromLink) {
    remember("pass", fromLink);
    url.searchParams.delete("pass");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  return fromLink ?? remembered("pass") ?? "";
})();

// Every API call carries the pass if there is one; nothing else about the request changes.
const api = (input: string, init: RequestInit = {}) =>
  fetch(input, { ...init, headers: { ...(init.headers ?? {}), ...(PASS ? { "x-demo-pass": PASS } : {}) } });

const loadLab = (): Lab => {
  try {
    const saved = JSON.parse(remembered("lab") ?? "{}");
    if (!["off", "tone", "words"].includes(saved.fillers)) delete saved.fillers;  // the old quick/slow phrases
    // The first lab had a yes/no "interrupt"; keep an explicit "no" from it.
    if (saved.barge === undefined && saved.interrupt === false) saved.barge = "off";
    delete saved.interrupt;
    return { ...LAB_DEFAULT, ...saved };
  } catch {
    return LAB_DEFAULT;
  }
};

// localStorage.debug = "1" logs every speech-gate decision to the console.
const DEBUG = remembered("debug") === "1";

const b64ToBytes = (b64: string) => {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
};

export default function Voice() {
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<Phase>("asleep");
  const [status, setStatus] = useState("Not listening");
  const [error, setError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [models, setModels] = useState<ModelChoice[]>([]);
  const [voices, setVoices] = useState<VoiceChoice[]>([]);
  const [model, setModel] = useState(remembered("model") ?? "");
  const [engines, setEngines] = useState<EngineChoice[]>([]);
  const [engine, setEngine] = useState(remembered("engine") ?? "kokoro-tts");
  const [voice, setVoice] = useState("");
  const [mic, setMic] = useState<string | undefined>(remembered("mic") ?? undefined);
  const [sensitivity, setSensitivity] = useState<Sensitivity>((remembered("sens") as Sensitivity) ?? "normal");
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [playhead, setPlayhead] = useState<{ id: number; t: number } | null>(null);
  const [draft, setDraft] = useState("");
  const [tester, setTester] = useState<string | null>(null);
  const [lab, setLabState] = useState<Lab>(loadLab);
  const [runs, setRuns] = useState<Run[]>([]);
  const [echoInfo, setEchoInfo] = useState("");
  const labRef = useRef(lab);
  labRef.current = lab;
  const setLab = (patch: Partial<Lab>) =>
    setLabState((l) => {
      const next = { ...l, ...patch };
      remember("lab", JSON.stringify(next));
      return next;
    });

  const levels = useRef<number[]>(new Array(BARS).fill(0));
  const who = useRef<("idle" | "you" | "mac")[]>(new Array(BARS).fill("idle"));
  const threshold = useRef(0.2);
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodeRef = useRef<ScriptProcessorNode | null>(null);
  const gateRef = useRef<ReturnType<typeof createGate> | null>(null);
  const playerRef = useRef<HTMLAudioElement | null>(null);
  const previewRef = useRef<HTMLAudioElement | null>(null);
  const unlocked = useRef(false);
  const history = useRef<{ role: "user" | "assistant"; content: string }[]>([]);
  const nextId = useRef(1);
  const speakerRef = useRef<ReturnType<typeof createSpeaker> | null>(null);
  const curRef = useRef<Current | null>(null);
  const carry = useRef("");
  const fillerCache = useRef(
    new Map<string, { url: string; env: number[] | null; text: string; start?: number; end?: number }[]>()
  );
  const lastFiller = useRef(-1);
  const toneClip = useRef<{ url: string; env: number[] } | null>(null);
  // Mic loudness divided by the reply's loudness, sampled while the reply plays and you are quiet: how much
  // of the Mac's own voice comes back into the microphone in this room, after the browser's echo cancelling.
  const echoRatios = useRef<number[]>([]);
  const bargeRef = useRef<Barge | null>(null);
  const wakeRef = useRef<WakeLockSentinel | null>(null);
  const reported = useRef(new Set<number>());
  const gateHandler = useRef<(e: GateEvent) => void>(() => undefined);

  const push = (v: number, src: "idle" | "you" | "mac") => {
    levels.current.push(Math.max(0, Math.min(1, v)));
    who.current.push(src);
    levels.current.shift();
    who.current.shift();
  };
  const go = (p: Phase, text: string) => {
    setPhase(p);
    setStatus(text);
  };

  useEffect(() => {
    api("/api/whoami")
      .then((r) => r.json())
      .then((d) => setTester(d.pass ?? null))
      .catch(() => undefined);
    fetch("/api/models")
      .then((r) => r.json())
      .then((d) => {
        setModels(d.models);
        setModel((m) => (m && d.models.some((x: ModelChoice) => x.id === m) ? m : d.default));
      })
      .catch(() => undefined);
    fetch("/api/engines")
      .then((r) => r.json())
      .then((d) => {
        setEngines(d.engines);
        setEngine((e) => (d.engines.some((x: EngineChoice) => x.id === e) ? e : d.default));
      })
      .catch(() => undefined);
  }, []);

  // Each engine has its own voices, and the voice you chose is remembered per engine.
  useEffect(() => {
    fetch(`/api/voices?engine=${encodeURIComponent(engine)}`)
      .then((r) => r.json())
      .then((d) => {
        setVoices(d.voices);
        const saved = remembered(`voice:${d.engine}`) ?? (d.engine === "kokoro-tts" ? remembered("voice") : null);
        setVoice(saved && d.voices.some((x: VoiceChoice) => x.id === saved) ? saved : d.default);
      })
      .catch(() => undefined);
  }, [engine]);

  useEffect(() => gateRef.current?.setSensitivity(sensitivity), [sensitivity]);
  useEffect(() => gateRef.current?.setSilence(lab.endOfTurn), [lab.endOfTurn]);
  useEffect(() => gateRef.current?.setEndMode(lab.turn), [lab.turn]);

  // Each finished turn is reported once, as numbers and enums, so real sessions show up in Grafana rather
  // than only the synthetic ones in the tests. Nothing anyone said is sent.
  useEffect(() => {
    for (const r of runs) {
      if (!r.outcome || reported.current.has(r.id)) continue;
      if (r.outcome === "done" && r.firstSound === undefined && r.filler === undefined) continue; // still settling
      reported.current.add(r.id);
      const body = JSON.stringify({
        outcome: r.outcome, reply: r.reply, engine: r.engine, turn: r.turnMode, barge: r.barge, fillers: r.fillers,
        silence: r.silence, heard: r.heard, firstWords: r.firstWords, firstSound: r.firstSound, filler: r.filler,
      });
      void api("/api/lab-metrics", { method: "POST", headers: { "content-type": "application/json" }, body, keepalive: true })
        .catch(() => undefined);
    }
  }, [runs]);

  // Phones dim the screen and then cut the microphone mid-conversation. Hold the screen awake while talking;
  // browsers drop the lock whenever the tab is hidden, so take it again on return.
  useEffect(() => {
    if (!running) return;
    const hold = () => {
      if (document.visibilityState !== "visible" || (wakeRef.current && !wakeRef.current.released)) return;
      navigator.wakeLock?.request("screen").then((l) => (wakeRef.current = l)).catch(() => undefined);
    };
    hold();
    document.addEventListener("visibilitychange", hold);
    return () => {
      document.removeEventListener("visibilitychange", hold);
      void wakeRef.current?.release().catch(() => undefined);
      wakeRef.current = null;
    };
  }, [running]);
  useEffect(() => {
    if (running && lab.fillers === "words") void prepareFillers(engine, voice);
  }, [engine, voice, running, lab.fillers]);
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => {
      const r = [...echoRatios.current].sort((a, b) => a - b);
      setEchoInfo(r.length >= 10 ? `echo ${(r[Math.floor(r.length * 0.75)] * 100).toFixed(0)}% of the reply` : "echo: still measuring");
    }, 1000);
    return () => window.clearInterval(t);
  }, [running]);

  const updRun = (id: number, patch: Partial<Run>) =>
    setRuns((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const setTurn = (id: number, patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));
  const since = (cur: Current) => Math.round((performance.now() - cur.tEnd) / 10) / 100;

  const speaker = () => {
    if (!speakerRef.current && playerRef.current) {
      const sp = createSpeaker(playerRef.current);
      sp.on({
        start: (clip: Clip) => {
          const cur = curRef.current;
          if (!cur) return;
          // "Heard" means the first word, not the start of the file: whatever silence is still in front of it counts.
          const audible = Math.round((since(cur) + Math.max(0, (clip.voiceAt ?? 0) - (clip.start ?? 0))) * 100) / 100;
          if (clip.kind === "filler") {
            if (cur.replyStarted) return;
            updRun(cur.runId, { filler: audible });
            go("thinking", clip.text ? "Thinking out loud" : "Thinking");
            return;
          }
          if (!cur.replyStarted) {
            cur.replyStarted = true;
            updRun(cur.runId, { firstSound: audible });
            commitTurn(cur); // the answer is being heard; carrying on now is a new turn, not this one
          }
          cur.spoken.push(clip.text);
          if (clip.i !== undefined && !cur.shown.has(clip.i)) showText(cur, clip.i, clip.text);
          if (!speakerRef.current?.ducked) {
            go("speaking", labRef.current.barge !== "off" ? "Speaking · talk to interrupt" : "Speaking");
          }
        },
        idle: () => {
          if (curRef.current) go("thinking", "Thinking");
        },
        error: (msg) => setError(msg),
      });
      speakerRef.current = sp;
    }
    return speakerRef.current;
  };

  /**
   * Fillers are fetched once per voice, before they are needed; one that has to be downloaded is late.
   *
   * One at a time, and never while a turn is in flight. Fetching all five at once was measured pushing the
   * first answer on Qwen3-TTS from 2.6 s to 9 s: five syntheses and the reply were competing for one GPU.
   */
  const prepareFillers = async (forEngine: string, forVoice: string) => {
    const key = `${forEngine}:${forVoice}`;
    if (!forVoice || fillerCache.current.has(key)) return;
    const set: { url: string; env: number[] | null; text: string; start?: number; end?: number; voiceAt?: number }[] = [];
    fillerCache.current.set(key, set);   // the same array the player reads: it fills up as they arrive
    try {
      const { fillers } = await (await fetch("/api/fillers")).json();
      for (const [i, text] of (fillers as string[]).entries()) {
        while (curRef.current) await new Promise((r) => setTimeout(r, 250));   // the answer comes first
        const r = await api(`/api/filler?engine=${encodeURIComponent(forEngine)}&voice=${encodeURIComponent(forVoice)}&i=${i}`);
        if (!r.ok) throw new Error("filler");
        const bytes = await r.arrayBuffer();
        const env = await shapeOf(ctxRef.current, bytes);
        set.push({ text, env, ...speechBounds(env), url: URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" })) });
      }
    } catch {
      if (!set.length) fillerCache.current.delete(key);
    }
  };

  /** Add a sentence to the Mac's side of the transcript — as it is written, or only once it is heard. */
  const showText = (cur: Current, i: number, text: string) => {
    cur.shown.add(i);
    if (!text) return;
    if (!cur.macId) {
      cur.macId = nextId.current++;
      const id = cur.macId;
      setTurns((t) => [...t, { id, who: "mac", text, meta: "" }]);
    } else {
      setTurn(cur.macId, (t) => ({ text: t.text ? `${t.text} ${text}` : text }));
    }
  };

  const clearTimers = (cur: Current) => {
    window.clearTimeout(cur.fillerTimer);
    window.clearTimeout(cur.commitTimer);
    window.clearTimeout(cur.watchdog);
  };

  /** A speculative turn stops being reopenable: close the gate's utterance so new speech starts a new one. */
  const commitTurn = (cur: Current) => {
    if (cur.committed) return;
    cur.committed = true;
    window.clearTimeout(cur.commitTimer);
    const gate = gateRef.current;
    if (!gate || !cur.speculative) return;
    gate.commit();
    if (curRef.current === cur) gate.setMode(labRef.current.barge === "off" ? "paused" : "barge");
  };

  /** You carried on talking before the answer began: cancel it quietly; the gate is still recording you. */
  const reopen = (cur: Current) => {
    cur.ctl.abort();
    clearTimers(cur);
    speakerRef.current?.stop();
    curRef.current = null;
    setTurns((ts) => ts.filter((t) => t.id !== cur.youId && t.id !== cur.macId));
    updRun(cur.runId, { outcome: "reopened" });
    gateRef.current?.setMode("listen");
    go("hearing", "Hearing you · still your turn");
  };

  /** You started talking over the reply (or before it began). Stop everything and keep what matters. */
  const interrupt = (cur: Current) => {
    cur.ctl.abort();
    clearTimers(cur);
    cur.committed = true;
    speakerRef.current?.stop();
    curRef.current = null;
    if (!cur.replyStarted) {
      // Nothing of the answer was heard, so this was a pause mid-thought, not an interruption. What you said
      // is carried into the next turn and answered together with it.
      if (cur.heard) carry.current = cur.question;
      if (cur.youId) setTurn(cur.youId, (t) => ({ meta: `${t.meta} · continued` }));
      if (cur.macId) setTurns((ts) => ts.filter((t) => t.id !== cur.macId));
      updRun(cur.runId, { outcome: "continued" });
    } else {
      const said = cur.spoken.join(" ");
      history.current.push({ role: "user", content: cur.question }, { role: "assistant", content: `${said} —` });
      carry.current = "";
      if (cur.macId) setTurn(cur.macId, (t) => ({ text: `${said} —`, meta: `${t.meta} · interrupted` }));
      updRun(cur.runId, { outcome: "interrupted", total: since(cur) });
    }
    gateRef.current?.setMode("listen");
  };

  const handleTurn = useCallback(
    async (blob: Blob | null, seconds: number, speculative = false, typed = "") => {
      const L = labRef.current;
      const gate = gateRef.current;
      const sp = speaker();
      if (curRef.current) interrupt(curRef.current);
      const runId = nextId.current++;
      const cur: Current = {
        ctl: new AbortController(), tEnd: performance.now(), runId, youId: 0, macId: 0, heard: "", question: "",
        texts: {}, spoken: [], written: "", replyQueued: false, replyStarted: false, fillerTimer: 0,
        speculative, committed: !speculative, commitTimer: 0, watchdog: 0, shown: new Set(),
      };
      curRef.current = cur;
      // A speculative turn keeps the gate's utterance open so you can carry on; barge mode there only adds
      // the echo allowance, it never throws the utterance away.
      gate?.setMode(speculative || L.barge !== "off" ? "barge" : "paused");
      const silence = (speculative ? PAUSE_MS : L.endOfTurn) / 1000;
      setRuns((rs) => [{ id: runId, reply: L.reply, model, engine, silence: typed ? 0 : silence,
        turnMode: speculative ? "smart" as const : "pause" as const,
        barge: L.barge, fillers: L.fillers }, ...rs].slice(0, 40));
      go("thinking", "Transcribing");

      // The Qwen runtime's watchdog: if nothing arrives for a while, say so instead of hanging.
      const kick = () => {
        window.clearTimeout(cur.watchdog);
        cur.watchdog = window.setTimeout(() => {
          if (curRef.current !== cur) return;
          // Still talking? Then nothing is wrong: on a heavy engine one sentence can take 8 s to synthesise,
          // and the gap between audio events is not silence to the person listening.
          if (sp?.busy) return kick();
          cur.ctl.abort();
          clearTimers(cur);
          speakerRef.current?.stop();
          curRef.current = null;
          commitTurn(cur);
          gateRef.current?.setMode("listen");
          setError("The Mac did not answer in time. Say that again?");
          updRun(runId, { outcome: "timeout" });
          go("listening", "Listening");
        }, WATCHDOG_MS);
      };
      kick();

      const startFillerTimer = () => {
        if (L.fillers === "off") return;
        const wait = Math.max(0, FILLER_AFTER[L.fillers] - (performance.now() - cur.tEnd));
        cur.fillerTimer = window.setTimeout(() => {
          if (curRef.current !== cur || cur.replyQueued || !sp) return;
          if (L.fillers === "tone") {
            toneClip.current ||= thinkingTone();
            sp.enqueue({ kind: "filler", ...toneClip.current, text: "" });
            return;
          }
          const set = fillerCache.current.get(`${engine}:${voice}`);
          if (!set?.length) return;
          let k = Math.floor(Math.random() * set.length);
          if (k === lastFiller.current) k = (k + 1) % set.length;
          lastFiller.current = k;
          sp.enqueue({ kind: "filler", ...set[k] });
        }, wait);
      };

      let chain = Promise.resolve();
      const handle = (e: Record<string, any>) => {
        if (curRef.current !== cur) return; // a late event from a turn that was cancelled or reopened
        kick();
        if (e.type === "turn") {
          updRun(runId, { turnP: e.probability });
          if (speculative && !cur.committed) {
            cur.commitTimer = window.setTimeout(() => commitTurn(cur), e.complete ? REOPEN_COMPLETE_MS : REOPEN_INCOMPLETE_MS);
          }
          if (!e.complete) go("thinking", "Sounds like you are not finished");
        } else if (e.type === "proceed") {
          startFillerTimer();
        } else if (e.type === "heard") {
          cur.heard = e.text;
          cur.question = `${carry.current} ${e.text}`.trim();
          updRun(runId, { heard: since(cur) });
          if (!e.text) return;
          cur.youId = nextId.current++;
          setTurns((t) => [...t, {
            id: cur.youId, who: "you", text: e.text, segments: e.segments ?? [],
            audio: blob ? URL.createObjectURL(blob) : undefined,
            meta: e.realtime ? `${e.realtime}× realtime` : e.typed ? "typed" : "",
          }]);
          go("thinking", "Thinking");
        } else if (e.type === "sentence") {
          if (Object.keys(cur.texts).length === 0) updRun(runId, { firstWords: since(cur) });
          cur.texts[e.i] = e.text;
          if (L.transcript === "written") showText(cur, e.i, e.text);
        } else if (e.type === "audio") {
          const bytes = b64ToBytes(e.mp3);
          // Decoding is async; the chain keeps sentence 3 from overtaking sentence 2.
          chain = chain.then(async () => {
            const env = await shapeOf(ctxRef.current, bytes);
            if (cur.ctl.signal.aborted || !sp) return;
            cur.replyQueued = true;
            sp.enqueue({ kind: "reply", i: e.i, text: cur.texts[e.i] ?? "", env, ...speechBounds(env),
              url: URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" })) });
          });
        } else if (e.type === "written") {
          cur.written = e.text;
        } else if (e.type === "error") {
          setError(e.detail);
        }
      };

      try {
        const fd = new FormData();
        if (blob) fd.append("audio", blob, "clip.wav");
        fd.append("meta", JSON.stringify({
          mode: L.reply, model, engine, voice, seconds, carry: carry.current, history: history.current.slice(-6),
          turn: speculative ? "smart" : "pause", text: typed,
        }));
        const r = await api("/api/talk", { method: "POST", body: fd, signal: cur.ctl.signal });
        if (!r.ok || !r.body) throw new Error((await r.json().catch(() => ({}))).detail ?? `Request failed (${r.status}).`);
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let cut: number;
          while ((cut = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, cut);
            buf = buf.slice(cut + 2);
            for (const line of chunk.split("\n")) if (line.startsWith("data:")) handle(JSON.parse(line.slice(5)));
          }
        }
        await chain;
        window.clearTimeout(cur.watchdog); // everything has arrived; playing it out is not "no answer"
        // Written and synthesised; now wait for it to finish being said, unless someone talks over it.
        while (curRef.current === cur && sp?.busy) await new Promise((res) => setTimeout(res, 80));
        if (curRef.current !== cur) return;
        // A sentence whose audio failed was never "heard"; show it anyway rather than lose it.
        for (const [i, text] of Object.entries(cur.texts)) if (!cur.shown.has(Number(i))) showText(cur, Number(i), text);
        if (!cur.heard) {
          updRun(runId, { outcome: "done", total: since(cur) });
          return;
        }
        history.current.push({ role: "user", content: cur.question }, { role: "assistant", content: cur.written });
        carry.current = "";
        updRun(runId, { outcome: "done", total: since(cur) });
      } catch (err) {
        if (cur.ctl.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        updRun(runId, { outcome: "error" });
      } finally {
        if (curRef.current === cur) {
          clearTimers(cur);
          commitTurn(cur);
          curRef.current = null;
          gateRef.current?.setMode("listen");
          if (running) go("listening", "Listening");
        }
      }
    },
    [model, engine, voice, running]
  );
  const turnRef = useRef(handleTurn);
  turnRef.current = handleTurn;

  // ---- Talking over the reply, the "smart" way -------------------------------------------------------------
  // The cloud models this copies decide inside the model whether overlap is "mm-hmm" or "stop". Nothing open
  // does that, so it is rebuilt from parts: turn the reply down at once, let the sound finish, transcribe it,
  // and only stop for real words — or for any sound that goes on long enough to be one.

  const decideBarge = (b: Barge, verdict: "interrupt" | "ignored", heard = "") => {
    if (bargeRef.current !== b) return;
    window.clearTimeout(b.timer);
    const smartTurns = labRef.current.turn === "smart";
    if (verdict === "interrupt") {
      bargeRef.current = null;
      if (curRef.current === b.cur) interrupt(b.cur);
      else speakerRef.current?.duck(false);
      const turn = b.pendingTurn ?? (smartTurns && b.blob ? { blob: b.blob, seconds: b.seconds ?? 0 } : null);
      if (turn) void turnRef.current(turn.blob, turn.seconds, smartTurns);
      return;
    }
    b.verdict = "ignored";
    speakerRef.current?.duck(false);
    const rid = nextId.current++;
    setRuns((rs) => [{ id: rid, reply: labRef.current.reply, model, engine, silence: 0, outcome: "ignored" as const,
      turnMode: labRef.current.turn, barge: labRef.current.barge, fillers: labRef.current.fillers,
      note: heard || "(noise)", turnP: null }, ...rs].slice(0, 40));
    if (curRef.current) go("speaking", "Speaking · talk to interrupt");
    // The sound has ended: forget it. In pause mode the gate closes it itself a moment later.
    if (b.pendingTurn || (smartTurns && b.blob)) {
      if (smartTurns) gateRef.current?.commit();
      bargeRef.current = null;
    }
  };

  const checkBarge = (b: Barge) => {
    if (!b.blob) return;
    const id = ++b.checkId;
    b.checking = true;
    const fd = new FormData();
    fd.append("audio", b.blob, "clip.wav");
    // What the Mac is saying, so its own voice coming back through the speakers can be recognised as echo
    // rather than as someone talking over it.
    fd.append("said", Object.values(b.cur.texts).join(" ").slice(0, 2000));
    api("/api/backchannel", { method: "POST", body: fd })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => b.checkId === id
        && decideBarge(b, d.verdict === "interrupt" ? "interrupt" : "ignored", d.verdict === "echo" ? "its own voice" : d.text))
      .catch(() => b.checkId === id && decideBarge(b, "interrupt"));
  };

  gateHandler.current = (e: GateEvent) => {
    const gate = gateRef.current;
    const sp = speakerRef.current;
    if (!gate) return;
    if (DEBUG && e.type !== "level") {
      const c = curRef.current;
      console.log(`[gate] ${e.type} mode=${gate.mode} speaking=${gate.speaking} voiced=${gate.voicedMs.toFixed(0)}`
        + ` cur=${c ? `${c.runId}${c.speculative ? " spec" : ""}${c.committed ? " committed" : ""}` : "-"} barge=${bargeRef.current ? bargeRef.current.verdict || "pending" : "-"}`);
    }
    const L = labRef.current;
    if (e.type === "level") {
      const playing = sp?.busy && sp.playingKind;
      if (playing && !e.speaking) {
        push(sp.level() * 3.2, "mac");
        const peak = sp.recentPeak();
        if ((gate.mode === "barge" || sp.playingKind === "chime") && peak > 0.03 && !sp.ducked) {
          echoRatios.current.push(e.rms / peak);
          if (echoRatios.current.length > 120) echoRatios.current.shift();
        }
      } else {
        push(e.normalised, e.speaking ? "you" : "idle");
      }
      threshold.current = gate.threshold / (gate.floor * 9 || 1);
      const b = bargeRef.current;
      if (b && gate.speaking && gate.voicedMs > LONG_VOICED_MS) decideBarge(b, "interrupt");
      return;
    }
    if (e.type === "open") {
      const cur = curRef.current;
      if (cur && gate.mode === "barge") {
        if (L.barge === "instant") {
          interrupt(cur);
        } else if (L.barge === "smart") {
          const b: Barge = { cur, checkId: 0, checking: false, verdict: "", timer: 0 };
          b.timer = window.setTimeout(() => decideBarge(b, "interrupt"), BARGE_DECIDE_MS);
          bargeRef.current = b;
          sp?.duck(true);
          go("hearing", "Hearing you · the reply is turned down");
          return;
        }
      }
      go("hearing", "Hearing you");
      return;
    }
    if (e.type === "pause") {
      const b = bargeRef.current;
      if (b) {
        if (b.verdict === "ignored") {
          if (L.turn === "smart") gate.commit();
          bargeRef.current = null;
        } else if (!b.checking) {
          b.blob = e.blob;
          b.seconds = e.seconds;
          checkBarge(b);
        }
        return;
      }
      if (L.turn === "smart" && !curRef.current) void turnRef.current(e.blob, e.seconds, true);
      return;
    }
    if (e.type === "resume") {
      const b = bargeRef.current;
      if (b) {
        // The sound carried on: whatever the short clip was judged to be no longer applies.
        b.checkId++;
        b.checking = false;
        b.blob = undefined;
        if (b.verdict === "ignored") {
          b.verdict = "";
          window.clearTimeout(b.timer);
          b.timer = window.setTimeout(() => decideBarge(b, "interrupt"), BARGE_DECIDE_MS);
          sp?.duck(true);
        }
        return;
      }
      const cur = curRef.current;
      if (cur?.speculative && !cur.committed) reopen(cur);
      return;
    }
    if (e.type === "turn") {
      const b = bargeRef.current;
      if (b) {
        if (b.verdict === "ignored") {
          bargeRef.current = null;
          return;
        }
        b.pendingTurn = { blob: e.blob, seconds: e.seconds };
        if (!b.checking) {
          b.blob = e.blob;
          checkBarge(b);
        }
        return;
      }
      if (L.turn !== "smart") void turnRef.current(e.blob, e.seconds, false);
    }
  };

  /** Typed instead of spoken. Same pipeline, same spoken answer — it just skips transcription. */
  const send = async () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    const player = playerRef.current;
    if (player && !unlocked.current) {
      // This click is the gesture iOS wants before any later audio may play itself.
      (player as HTMLMediaElement & { playsInline: boolean }).playsInline = true;
      try {
        player.src = SILENT_WAV;
        await player.play();
        player.pause();
        unlocked.current = true;
      } catch {
        /* the reply may still play */
      }
    }
    if (!ctxRef.current && typeof AudioContext !== "undefined") ctxRef.current = new AudioContext();
    await turnRef.current(null, 0, false, text);
  };

  const start = async () => {
    setError(null);
    const player = playerRef.current;
    if (player && !unlocked.current) {
      // iOS only plays audio a gesture started; a moment of silence here permits every later reply.
      (player as HTMLMediaElement & { playsInline: boolean }).playsInline = true;
      try {
        player.src = SILENT_WAV;
        await player.play();
        player.pause();
        player.currentTime = 0;
        unlocked.current = true;
      } catch {
        /* a later play may still work */
      }
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: mic ? { exact: mic } : undefined,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch {
      setError("The microphone was not allowed, so there is nothing to listen to.");
      return;
    }
    streamRef.current = stream;
    const ctx = ctxRef.current?.state !== "closed" && ctxRef.current ? ctxRef.current : new AudioContext();
    ctxRef.current = ctx;
    if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);

    const sp = speaker();
    // Every gate event goes through a ref, so the handler always sees the current lab settings and voice.
    const gate = createGate(ctx.sampleRate, (e) => gateHandler.current(e));
    gate.setSensitivity(sensitivity);
    gate.setSilence(labRef.current.endOfTurn);
    gate.setEndMode(labRef.current.turn);
    gate.setEcho(() => {
      // Until the room has been heard for a moment, assume a fair amount of echo: better to be a little hard
      // to interrupt at first than to have the Mac cut itself off with its own voice.
      const r = [...echoRatios.current].sort((a, b) => a - b);
      const coupling = r.length >= 10 ? r[Math.floor(r.length * 0.75)] : 0.25;
      // A room that sends a lot of the reply back — laptop speakers rather than headphones — needs a wider
      // gap before a sound counts as you, or the Mac keeps interrupting itself.
      return coupling * (sp?.recentPeak() ?? 0) * (coupling > 0.3 ? 1.8 : 1);
    });
    gateRef.current = gate;
    if (labRef.current.fillers === "words") void prepareFillers(engine, voice);
    if (labRef.current.chime && sp) {
      // Measure this room's echo before the first reply: the gate only listens to levels while the chime plays.
      echoRatios.current = [];
      gate.setMode("paused");
      const c = chime();
      sp.enqueue({ kind: "chime", url: c.url, env: c.env, text: "" });
      const settle = () => {
        if (gateRef.current !== gate) return;
        if (sp.busy) return void window.setTimeout(settle, 60);
        if (!curRef.current) gate.setMode("listen");
      };
      window.setTimeout(settle, 120);
    }

    const src = ctx.createMediaStreamSource(stream);
    const node = ctx.createScriptProcessor(BLOCK, 1, 1);
    node.onaudioprocess = (ev) => gate.push(ev.inputBuffer.getChannelData(0));
    const mute = ctx.createGain();
    mute.gain.value = 0;
    src.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    nodeRef.current = node;

    setRunning(true);
    go("listening", "Listening");
  };

  const stop = useCallback(() => {
    setRunning(false);
    if (curRef.current) {
      curRef.current.ctl.abort();
      clearTimers(curRef.current);
      curRef.current = null;
    }
    if (bargeRef.current) {
      window.clearTimeout(bargeRef.current.timer);
      bargeRef.current = null;
    }
    speakerRef.current?.stop();
    nodeRef.current?.disconnect();
    if (nodeRef.current) nodeRef.current.onaudioprocess = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    playerRef.current?.pause();
    // The context stays open: it is cheap, and reopening one per conversation is not.
    levels.current = new Array(BARS).fill(0);
    who.current = new Array(BARS).fill("idle");
    go("asleep", "Not listening");
  }, []);

  useEffect(() => () => stop(), [stop]);

  const previewVoice = async (id: string) => {
    setPreviewing(id);
    try {
      // Cached server-side after the first play, so clicking through voices costs nothing.
      const r = await api(`/api/preview?engine=${encodeURIComponent(engine)}&voice=${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error("preview failed");
      const el = previewRef.current;
      if (!el) return;
      el.src = URL.createObjectURL(await r.blob());
      await el.play().catch(() => undefined);
    } catch {
      setError("That voice could not be previewed.");
    } finally {
      setPreviewing(null);
    }
  };

  // Stick to the newest turn while you are at the bottom; leave you alone when you have scrolled up to read.
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [turns, status]);

  const byLanguage = voices.reduce<Record<string, VoiceChoice[]>>((acc, v) => {
    (acc[v.language] ||= []).push(v);
    return acc;
  }, {});
  const currentVoice = voices.find((v) => v.id === voice);

  return (
    <div className="mx-auto max-w-3xl px-5 pb-24">
      <header className="flex flex-col gap-3 border-foreground border-b-2 pt-14 pb-6">
        <span className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-primary uppercase tracking-[0.14em]">
          Private AI stack · speech demo
          {tester ? (
            <span className="rounded-full border border-primary px-2 py-0.5 text-[10px] tracking-[0.08em]">
              tester pass · {tester}
            </span>
          ) : null}
        </span>
        <h1 className="text-balance font-semibold text-3xl leading-[1.05] tracking-tight sm:text-4xl">
          Talk to a Mac that never sends your voice anywhere
        </h1>
        <p className="max-w-[52ch] text-muted-foreground">
          Start the conversation and just speak. It notices when you stop, answers, and listens again.
          Everything happens on one machine in an office in Ohio, and nothing is kept.
        </p>
      </header>

      <main className="flex flex-col gap-5 pt-7">
        <section className="flex flex-col items-center gap-4 rounded-lg border bg-card p-6 text-center">
          <Ring className="h-[200px] w-[200px]" levels={levels} phase={phase} threshold={threshold} who={who} />
          <div className="flex items-center gap-2 font-mono text-[12px] text-muted-foreground uppercase tracking-[0.08em]">
            <span
              className={`size-2.5 rounded-full ${
                phase === "hearing" || phase === "listening"
                  ? "bg-[var(--color-you)]"
                  : phase === "asleep"
                    ? "bg-border"
                    : "bg-primary"
              }`}
            />
            {status}
          </div>

          <Button className="rounded-full px-8" onClick={() => (running ? stop() : void start())} size="lg"
            variant={running ? "destructive" : "default"}>
            {running ? "Stop" : "Start conversation"}
          </Button>

          <div className="flex flex-wrap items-center justify-center gap-2">
            <MicSelector
              onValueChange={(v) => {
                setMic(v);
                if (v) remember("mic", v);
              }}
              value={mic}
            >
              <MicSelectorTrigger>
                <MicSelectorValue />
              </MicSelectorTrigger>
              <MicSelectorContent>
                <MicSelectorInput placeholder="Search microphones…" />
                <MicSelectorList>
                  {(devices) => (
                    <>
                      <MicSelectorEmpty />
                      {devices.map((d) => (
                        <MicSelectorItem key={d.deviceId} value={d.deviceId}>
                          {d.label || "Microphone"}
                        </MicSelectorItem>
                      ))}
                    </>
                  )}
                </MicSelectorList>
              </MicSelectorContent>
            </MicSelector>

            <select
              aria-label="Speech engine"
              className="h-8 rounded-md border bg-card px-2 font-mono text-[12px]"
              onChange={(e) => {
                setEngine(e.target.value);
                remember("engine", e.target.value);
              }}
              value={engine}
            >
              {engines.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.label} · {e.note}
                </option>
              ))}
            </select>

            <VoiceSelector onOpenChange={setVoiceOpen} open={voiceOpen} value={voice}>
              <VoiceSelectorTrigger asChild>
                <Button size="sm" variant="outline">
                  Voice: {currentVoice?.label ?? "…"}
                </Button>
              </VoiceSelectorTrigger>
              {/* VoiceSelector is itself the Dialog root, so the content goes straight inside it.
                  Wrapping this in VoiceSelectorDialog nests a second dialog whose own open state stays
                  false, and the palette silently never appears. */}
              <VoiceSelectorContent>
                <VoiceSelectorInput placeholder={`Search ${voices.length} voices…`} />
                <VoiceSelectorList>
                  <VoiceSelectorEmpty>No voice found.</VoiceSelectorEmpty>
                  {Object.entries(byLanguage).map(([language, group]) => (
                    <VoiceSelectorGroup heading={language} key={language}>
                      {group.map((v) => (
                        <VoiceSelectorItem
                          key={v.id}
                          onSelect={() => {
                            setVoice(v.id);
                            remember(`voice:${engine}`, v.id);
                            setVoiceOpen(false);
                          }}
                          // What the search box matches on; selection is handled above.
                          value={`${v.label} ${language} ${v.gender}`}
                        >
                          <div className="flex flex-1 items-center gap-2">
                            <VoiceSelectorName>{v.label}</VoiceSelectorName>
                            <VoiceSelectorGender value={v.gender as "male" | "female"} />
                            <VoiceSelectorDescription>{language}</VoiceSelectorDescription>
                            {v.id === voice ? (
                              <span className="font-mono text-[10px] text-primary uppercase">current</span>
                            ) : null}
                          </div>
                          <VoiceSelectorPreview
                            loading={previewing === v.id}
                            onPlay={() => void previewVoice(v.id)}
                            playing={false}
                          />
                        </VoiceSelectorItem>
                      ))}
                    </VoiceSelectorGroup>
                  ))}
                </VoiceSelectorList>
              </VoiceSelectorContent>
            </VoiceSelector>

            <select
              aria-label="Chat model"
              className="h-8 rounded-md border bg-card px-2 font-mono text-[12px]"
              onChange={(e) => {
                setModel(e.target.value);
                remember("model", e.target.value);
              }}
              value={model}
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} · {m.note}
                </option>
              ))}
            </select>

            <select
              aria-label="Microphone sensitivity"
              className="h-8 rounded-md border bg-card px-2 font-mono text-[12px]"
              onChange={(e) => {
                setSensitivity(e.target.value as Sensitivity);
                remember("sens", e.target.value);
              }}
              value={sensitivity}
            >
              <option value="high">Sensitivity: high · quiet room</option>
              <option value="normal">Sensitivity: normal</option>
              <option value="low">Sensitivity: low · noisy room</option>
            </select>
          </div>

          {error ? <p className="text-destructive text-sm">{error}</p> : null}
        </section>

        <section aria-label="Conversation" className="flex flex-col overflow-hidden rounded-lg border bg-card">
          <div
            className="flex min-h-[220px] flex-col gap-3 overflow-y-auto p-4 [max-height:min(56vh,560px)]"
            onScroll={(e) => {
              const el = e.currentTarget;
              stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
            }}
            ref={scroller}
          >
            {turns.length === 0 ? (
              <p className="m-auto max-w-[36ch] text-center text-muted-foreground text-sm">
                Press start and talk, or type below. Either way the answer is spoken back.
              </p>
            ) : (
              turns.map((turn) => (
                <article
                  className={`flex flex-col gap-1 ${turn.who === "you" ? "items-end" : "items-start"}`}
                  key={turn.id}
                >
                  <div
                    className={`max-w-[min(85%,52ch)] rounded-2xl px-3.5 py-2 ${
                      turn.who === "you"
                        ? "rounded-br-sm bg-[var(--color-you)]/12 text-foreground"
                        : "rounded-bl-sm bg-muted text-foreground"
                    }`}
                  >
                    <span className="break-words">
                      {turn.who === "you" && turn.segments?.length ? (
                        <Transcription
                          currentTime={playhead?.id === turn.id ? playhead.t : 0}
                          onSeek={(t) => {
                            const el = document.getElementById(`audio-${turn.id}`) as HTMLAudioElement | null;
                            if (el) {
                              el.currentTime = t;
                              void el.play();
                            }
                          }}
                          segments={turn.segments}
                        >
                          {(segment, i) => <TranscriptionSegment index={i} key={i} segment={segment} />}
                        </Transcription>
                      ) : (
                        turn.text
                      )}
                    </span>
                    {turn.audio ? (
                      <AudioPlayer className="mt-1.5 w-full">
                        <AudioPlayerElement
                          id={`audio-${turn.id}`}
                          onTimeUpdate={(e) => setPlayhead({ id: turn.id, t: (e.target as HTMLAudioElement).currentTime })}
                          slot="media"
                          src={turn.audio}
                        />
                        <AudioPlayerControlBar>
                          <AudioPlayerPlayButton />
                          <AudioPlayerTimeRange />
                          <AudioPlayerTimeDisplay />
                        </AudioPlayerControlBar>
                      </AudioPlayer>
                    ) : null}
                  </div>
                  <span className="px-1 font-mono text-[10.5px] text-muted-foreground uppercase tracking-[0.08em]">
                    {turn.who === "you" ? "You" : "Mac"}
                    {turn.meta ? ` · ${turn.meta}` : ""}
                  </span>
                </article>
              ))
            )}
            {phase === "thinking" || phase === "speaking" ? (
              <span className="px-1 font-mono text-[10.5px] text-muted-foreground uppercase tracking-[0.08em]">
                {status}…
              </span>
            ) : null}
          </div>

          <form
            className="flex items-center gap-2 border-t p-3"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <input
              aria-label="Type a message"
              className="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              maxLength={400}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={running ? "…or type instead of talking" : "Type a message — no microphone needed"}
              value={draft}
            />
            <Button disabled={!draft.trim()} size="sm" type="submit">
              Send
            </Button>
          </form>
        </section>

        <section className="flex flex-col gap-3 rounded-lg border bg-card p-4" aria-label="Conversation lab">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-mono text-[11px] text-primary uppercase tracking-[0.14em]">Lab</h2>
            <span className="font-mono text-[11px] text-muted-foreground">{running ? echoInfo : "changes apply to the next turn"}</span>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              How the reply is spoken
              <select aria-label="Reply mode" className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ reply: e.target.value as ReplyMode })} value={lab.reply}>
                <option value="eager">Eager · start on the first clause</option>
                <option value="stream">Sentence by sentence</option>
                <option value="whole">Whole reply · the original</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              Talking over the reply
              <select aria-label="Interrupt" className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ barge: e.target.value as BargeMode })} value={lab.barge}>
                <option value="smart">Smart · turns it down, stops only for real words</option>
                <option value="instant">Stops it instantly</option>
                <option value="off">Is ignored until it finishes</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              How it knows you are done
              <select aria-label="Turn detection" className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ turn: e.target.value as TurnMode })} value={lab.turn}>
                <option value="smart">Smart Turn model · listens to how you stopped</option>
                <option value="pause">A pause of fixed length</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              While it thinks
              <select aria-label="Fillers" className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ fillers: e.target.value as FillerMode })} value={lab.fillers}>
                <option value="off">Nothing · the ring shows it is busy</option>
                <option value="tone">A quiet tone · after {FILLER_AFTER.tone} ms</option>
                <option value="words">It says something · after {FILLER_AFTER.words} ms</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              Pause that ends your turn{lab.turn === "smart" ? " · pause mode only" : ""}
              <select aria-label="End of turn" disabled={lab.turn === "smart"} className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ endOfTurn: Number(e.target.value) })} value={lab.endOfTurn}>
                <option value={450}>0.45 s · snappy, may cut you off</option>
                <option value={700}>0.7 s · default</option>
                <option value={1000}>1 s · patient</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              Start chime
              <select aria-label="Start chime" className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ chime: e.target.value === "on" })} value={lab.chime ? "on" : "off"}>
                <option value="on">On · measures this room's echo first</option>
                <option value="off">Off</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              The Mac's words appear
              <select aria-label="Transcript" className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ transcript: e.target.value as TranscriptMode })} value={lab.transcript}>
                <option value="spoken">As they are spoken</option>
                <option value="written">As they are written</option>
              </select>
            </label>
          </div>

          {runs.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-[11.5px]">
                <caption className="pb-2 text-left font-sans text-[12px] text-muted-foreground">
                  Seconds after the pause that ended your turn. Add that pause for what you actually waited.
                </caption>
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-normal">mode</th>
                    <th className="py-1 pr-3 font-normal">engine</th>
                    <th className="py-1 pr-3 font-normal">done?</th>
                    <th className="py-1 pr-3 font-normal">heard</th>
                    <th className="py-1 pr-3 font-normal">words</th>
                    <th className="py-1 pr-3 font-normal">filler</th>
                    <th className="py-1 pr-3 font-normal">voice</th>
                    <th className="py-1 pr-3 font-normal">you waited</th>
                    <th className="py-1 font-normal">outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => {
                    const firstHeard = Math.min(r.filler ?? 99, r.firstSound ?? 99);
                    return (
                      <tr className="border-t" key={r.id}>
                        <td className="py-1 pr-3">{r.reply}</td>
                        <td className="py-1 pr-3">{r.engine.replace("-tts", "").replace("-realtime", "")}</td>
                        <td className="py-1 pr-3">{r.turnP === undefined ? "pause" : r.turnP === null ? "n/a" : r.turnP.toFixed(2)}</td>
                        <td className="py-1 pr-3">{r.heard?.toFixed(2) ?? "…"}</td>
                        <td className="py-1 pr-3">{r.firstWords?.toFixed(2) ?? "…"}</td>
                        <td className="py-1 pr-3">{r.filler?.toFixed(2) ?? "–"}</td>
                        <td className="py-1 pr-3 text-primary">{r.firstSound?.toFixed(2) ?? "…"}</td>
                        <td className="py-1 pr-3">{firstHeard < 99 ? (firstHeard + r.silence).toFixed(2) : "…"}</td>
                        <td className="py-1">{r.outcome ?? "…"}{r.note ? ` "${r.note}"` : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>

      </main>

      <audio hidden ref={playerRef} />
      <audio hidden ref={previewRef} />
    </div>
  );
}

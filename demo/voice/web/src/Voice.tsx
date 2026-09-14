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
import { SILENT_WAV, createGate, BLOCK, type Sensitivity } from "@/lib/audio";
import { createSpeaker, shapeOf, type Clip } from "@/lib/speaker";

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
type FillerMode = "off" | "slow" | "quick";
type Lab = { reply: ReplyMode; interrupt: boolean; fillers: FillerMode; endOfTurn: number };
const LAB_DEFAULT: Lab = { reply: "eager", interrupt: true, fillers: "slow", endOfTurn: 700 };
const FILLER_AFTER: Record<Exclude<FillerMode, "off">, number> = { quick: 250, slow: 600 };

/** One row of the latency table. Times are seconds from the moment the gate decided you had finished. */
type Run = {
  id: number;
  reply: ReplyMode;
  model: string;
  engine: string;
  silence: number;
  heard?: number;
  firstWords?: number;
  firstSound?: number;
  filler?: number;
  total?: number;
  outcome?: "done" | "interrupted" | "continued" | "error";
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
    return { ...LAB_DEFAULT, ...JSON.parse(remembered("lab") ?? "{}") };
  } catch {
    return LAB_DEFAULT;
  }
};

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
  const fillerCache = useRef(new Map<string, { url: string; env: number[] | null; text: string }[]>());
  const lastFiller = useRef(-1);
  // Mic loudness divided by the reply's loudness, sampled while the reply plays and you are quiet: how much
  // of the Mac's own voice comes back into the microphone in this room, after the browser's echo cancelling.
  const echoRatios = useRef<number[]>([]);

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
  useEffect(() => {
    if (running) void prepareFillers(engine, voice);
  }, [engine, voice, running]);
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
          if (clip.kind === "filler") {
            updRun(cur.runId, { filler: since(cur) });
            go("speaking", "Thinking out loud");
            return;
          }
          if (!cur.replyStarted) {
            cur.replyStarted = true;
            updRun(cur.runId, { firstSound: since(cur) });
          }
          cur.spoken.push(clip.text);
          go("speaking", labRef.current.interrupt ? "Speaking · talk to interrupt" : "Speaking");
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

  /** Fillers are fetched once per voice, before they are needed; a filler that has to be downloaded is late. */
  const prepareFillers = async (forEngine: string, forVoice: string) => {
    const key = `${forEngine}:${forVoice}`;
    if (!forVoice || fillerCache.current.has(key)) return;
    fillerCache.current.set(key, []);
    try {
      const { fillers } = await (await fetch("/api/fillers")).json();
      const set = await Promise.all(
        (fillers as string[]).map(async (text, i) => {
          const r = await api(`/api/filler?engine=${encodeURIComponent(forEngine)}&voice=${encodeURIComponent(forVoice)}&i=${i}`);
          if (!r.ok) throw new Error("filler");
          const bytes = await r.arrayBuffer();
          return { text, env: await shapeOf(ctxRef.current, bytes), url: URL.createObjectURL(new Blob([bytes], { type: "audio/mpeg" })) };
        })
      );
      fillerCache.current.set(key, set);
    } catch {
      fillerCache.current.delete(key);
    }
  };

  /** You started talking over the reply (or before it began). Stop everything and keep what matters. */
  const interrupt = (cur: Current) => {
    cur.ctl.abort();
    window.clearTimeout(cur.fillerTimer);
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
    async (blob: Blob, seconds: number) => {
      const L = labRef.current;
      const gate = gateRef.current;
      const sp = speaker();
      if (curRef.current) interrupt(curRef.current);
      const runId = nextId.current++;
      const cur: Current = {
        ctl: new AbortController(), tEnd: performance.now(), runId, youId: 0, macId: 0, heard: "", question: "",
        texts: {}, spoken: [], written: "", replyQueued: false, replyStarted: false, fillerTimer: 0,
      };
      curRef.current = cur;
      gate?.setMode(L.interrupt ? "barge" : "paused");
      setRuns((rs) => [{ id: runId, reply: L.reply, model, engine, silence: L.endOfTurn / 1000 }, ...rs].slice(0, 40));
      go("thinking", "Transcribing");

      if (L.fillers !== "off") {
        cur.fillerTimer = window.setTimeout(() => {
          const set = fillerCache.current.get(`${engine}:${voice}`);
          if (curRef.current !== cur || cur.replyQueued || !set?.length || !sp) return;
          let k = Math.floor(Math.random() * set.length);
          if (k === lastFiller.current) k = (k + 1) % set.length;
          lastFiller.current = k;
          sp.enqueue({ kind: "filler", ...set[k] });
        }, FILLER_AFTER[L.fillers]);
      }

      let chain = Promise.resolve();
      const handle = (e: Record<string, any>) => {
        if (e.type === "heard") {
          cur.heard = e.text;
          cur.question = `${carry.current} ${e.text}`.trim();
          updRun(runId, { heard: since(cur) });
          if (!e.text) return;
          cur.youId = nextId.current++;
          setTurns((t) => [...t, {
            id: cur.youId, who: "you", text: e.text, audio: URL.createObjectURL(blob), segments: e.segments ?? [],
            meta: e.realtime ? `${e.realtime}× realtime` : "",
          }]);
          go("thinking", "Thinking");
        } else if (e.type === "sentence") {
          cur.texts[e.i] = e.text;
          if (!cur.macId) {
            cur.macId = nextId.current++;
            updRun(runId, { firstWords: since(cur) });
            setTurns((t) => [...t, { id: cur.macId, who: "mac", text: e.text, meta: "" }]);
          } else {
            setTurn(cur.macId, (t) => ({ text: `${t.text} ${e.text}` }));
          }
        } else if (e.type === "audio") {
          const bytes = b64ToBytes(e.mp3);
          // Decoding is async; the chain keeps sentence 3 from overtaking sentence 2.
          chain = chain.then(async () => {
            const env = await shapeOf(ctxRef.current, bytes);
            if (cur.ctl.signal.aborted || !sp) return;
            cur.replyQueued = true;
            sp.enqueue({ kind: "reply", i: e.i, text: cur.texts[e.i] ?? "", env,
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
        fd.append("audio", blob, "clip.wav");
        fd.append("meta", JSON.stringify({
          mode: L.reply, model, engine, voice, seconds, carry: carry.current, history: history.current.slice(-6),
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
        // Written and synthesised; now wait for it to finish being said, unless someone talks over it.
        while (curRef.current === cur && sp?.busy) await new Promise((res) => setTimeout(res, 80));
        if (curRef.current !== cur) return;
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
          window.clearTimeout(cur.fillerTimer);
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
    const gate = createGate(ctx.sampleRate, (e) => {
      if (e.type === "level") {
        const playing = sp?.busy && sp.playingKind;
        if (playing && !e.speaking) {
          push(sp.level() * 3.2, "mac");
          const peak = sp.recentPeak();
          if (gate.mode === "barge" && peak > 0.03) {
            echoRatios.current.push(e.rms / peak);
            if (echoRatios.current.length > 120) echoRatios.current.shift();
          }
        } else {
          push(e.normalised, e.speaking ? "you" : "idle");
        }
        threshold.current = gate.threshold / (gate.floor * 9 || 1);
      } else if (e.type === "open") {
        // Opening while the reply is in flight means you talked over it.
        if (curRef.current && gate.mode === "barge") interrupt(curRef.current);
        go("hearing", "Hearing you");
      } else if (e.type === "turn") {
        void turnRef.current(e.blob, e.seconds);
      }
    });
    gate.setSensitivity(sensitivity);
    gate.setSilence(labRef.current.endOfTurn);
    gate.setEcho(() => {
      // Until the room has been heard for a moment, assume a fair amount of echo: better to be a little hard
      // to interrupt at first than to have the Mac cut itself off with its own voice.
      const r = [...echoRatios.current].sort((a, b) => a - b);
      const coupling = r.length >= 10 ? r[Math.floor(r.length * 0.75)] : 0.25;
      return coupling * (sp?.recentPeak() ?? 0);
    });
    gateRef.current = gate;
    void prepareFillers(engine, voice);

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
      window.clearTimeout(curRef.current.fillerTimer);
      curRef.current = null;
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
                onChange={(e) => setLab({ interrupt: e.target.value === "on" })} value={lab.interrupt ? "on" : "off"}>
                <option value="on">Interrupts it</option>
                <option value="off">Is ignored until it finishes</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              Fillers while it thinks
              <select aria-label="Fillers" className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ fillers: e.target.value as FillerMode })} value={lab.fillers}>
                <option value="off">Off</option>
                <option value="slow">Only when slow · after {FILLER_AFTER.slow} ms</option>
                <option value="quick">Quick acknowledgement · after {FILLER_AFTER.quick} ms</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-left text-[12px] text-muted-foreground">
              Pause that ends your turn
              <select aria-label="End of turn" className="h-8 rounded-md border bg-card px-2 font-mono text-[12px] text-foreground"
                onChange={(e) => setLab({ endOfTurn: Number(e.target.value) })} value={lab.endOfTurn}>
                <option value={450}>0.45 s · snappy, may cut you off</option>
                <option value={700}>0.7 s · default</option>
                <option value={1000}>1 s · patient</option>
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
                        <td className="py-1 pr-3">{r.heard?.toFixed(2) ?? "…"}</td>
                        <td className="py-1 pr-3">{r.firstWords?.toFixed(2) ?? "…"}</td>
                        <td className="py-1 pr-3">{r.filler?.toFixed(2) ?? "–"}</td>
                        <td className="py-1 pr-3 text-primary">{r.firstSound?.toFixed(2) ?? "…"}</td>
                        <td className="py-1 pr-3">{firstHeard < 99 ? (firstHeard + r.silence).toFixed(2) : "…"}</td>
                        <td className="py-1">{r.outcome ?? "…"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>

        <section className="divide-y rounded-lg border bg-card">
          {turns.length === 0 ? (
            <p className="p-4 text-muted-foreground text-sm">Nothing said yet.</p>
          ) : (
            turns.map((turn) => (
              <article className="flex flex-col gap-2 p-4" key={turn.id}>
                <div className="flex items-baseline gap-3">
                  <span
                    className={`font-mono text-[11px] uppercase tracking-[0.1em] ${
                      turn.who === "you" ? "text-[var(--color-you)]" : "text-muted-foreground"
                    }`}
                  >
                    {turn.who === "you" ? "You" : "Mac"}
                  </span>
                  <span className="min-w-0 flex-1 break-words">
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
                  <span className="whitespace-nowrap font-mono text-[11.5px] text-primary">{turn.meta}</span>
                </div>
                {turn.audio ? (
                  <AudioPlayer className="w-full">
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
              </article>
            ))
          )}
        </section>
      </main>

      <audio hidden ref={playerRef} />
      <audio hidden ref={previewRef} />
    </div>
  );
}

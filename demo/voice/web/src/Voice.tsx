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
  VoiceSelectorDialog,
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
import { SILENT_WAV, createGate, envelopeOf, BLOCK, type Sensitivity } from "@/lib/audio";

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
type VoiceChoice = { id: string; label: string; language: string; gender: string };

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

export default function Voice() {
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<Phase>("asleep");
  const [status, setStatus] = useState("Not listening");
  const [error, setError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [models, setModels] = useState<ModelChoice[]>([]);
  const [voices, setVoices] = useState<VoiceChoice[]>([]);
  const [model, setModel] = useState(remembered("model") ?? "");
  const [voice, setVoice] = useState(remembered("voice") ?? "");
  const [mic, setMic] = useState<string | undefined>(remembered("mic") ?? undefined);
  const [sensitivity, setSensitivity] = useState<Sensitivity>((remembered("sens") as Sensitivity) ?? "normal");
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState<{ id: number; t: number } | null>(null);

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
    fetch("/api/models")
      .then((r) => r.json())
      .then((d) => {
        setModels(d.models);
        setModel((m) => (m && d.models.some((x: ModelChoice) => x.id === m) ? m : d.default));
      })
      .catch(() => undefined);
    fetch("/api/voices")
      .then((r) => r.json())
      .then((d) => {
        setVoices(d.voices);
        setVoice((v) => (v && d.voices.some((x: VoiceChoice) => x.id === v) ? v : d.default));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => gateRef.current?.setSensitivity(sensitivity), [sensitivity]);

  const post = async (url: string, init: RequestInit) => {
    const r = await fetch(url, init);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail ?? `Request failed (${r.status}).`);
    return d;
  };

  const handleTurn = useCallback(
    async (blob: Blob, seconds: number) => {
      gateRef.current?.setPaused(true);
      try {
        go("thinking", "Transcribing");
        const fd = new FormData();
        fd.append("audio", blob, "clip.wav");
        fd.append("seconds", seconds.toFixed(2));
        const heard = await post("/api/transcribe", { method: "POST", body: fd });
        if (!heard.text) {
          go("listening", "Listening");
          return;
        }
        const yourTurn: Turn = {
          id: nextId.current++,
          who: "you",
          text: heard.text,
          meta: heard.realtime ? `${heard.realtime}× realtime` : `${heard.seconds}s`,
          audio: URL.createObjectURL(blob),
          segments: heard.segments ?? [],
        };
        setTurns((t) => [...t, yourTurn]);
        history.current.push({ role: "user", content: heard.text });

        go("thinking", "Thinking");
        const said = await post("/api/reply", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text: heard.text, history: history.current.slice(0, -1), model }),
        });
        history.current.push({ role: "assistant", content: said.text });
        const macId = nextId.current++;
        setTurns((t) => [...t, { id: macId, who: "mac", text: said.text, meta: `${said.seconds}s` }]);

        go("speaking", "Speaking");
        const r = await fetch("/api/speak", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text: said.text, voice }),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Speech synthesis failed.");
        const bytes = await r.arrayBuffer();
        const shape = envelopeOf(bytes);
        const synth = r.headers.get("X-Synthesis-Seconds");
        if (synth) setTurns((t) => t.map((x) => (x.id === macId ? { ...x, meta: `${x.meta} · ${synth}s to speak` } : x)));

        const player = playerRef.current;
        if (!player) return;
        player.src = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
        let watching = true;
        const watch = () => {
          if (!watching) return;
          if (shape) push((shape.env[Math.floor(player.currentTime / 0.046)] ?? 0) * 3.2, "mac");
          requestAnimationFrame(watch);
        };
        watch();
        await new Promise<void>((done) => {
          player.onended = () => done();
          player.onerror = () => {
            setError("The reply could not be played on this device.");
            done();
          };
          player.play().catch(() => {
            setError("This browser blocked the reply from playing. Press Start again to allow audio.");
            done();
          });
        });
        watching = false;
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        gateRef.current?.setPaused(false);
        if (running) go("listening", "Listening");
      }
    },
    [model, voice, running]
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

    const gate = createGate(ctx.sampleRate, (e) => {
      if (e.type === "level") {
        push(e.normalised, e.speaking ? "you" : "idle");
        threshold.current = gate.threshold / (gate.floor * 9 || 1);
      } else if (e.type === "open") {
        go("hearing", "Hearing you");
      } else if (e.type === "turn") {
        void turnRef.current(e.blob, e.seconds);
      }
    });
    gate.setSensitivity(sensitivity);
    gateRef.current = gate;

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
      const r = await fetch("/api/speak", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: "This is how I sound.", voice: id }),
      });
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
        <span className="font-mono text-[11px] text-primary uppercase tracking-[0.14em]">
          Private AI stack · speech demo
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

            <VoiceSelector
              onValueChange={(v) => {
                if (!v) return;
                setVoice(v);
                remember("voice", v);
              }}
              value={voice}
            >
              <VoiceSelectorTrigger asChild>
                <Button size="sm" variant="outline">
                  Voice: {currentVoice?.label ?? "…"}
                </Button>
              </VoiceSelectorTrigger>
              <VoiceSelectorDialog>
                <VoiceSelectorContent>
                  <VoiceSelectorInput placeholder="Search voices…" />
                  <VoiceSelectorList>
                    <VoiceSelectorEmpty />
                    {Object.entries(byLanguage).map(([language, group]) => (
                      <VoiceSelectorGroup heading={language} key={language}>
                        {group.map((v) => (
                          <VoiceSelectorItem key={v.id} value={v.id}>
                            <div className="flex flex-1 items-center gap-2">
                              <VoiceSelectorName>{v.label}</VoiceSelectorName>
                              <VoiceSelectorGender value={v.gender as "male" | "female"} />
                              <VoiceSelectorDescription>{language}</VoiceSelectorDescription>
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
              </VoiceSelectorDialog>
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

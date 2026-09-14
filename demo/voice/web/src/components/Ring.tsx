import { useEffect, useRef } from "react";

export type Phase = "asleep" | "listening" | "hearing" | "thinking" | "speaking";

const BARS = 72;

/**
 * One circular object with five states, the vocabulary borrowed from the Persona component.
 *
 * The difference is that four of the five are driven by real amplitude rather than a canned animation
 * — your microphone on one side, the reply's waveform on the other. Only `thinking` has nothing to
 * listen to, so only that one is synthesised, which keeps loading as this object being busy rather
 * than a spinner appearing beside it.
 */
export function Ring({
  phase,
  levels,
  who,
  threshold,
  className,
}: {
  phase: Phase;
  levels: React.RefObject<number[]>;
  who: React.RefObject<("idle" | "you" | "mac")[]>;
  threshold: React.RefObject<number>;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;

    const colour = (name: string) =>
      getComputedStyle(canvas).getPropertyValue(name).trim() || "#888";

    const draw = (now: number) => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth || 200;
      const h = canvas.clientHeight || 200;
      if (canvas.width !== Math.round(w * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const p = phaseRef.current;
      const you = colour("--color-you");
      const mac = colour("--color-primary");
      const idle = colour("--color-border");
      const cx = w / 2;
      const cy = h / 2;
      const R = Math.min(w, h) * 0.27;
      const reach = Math.min(w, h) * 0.19;
      const t = now / 1000;
      const breathe = 1 + Math.sin(t * 1.6) * (p === "asleep" ? 0.012 : 0.02);
      const tone = p === "asleep" ? idle : p === "listening" || p === "hearing" ? you : mac;

      // Where the gate opens, so a voice that is not clearing it explains itself.
      if (p === "listening" || p === "hearing") {
        ctx.beginPath();
        ctx.arc(cx, cy, R * breathe + 4 + Math.min(0.95, threshold.current) * reach, 0, Math.PI * 2);
        ctx.strokeStyle = idle;
        ctx.globalAlpha = 0.55;
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      ctx.globalAlpha = 1;
      ctx.lineCap = "round";
      for (let i = 0; i < BARS; i++) {
        let v: number;
        let c = tone;
        if (p === "thinking") {
          v = 0.18 + 0.34 * Math.max(0, Math.sin((i / BARS) * Math.PI * 4 - t * 3.4));
        } else if (p === "asleep") {
          v = 0.06 + 0.03 * Math.sin((i / BARS) * Math.PI * 6 + t * 0.9);
        } else {
          v = levels.current[i] ?? 0;
          const src = who.current[i] ?? "idle";
          c = src === "idle" ? idle : src === "you" ? you : mac;
        }
        const a = (i / BARS) * Math.PI * 2 - Math.PI / 2;
        const r0 = R * breathe;
        const r1 = r0 + 3 + v * reach;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
        ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
        ctx.strokeStyle = c;
        ctx.globalAlpha = p === "asleep" ? 0.5 : v < 0.02 ? 0.45 : 1;
        ctx.lineWidth = Math.max(2, (Math.min(w, h) / BARS) * 0.9);
        ctx.stroke();
      }

      const recent = levels.current.slice(-8);
      const loud = p === "thinking" ? 0.3 : recent.reduce((a, b) => a + b, 0) / Math.max(1, recent.length);
      ctx.beginPath();
      ctx.arc(cx, cy, R * breathe - 7, 0, Math.PI * 2);
      ctx.strokeStyle = tone;
      ctx.globalAlpha = 0.35 + loud * 0.5;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.globalAlpha = 1;

      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [levels, who, threshold]);

  return <canvas aria-hidden className={className} ref={ref} />;
}

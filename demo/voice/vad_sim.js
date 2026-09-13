// Offline replay of the browser's speech gate against synthetic block sequences: `node vad_sim.js`.
// Keep the constants in step with static/index.html. Written because the gate cannot be tested
// from a terminal, and the first version of it only opened for continuous loud speech.
// Floor = a low percentile of the recent past, which is what the room sounds like when nobody talks.
// Converges in about a second and is not dragged upward by speech, unlike an exponential average.
const MS = 46, MIN_SPEECH = 200, SILENCE = 700, WIN = 40;   // ~1.8 s of history
const SENS = { high:{start:1.9,keep:1.35,abs:0.0006}, normal:{start:2.6,keep:1.7,abs:0.0010},
               low:{start:4.0,keep:2.4,abs:0.0020} };

function floorOf(hist){
  const s = [...hist].sort((a,b)=>a-b);
  return Math.max(0.0008, s[Math.floor(s.length*0.2)] || 0.003);
}
function run(levels, sensName){
  const sens = SENS[sensName];
  let hist = [], speechMs = 0, silenceMs = 0, speaking = false, opened = null, closed = null;
  levels.forEach((rms,i) => {
    hist.push(rms); if (hist.length > WIN) hist.shift();
    const floor = floorOf(hist);
    const startT = floor*sens.start + sens.abs, keepT = floor*sens.keep + sens.abs*0.5;
    if (!speaking){
      if (rms > startT) speechMs += MS; else speechMs = Math.max(0, speechMs - MS*0.6);
      if (speechMs >= MIN_SPEECH){ speaking = true; opened ??= i*MS; silenceMs = 0; }
    } else {
      silenceMs = rms > keepT ? 0 : silenceMs + MS;
      if (silenceMs >= SILENCE){ closed ??= i*MS; speaking = false; }
    }
  });
  return { opened, closed };
}
const quiet = n => Array(n).fill(0.003);
const talk  = (n, lvl) => Array.from({length:n}, (_,i) => i%5===4 ? 0.003 : lvl);
const noisy = n => Array.from({length:n}, () => 0.012 + Math.random()*0.004);   // fan / open office
const seq = (...xs) => [].concat(...xs);

const cases = [
  ["loud speech 0.020",  seq(quiet(20), talk(30,0.020), quiet(25))],
  ["normal 0.012",       seq(quiet(20), talk(30,0.012), quiet(25))],
  ["quiet talker 0.008", seq(quiet(20), talk(30,0.008), quiet(25))],
  ["very quiet 0.006",   seq(quiet(20), talk(30,0.006), quiet(25))],
  ["short word 0.3 s",   seq(quiet(20), talk(7,0.015),  quiet(25))],
  ["noise only (fan)",   seq(quiet(10), noisy(50))],
];
for (const [name, lv] of cases){
  const out = ["high","normal","low"].map(s => {
    const r = run(lv, s);
    return `${s}:${r.opened===null?"—":"+"+r.opened+"ms"}`;
  });
  console.log(`  ${name.padEnd(20)} ${out.join("   ")}`);
}

// Check the WAV envelope parser against a real reply: `node env_test.js reply.wav static/index.html`.
// The visualiser reads the waveform rather than tapping the audio graph, so this is the only way
// to know it parsed the engine's WAV correctly without opening a browser.
// Verify the WAV envelope parser against the real file the engine returns.
const fs = require("fs");
const buf = fs.readFileSync(process.argv[2]);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const src = fs.readFileSync(process.argv[3], "utf8");
const fn = src.slice(src.indexOf("function envelopeOf"), src.indexOf("// iOS will not play"));
const envelopeOf = new Function(fn + "; return envelopeOf;")();
const out = envelopeOf(ab);
if (!out) { console.log("  parser returned null — FAIL"); process.exit(1); }
const peak = Math.max(...out.env), mean = out.env.reduce((a,b)=>a+b,0)/out.env.length;
console.log(`  parsed ${out.env.length} points covering ${out.seconds.toFixed(2)}s`);
console.log(`  peak ${peak.toFixed(3)} · mean ${mean.toFixed(3)} · silent points ${out.env.filter(v=>v<0.005).length}`);
console.log(`  ${peak > 0.05 && out.seconds > 1 ? "PASS — shape looks like speech" : "FAIL — flat or wrong length"}`);

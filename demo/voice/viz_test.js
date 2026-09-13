// Render every phase against a recording canvas stub: catches NaN geometry, bars drawn off-canvas and
// a state that silently draws nothing. The visualiser cannot be eyeballed from a terminal; this is how
// it gets checked at all.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const js = src.match(/<script>([\s\S]*)<\/script>/)[1];

const ops = [];
let bad = 0;
const num = v => { if (typeof v === "number" && !Number.isFinite(v)) bad++; return v; };
const c2d = {
  setTransform(){}, clearRect(){}, beginPath(){ ops.push(["path"]); }, arc(x,y,r){ num(x);num(y);num(r); ops.push(["arc",r]); },
  moveTo(x,y){ num(x);num(y); }, lineTo(x,y){ num(x);num(y); ops.push(["line",x,y]); },
  stroke(){ ops.push(["stroke", c2d.strokeStyle, c2d.globalAlpha]); }, fillRect(){}, setLineDash(){},
  strokeStyle:"", fillStyle:"", globalAlpha:1, lineWidth:1, lineCap:"",
};
const canvas = { width:400, height:400, clientWidth:200, clientHeight:200, getContext:()=>c2d };
const el = id => id === "viz" ? canvas : { classList:{add(){},remove(){}}, style:{}, addEventListener(){},
  appendChild(){}, children:[{},{},{}], textContent:"", innerHTML:"", value:"", hidden:false, remove(){}, scrollIntoView(){} };
global.document = { getElementById: el, documentElement: {}, createElement: () => el(""), addEventListener(){} };
global.getComputedStyle = () => ({ getPropertyValue: k => ({"--you":"#2F5D45","--accent":"#245D63","--rule":"#CFD4CE","--muted":"#6B7679"}[k] || "#000") });
global.window = { AudioContext: function(){}, devicePixelRatio: 2, addEventListener(){} };
global.navigator = { mediaDevices: {} };
global.localStorage = { getItem: () => null, setItem(){} };
global.fetch = () => Promise.reject(new Error("no network in test"));
let raf = 0;
global.requestAnimationFrame = () => ++raf;   // draw once per call, no loop
const mod = new Function(js + "; return { drawViz, setState, pushLevel, wave, waveWho };")();

for (const [cls, name] of [["", "asleep"], ["listen", "listening"], ["hear", "hearing"], ["work", "thinking"], ["speak", "speaking"]]){
  ops.length = 0; bad = 0;
  mod.setState(cls, name);
  if (name === "speaking" || name === "hearing") for (let i = 0; i < 72; i++) mod.pushLevel(0.3 + Math.random()*0.4, name === "speaking" ? "mac" : "you");
  mod.drawViz(1234);
  const bars = ops.filter(o => o[0] === "line").length;
  const colours = [...new Set(ops.filter(o => o[0] === "stroke").map(o => o[1]))];
  console.log(`  ${name.padEnd(10)} bars=${String(bars).padEnd(3)} strokes=${ops.filter(o=>o[0]==="stroke").length}  nonfinite=${bad}  colours=${colours.join(",")}`);
}

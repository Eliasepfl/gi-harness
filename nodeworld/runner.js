"use strict";
/*
 * runner.js -- episode executor CLI (JS mirror of the CONTRACTS §2 runner and of
 * harness/gameverify.run_episode). Node "muscle": builds worlds, runs episodes,
 * emits JSONL. The harness's oracles stay in Python and consume this output.
 *
 * PROTOCOL
 *   stdin : ONE JSON job:
 *     { "source": <game module JS source string>,
 *       "episodes": [ { "seed": int, "actions": [str, ...] }, ... ],
 *       "max_ticks": int,
 *       "frames_every": int|0 }
 *   stdout: ONE JSON line per episode (in order):
 *     { "result": "success|failure|budget|exhausted|error",
 *       "ticks": int,
 *       "checkpoints": { name: tick|null },
 *       "final_snapshot": { name: {pos,vel,angle} },
 *       "frames": [ { "tick": t, "entities": { name: query-dict } }, ... ]  // only if frames_every>0
 *       "error": null|string }
 *   exit  : 0 (episode-level errors are reported in-band, not via exit code)
 *
 * DECISION-TICK SEMANTICS (CONTRACTS §2, K=6):
 *   act(world, action)  then  6 x [ world.step(1); on_step?(world) ]
 *   then latch checkpoints  then check failure  then check success.
 *   Episode ends on success/failure/step-budget/exhausted-actions/error.
 */

const vm = require("node:vm");
const { World } = require("./world.js");

const K_STEPS = 6; // physics steps per decision tick (CONTRACTS §2)

// ===========================================================================
// SANDBOX
// ---------------------------------------------------------------------------
// Two layers, spike-level:
//   (1) static scan: reject source containing dangerous tokens;
//   (2) node:vm context exposing ONLY a frozen Math (plus vm's own JS builtins).
//
// WHAT THIS DEFENDS AGAINST: casual/accidental access to Node capabilities
// (require, process, fs, child_process, eval, dynamic import, the classic
// `constructor.constructor`/`Function(` escape to the Function constructor).
//
// WHAT THIS DOES *NOT* DEFEND AGAINST (spike honesty): node:vm is NOT a security
// boundary for adversarial code. A determined attacker can in principle reach
// out of a vm context (prototype/realm tricks, unbounded CPU/allocation loops
// the scan doesn't catch). The REAL isolation in the production harness is the
// Python side: a separate OS process with a hard timeout + kill (mirrors
// harness/sandbox.run_sandboxed). This runner is the inner, cooperative layer.
// ===========================================================================

const FORBIDDEN_PATTERNS = [
  { re: /\brequire\b/, msg: "require" },
  { re: /\bimport\b/, msg: "import" },
  { re: /\bprocess\b/, msg: "process" },
  { re: /\bglobalThis\b/, msg: "globalThis" },
  { re: /\beval\b/, msg: "eval" },
  { re: /\bFunction\s*\(/, msg: "Function(" },
  { re: /constructor\s*\.\s*constructor/, msg: "constructor.constructor" },
  { re: /\bchild_process\b/, msg: "child_process" },
  { re: /\bfs\b/, msg: "fs" },
  { re: /\b__proto__\b/, msg: "__proto__" },
  { re: /\bReflect\b/, msg: "Reflect" },
  { re: /\bProxy\b/, msg: "Proxy" },
];

// Replace comments and string/template literals with spaces so the token scan
// looks at CODE only. This is the JS analogue of the Python side's AST scan: a
// dangerous identifier/call has to appear as live code (outside strings) to run,
// so a game whose PROMPT string happens to contain "import" is not a false
// positive. Naive by design (spike): it does not fully model every escape or
// nested template-literal edge case, but it is used ONLY for scanning -- the
// ORIGINAL source is what actually executes -- so a stripper glitch can only
// mis-scan, never mis-run.
function stripCommentsAndStrings(src) {
  let out = "";
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    const c2 = src[i + 1];
    if (c === "/" && c2 === "/") {
      while (i < n && src[i] !== "\n") i++;
    } else if (c === "/" && c2 === "*") {
      i += 2;
      while (i < n && !(src[i] === "*" && src[i + 1] === "/")) i++;
      i += 2;
    } else if (c === '"' || c === "'" || c === "`") {
      const q = c;
      i++;
      while (i < n) {
        if (src[i] === "\\") {
          i += 2;
          continue;
        }
        if (src[i] === q) {
          i++;
          break;
        }
        i++;
      }
      out += " ";
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

function scanSource(source) {
  const code = stripCommentsAndStrings(source);
  const violations = [];
  for (const { re, msg } of FORBIDDEN_PATTERNS) {
    if (re.test(code)) violations.push(msg);
  }
  return violations;
}

// A frozen Math so game code cannot monkey-patch shared numeric primitives.
function frozenMath() {
  const m = Object.create(null);
  for (const k of Object.getOwnPropertyNames(Math)) m[k] = Math[k];
  return Object.freeze(m);
}

// Load the game once: evaluate the source in a locked vm context and pull out the
// CONTRACTS §2 symbols. Reused across episodes (mirrors gameverify.load_game).
function loadGame(source) {
  const violations = scanSource(source);
  if (violations.length) {
    throw new Error("sandbox scan rejected source: " + violations.join(", "));
  }
  // Only Math is exposed; vm gives its own Object/Array/JSON/etc. No Node globals.
  const context = vm.createContext({ Math: frozenMath() });
  const harvest =
    "\n;({ TITLE: (typeof TITLE!=='undefined'?TITLE:undefined)," +
    " PROMPT: (typeof PROMPT!=='undefined'?PROMPT:undefined)," +
    " ACTIONS: (typeof ACTIONS!=='undefined'?ACTIONS:undefined)," +
    " build: (typeof build!=='undefined'?build:undefined)," +
    " act: (typeof act!=='undefined'?act:undefined)," +
    " on_step: (typeof on_step!=='undefined'?on_step:undefined)," +
    " failure: (typeof failure!=='undefined'?failure:undefined)," +
    " success: (typeof success!=='undefined'?success:undefined)," +
    " checkpoints: (typeof checkpoints!=='undefined'?checkpoints:undefined) })";
  const script = new vm.Script(source + harvest, { filename: "<game>" });
  const g = script.runInContext(context, { timeout: 5000 });

  for (const req of ["TITLE", "PROMPT", "ACTIONS", "build", "act", "success", "checkpoints"]) {
    if (g[req] === undefined) throw new Error(`game module missing required symbol: ${req}`);
  }
  if (!Array.isArray(g.ACTIONS) || g.ACTIONS.length < 2 || g.ACTIONS.length > 8) {
    throw new Error("ACTIONS must be a list of 2..8 strings");
  }
  return g;
}

// ===========================================================================
// EPISODE
// ===========================================================================
function frameOf(world) {
  const entities = {};
  for (const name of world.entities()) entities[name] = world.query(name);
  return entities;
}

function runEpisode(game, world, actions, maxTicks, framesEvery) {
  const applied = [];
  const latches = {};
  const frames = [];
  let result = null; // set on early termination; classified after the loop

  if (framesEvery > 0) frames.push({ tick: 0, entities: frameOf(world) }); // initial state

  const budget = Math.min(maxTicks, actions.length);
  for (let t = 0; t < budget; t++) {
    const action = actions[t];
    applied.push(action);
    if (action !== null && action !== undefined) game.act(world, action);
    for (let s = 0; s < K_STEPS; s++) {
      world.step(1);
      if (game.on_step) game.on_step(world);
    }
    // Latch BEFORE terminal checks so milestones on the winning tick are recorded.
    if (game.checkpoints) {
      const cps = game.checkpoints(world);
      for (const key of Object.keys(cps)) {
        if (!(key in latches)) latches[key] = null;
        if (latches[key] === null && cps[key]) latches[key] = applied.length;
      }
    }
    if (framesEvery > 0 && applied.length % framesEvery === 0) {
      frames.push({ tick: applied.length, entities: frameOf(world) });
    }
    if (game.failure && game.failure(world)) {
      result = "failure";
      break;
    }
    if (game.success(world)) {
      result = "success";
      break;
    }
  }
  // No early terminal: ran out of the action list ("exhausted") or hit the
  // per-episode tick budget ("budget") -- mirrors gameverify.run_episode.
  if (result === null) result = actions.length < maxTicks ? "exhausted" : "budget";

  const out = {
    result,
    ticks: applied.length,
    checkpoints: latches,
    final_snapshot: world.snapshot(),
    error: null,
  };
  if (framesEvery > 0) out.frames = frames;
  return out;
}

// ===========================================================================
// MAIN
// ===========================================================================
function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (d) => (buf += d));
    process.stdin.on("end", () => resolve(buf));
    process.stdin.on("error", reject);
  });
}

async function main() {
  const raw = await readStdin();
  let job;
  try {
    job = JSON.parse(raw);
  } catch (e) {
    process.stdout.write(JSON.stringify({ result: "error", ticks: 0, checkpoints: {}, final_snapshot: {}, error: "bad job JSON: " + e.message }) + "\n");
    return;
  }

  const maxTicks = job.max_ticks | 0;
  const framesEvery = job.frames_every | 0;
  const episodes = job.episodes || [];

  let game = null;
  let loadError = null;
  try {
    game = loadGame(job.source);
  } catch (e) {
    loadError = e.message;
  }

  const lines = [];
  for (const ep of episodes) {
    if (loadError) {
      lines.push(JSON.stringify({ result: "error", ticks: 0, checkpoints: {}, final_snapshot: {}, error: loadError }));
      continue;
    }
    try {
      const world = new World(ep.seed | 0);
      game.build(world);
      const rec = runEpisode(game, world, ep.actions || [], maxTicks, framesEvery);
      lines.push(JSON.stringify(rec));
    } catch (e) {
      lines.push(JSON.stringify({ result: "error", ticks: 0, checkpoints: {}, final_snapshot: {}, error: String((e && e.stack) || e).split("\n").slice(0, 4).join(" | ") }));
    }
  }
  process.stdout.write(lines.join("\n") + (lines.length ? "\n" : ""));
}

main().then(
  () => process.exit(0),
  (e) => {
    process.stdout.write(JSON.stringify({ result: "error", ticks: 0, checkpoints: {}, final_snapshot: {}, error: "runner fatal: " + String(e) }) + "\n");
    process.exit(0);
  }
);

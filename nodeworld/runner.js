"use strict";
/*
 * runner.js -- episode executor CLI (JS mirror of the CONTRACTS §2 runner and of
 * harness/gameverify.run_episode). Node "muscle": builds worlds, runs episodes,
 * emits JSONL. The harness's oracles stay in Python and consume this output.
 *
 * PROTOCOL
 *   stdin : ONE JSON job. Two modes selected by "mode":
 *
 *   (1) "episodes" (default) -- run rollouts, one JSONL line per episode:
 *     { "mode": "episodes",                 // optional; default
 *       "source": <game module JS source string>,
 *       "episodes": [ { "seed": int, "actions": [str|null, ...] }, ... ],
 *       "max_ticks": int,
 *       "frames_every": int|0,
 *       "escape_margin": number|null }      // if a number, each record adds
 *                                           // nan + oob (bodies out of bounds)
 *     -> stdout: ONE JSON line per episode (in order):
 *       { "result": "success|failure|budget|exhausted|error",
 *         "ticks": int,
 *         "checkpoints": { name: tick|null },
 *         "final_snapshot": { name: {pos,vel,angle} },
 *         "frames": [...],                  // only if frames_every>0
 *         "nan": bool,                      // only if escape_margin is a number
 *         "oob": [name, ...],               // only if escape_margin is a number
 *         "error": null|string }
 *
 *   (2) "check" -- static + goal probes (G0/G2), ONE JSON object on stdout:
 *     { "mode": "check", "source": <game source> }
 *     -> stdout: ONE JSON object of RAW engine facts (thresholds/gating stay in
 *        Python; see runCheck for the exact schema).
 *
 *   exit  : 0 (per-item errors are reported in-band, not via exit code)
 *
 * DECISION-TICK SEMANTICS (CONTRACTS §2, K=6):
 *   act(world, action)  then  6 x [ world.step(1); on_step?(world) ]
 *   then latch checkpoints  then check failure  then check success.
 *   Episode ends on success/failure/step-budget/exhausted-actions/error.
 *
 * BYTE-DETERMINISM: no Date, no Math.random -- the only randomness is the game's
 * world.rng (seeded mulberry32). The same job piped to two node processes yields
 * byte-identical stdout (SPIKE_REPORT.md criterion (c)).
 */

const vm = require("node:vm");
const { World } = require("./world.js");

const K_STEPS = 6; // physics steps per decision tick (CONTRACTS §2)
const WORLD_SEED = 0; // physics seed for check-mode worlds (mirror gameverify)

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

// Evaluate the source in a locked vm context and harvest the CONTRACTS §2
// symbols. Does NOT validate them -- callers decide what "valid" means (episode
// mode requires all symbols; check mode reports which are missing). Throws only
// on a sandbox-scan rejection or a syntax/eval error.
function evalHarvest(source) {
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
    " WORLD_SIZE: (typeof WORLD_SIZE!=='undefined'?WORLD_SIZE:undefined)," +
    " checkpoints: (typeof checkpoints!=='undefined'?checkpoints:undefined) })";
  const script = new vm.Script(source + harvest, { filename: "<game>" });
  return script.runInContext(context, { timeout: 5000 });
}

// Load the game once for episode mode: harvest + validate the required symbols.
// Reused across episodes (mirrors gameverify.load_game).
function loadGame(source) {
  const g = evalHarvest(source);
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

// dynamic (non-static) entity names, insertion order (mirror _dynamic_entities).
function dynamicEntities(world) {
  const out = [];
  for (const name of world.entities()) {
    try {
      if (!world.query(name).static) out.push(name);
    } catch (e) {
      /* skip unqueryable */
    }
  }
  return out;
}

function hadNan(world) {
  for (const e of world.events()) {
    if (e.type === "nan_detected" || e.type === "nan" || e.type === "explosion") return true;
  }
  return false;
}

function runEpisode(game, world, actions, maxTicks, framesEvery, escapeMargin) {
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
    world_size: world.size.slice(),
    error: null,
  };
  if (framesEvery > 0) out.frames = frames;
  // G1 extras: only when the Python side asks (escape_margin is a number). Kept
  // off the default record so determinism/efficacy batches stay lean and the
  // spike bench output is unchanged.
  if (typeof escapeMargin === "number" && Number.isFinite(escapeMargin)) {
    out.nan = hadNan(world);
    out.oob = dynamicEntities(world).filter((n) => !world.in_bounds(n, escapeMargin));
  }
  return out;
}

// ===========================================================================
// CHECK MODE -- raw G0/G2 facts (thresholds + gating live in Python)
// ===========================================================================
// Returns a single object of engine facts the Python G0/G2 layers consume to
// assemble the SAME check dicts they build for pymunk games (so the report
// schema and hints are identical across engines). Gated like the Python funnel:
// downstream fields are present only when upstream gates passed, so the Python
// side reconstructs the same early-return check shape.
// Effective world size for a game: its declared WORLD_SIZE when it looks like
// [w, h] with finite numbers, else the 800x600 default. Bounds VALIDATION
// (min/max) lives in the Python G0 layer; this only guards world construction.
function worldSizeOf(game) {
  const ws = game.WORLD_SIZE;
  if (Array.isArray(ws) && ws.length === 2 &&
      Number.isFinite(ws[0]) && Number.isFinite(ws[1]) && ws[0] > 0 && ws[1] > 0) {
    return [ws[0], ws[1]];
  }
  return [800, 600];
}

function buildFreshWorld(game) {
  const world = new World(WORLD_SEED, worldSizeOf(game));
  game.build(world);
  return world;
}

function probePredicate(game, fn) {
  // Mirror gameverify._check_predicate: two calls on a fresh t=0 world; report
  // is-bool, value, determinism, and whether the snapshot was left unchanged.
  const world = buildFreshWorld(game);
  const before = JSON.stringify(world.snapshot());
  let r1, r2;
  try {
    r1 = fn(world);
    r2 = fn(world);
  } catch (e) {
    return { is_bool: false, value: null, deterministic: false, state_unchanged: false, error: String((e && e.message) || e) };
  }
  const after = JSON.stringify(world.snapshot());
  const isBool = typeof r1 === "boolean" && typeof r2 === "boolean";
  return {
    is_bool: isBool,
    value: isBool ? r1 : null,
    deterministic: r1 === r2,
    state_unchanged: before === after,
    error: null,
  };
}

function probeCheckpoints(game) {
  // Mirror gameverify._check_checkpoints: dict shape, keys, bool values,
  // truthy-at-t0 keys, determinism, snapshot-unchanged.
  const world = buildFreshWorld(game);
  const before = JSON.stringify(world.snapshot());
  let c1, c2;
  try {
    c1 = game.checkpoints(world);
    c2 = game.checkpoints(world);
  } catch (e) {
    return { is_dict: false, keys: [], n: null, non_bool_keys: [], true_keys: [], deterministic: false, state_unchanged: false, error: String((e && e.message) || e) };
  }
  const after = JSON.stringify(world.snapshot());
  const isDict = c1 !== null && typeof c1 === "object" && !Array.isArray(c1) && c2 !== null && typeof c2 === "object" && !Array.isArray(c2);
  if (!isDict) {
    return { is_dict: false, keys: [], n: null, non_bool_keys: [], true_keys: [], deterministic: false, state_unchanged: false, error: null };
  }
  const keys = Object.keys(c1);
  const nonBool = keys.filter((k) => typeof c1[k] !== "boolean");
  const trueKeys = keys.filter((k) => c1[k]);
  return {
    is_dict: true,
    keys,
    n: keys.length,
    non_bool_keys: nonBool,
    true_keys: trueKeys,
    deterministic: JSON.stringify(c1) === JSON.stringify(c2),
    state_unchanged: before === after,
    error: null,
  };
}

function runCheck(source) {
  const out = { mode: "check" };

  // 1. Static scan (G0 sandbox_scan).
  const violations = scanSource(source);
  out.scan = violations;
  if (violations.length) return out;

  // 2. Evaluate + harvest (G0 loads).
  let g;
  try {
    g = evalHarvest(source);
  } catch (e) {
    out.load = { ok: false, error: String((e && e.message) || e) };
    return out;
  }
  out.load = { ok: true, error: null };

  // 3. Required symbols (G0 symbols).
  const required = ["TITLE", "PROMPT", "ACTIONS", "build", "act", "success", "checkpoints"];
  const callable = ["build", "act", "success", "checkpoints"];
  const defined = {};
  for (const s of required) defined[s] = g[s] !== undefined;
  const isCallable = {};
  for (const s of callable) isCallable[s] = typeof g[s] === "function";
  out.symbols = { defined, callable: isCallable };
  const missing = required.filter((s) => !defined[s]);
  const notCallable = callable.filter((s) => defined[s] && !isCallable[s]);
  if (missing.length || notCallable.length) return out;

  // 4. ACTIONS well-formedness (G0 actions). `values` echoes the declared move
  // set so the Python G1/G3 layers can build macro-plans / efficacy batches.
  out.actions = {
    is_list: Array.isArray(g.ACTIONS),
    length: Array.isArray(g.ACTIONS) ? g.ACTIONS.length : null,
    all_str: Array.isArray(g.ACTIONS) && g.ACTIONS.every((a) => typeof a === "string"),
    values: Array.isArray(g.ACTIONS) ? g.ACTIONS.slice() : null,
  };
  const actionsOk = out.actions.is_list && out.actions.length >= 2 && out.actions.length <= 8 && out.actions.all_str;
  if (!actionsOk) return out;

  // 4b. Declared world size (G0 world_size: bounds/shape validated in Python).
  out.world_size = {
    declared: g.WORLD_SIZE === undefined ? null : g.WORLD_SIZE,
    effective: worldSizeOf(g),
  };

  // 5. build(world) runs (G0 builds) -> a queryable world.
  let world;
  try {
    world = buildFreshWorld(g);
  } catch (e) {
    out.build = { ok: false, error: String((e && e.stack ? e.stack.split("\n").slice(0, 3).join(" | ") : e)) };
    return out;
  }
  out.build = { ok: true, error: null };

  // 6. Post-build world facts (G0 controlled / counts / penetration / in_bounds).
  const entities = world.entities();
  out.entities = entities;
  const queries = {};
  for (const name of entities) {
    const q = world.query(name);
    queries[name] = {
      static: q.static,
      sensor: q.sensor,
      controlled: q.controlled,
      in_bounds: world.in_bounds(name, 0.0),
    };
  }
  out.queries = queries;
  const pen = [];
  for (let i = 0; i < entities.length; i++) {
    for (let j = i + 1; j < entities.length; j++) {
      const a = entities[i];
      const b = entities[j];
      if (queries[a].static && queries[b].static) continue;
      const d = world.penetration_depth(a, b) || 0.0;
      if (d > 0.0) pen.push([a, b, d]);
    }
  }
  out.penetration = pen;

  // 7. Goal probes (G2 success / failure / checkpoints), each on a fresh world.
  out.g2 = {
    success: probePredicate(g, g.success),
    failure: g.failure ? probePredicate(g, g.failure) : null,
    checkpoints: probeCheckpoints(g),
  };
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

  // Check mode: one structured object of raw G0/G2 facts.
  if (job.mode === "check") {
    let obj;
    try {
      obj = runCheck(job.source);
    } catch (e) {
      obj = { mode: "check", error: "check fatal: " + String((e && e.stack) || e) };
    }
    process.stdout.write(JSON.stringify(obj) + "\n");
    return;
  }

  // Episode mode (default).
  const maxTicks = job.max_ticks | 0;
  const framesEvery = job.frames_every | 0;
  const escapeMargin = typeof job.escape_margin === "number" ? job.escape_margin : null;
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
      const world = new World(ep.seed | 0, worldSizeOf(game));
      game.build(world);
      const rec = runEpisode(game, world, ep.actions || [], maxTicks, framesEvery, escapeMargin);
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

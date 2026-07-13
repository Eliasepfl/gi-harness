// sample_drift.js -- hand-port of harness/gamegen.py _DRIFT (the "drift" template).
//
// JS mirror of the CONTRACTS §2 game module format. This file is consumed as
// SOURCE TEXT by runner.js (read, sandbox-scanned, then evaluated in a node:vm
// context) -- it is NOT `require()`d, so it declares plain top-level symbols and
// uses NO module wrapper, require, import or exports.
//
// The one JS-vs-Python difference: world.add takes an options OBJECT instead of
// Python keyword arguments (world.add("puck","circle",{pos:[x,y],radius:r,...})).
//
// Design: an air-hockey puck on frictionless ice; four directional impulses nudge
// it onto a sensor pad. Zero gravity, bouncy walls; momentum carries. Random-
// solvable by the seeded macro-action probe.

const TITLE = "Drift";
const PROMPT = "guide the puck across the ice onto the glowing pad";
const ACTIONS = ["left", "right", "up", "down"];

function build(world) {
  world.set_gravity(0.0, 0.0);
  world.add("puck", "circle", { pos: [180.0, 150.0], radius: 16.0, mass: 1.0, friction: 0.2, elasticity: 0.6 });
  world.control("puck");
  world.add("pad", "box", { pos: [560.0, 430.0], size: [200.0, 200.0], static: true, sensor: true });
  world.add("w_left", "segment", { pos: [0.0, 0.0], a: [8.0, 0.0], b: [8.0, 600.0], static: true, elasticity: 0.9 });
  world.add("w_right", "segment", { pos: [0.0, 0.0], a: [792.0, 0.0], b: [792.0, 600.0], static: true, elasticity: 0.9 });
  world.add("w_bottom", "segment", { pos: [0.0, 0.0], a: [0.0, 8.0], b: [800.0, 8.0], static: true, elasticity: 0.9 });
  world.add("w_top", "segment", { pos: [0.0, 0.0], a: [0.0, 592.0], b: [800.0, 592.0], static: true, elasticity: 0.9 });
}

function act(world, action) {
  const j = 70.0;
  if (action === "left") world.impulse("puck", [-j, 0.0]);
  else if (action === "right") world.impulse("puck", [j, 0.0]);
  else if (action === "up") world.impulse("puck", [0.0, j]);
  else if (action === "down") world.impulse("puck", [0.0, -j]);
}

function success(world) {
  const p = world.query("puck");
  const z = world.query("pad");
  const cx = (p.bbox[0] + p.bbox[2]) / 2.0;
  const cy = (p.bbox[1] + p.bbox[3]) / 2.0;
  return z.bbox[0] <= cx && cx <= z.bbox[2] && z.bbox[1] <= cy && cy <= z.bbox[3];
}

function checkpoints(world) {
  const p = world.query("puck").pos;
  const dx = p[0] - 180.0;
  const dy = p[1] - 150.0;
  return {
    moved_off_start: dx * dx + dy * dy > 1600.0,
    crossed_midline: p[0] > 400.0,
    entered_upper_half: p[1] > 300.0,
  };
}

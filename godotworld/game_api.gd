# game_api.gd -- the GameAPI CONTRACT base class for the GDScript lane.
#
# The GDScript twin of the declarative SPEC.md lane: instead of emitting DATA a
# frozen interpreter reads, a generated game is ONE .gd file that `extends GameAPI`
# and IMPLEMENTS the contract methods below. Any game speaking this vocabulary is
# verifiable through the SAME G0-G4 funnel via the serve seam (serve_game.gd +
# harness/verify/gd_exec.py), so the certificate stays ours while the medium
# becomes code (notes/engines/GDSCRIPT_LANE.md).
#
# This base is FROZEN + TRUSTED (it ships in the project, never generated). It
# provides three things and NOTHING else:
#   * `rng`   -- a RandomNumberGenerator the HARNESS seeds before build(); a game
#                that needs randomness MUST draw from it (the G0 banned-API scanner
#                rejects the global randi/randf/randomize -- unseeded nondeterminism).
#   * the physics-space handle (`get_space()`), for a game that wants direct-space
#                queries; most games just add RigidBody2D/Area2D children.
#   * documentation of the HARD BANS (enforced statically by the scanner, so this
#                is a contract reminder, not the security boundary).
#
# The base deliberately does NOT define the contract methods, so the serve host's
# contract probe (`has_method`) can tell an incomplete game apart from a complete
# one -- a game that forgets state() is rejected at G0, not silently mis-run.
#
# ------------------------------------------------------------------------------
# THE CONTRACT -- a generated game MUST implement every method (signatures exact):
#
#   func build(world_seed: int) -> void
#       Construct the scene UNDER self (add_child RigidBody2D / StaticBody2D /
#       Area2D + CollisionShape2D children). Determinism: draw ANY randomness from
#       `self.rng` (already seeded with world_seed). Called once per episode after
#       a full free+rebuild; must be idempotent given the seed.
#
#   func act(action: String) -> void
#       Apply ONE decision-tick input (e.g. an impulse on the controlled body).
#       `action` is one of actions(); the host steps physics K=6 frames after.
#
#   func state() -> Dictionary
#       A TYPED snapshot the host reads without touching the scene:
#         {"bodies": [ {"name": String, "pos": [x, y], "vel": [vx, vy],
#                       "angle": float, "controlled": bool, "static": bool}, ... ],
#          "flags": {String: bool},          # optional latch mirror (cosmetic)
#          "world_size": [w, h],             # optional; defaults to [800, 600]
#          ...custom scalars (float/bool)... }
#       Exactly ONE body must be {"controlled": true, "static": false}; >= 2 bodies.
#       MUST be pure (no scene mutation) -- the host calls it repeatedly.
#
#   func checkpoints() -> Dictionary
#       1..6 snake_case milestones -> bool, ALL false at t=0. Pure (no mutation);
#       track progress in per-step latch flags updated during physics, NOT here.
#
#   func is_success() -> bool / func is_failure() -> bool
#       Pure bool terminal predicates; both false at t=0.
#
#   func actions() -> Array           # -> Array[String], 2..8 distinct verbs
#
# BANS (the G0 scanner is a HARD fail on any of these in the source):
#   OS.* / FileAccess / DirAccess / HTTP* / TCPServer / StreamPeer* / PacketPeer* /
#   Thread / Mutex / Engine.get_singleton / ClassDB / Expression /
#   load() / preload() / Time.* (wall clock) / randi/randf/randomize (use self.rng) /
#   get_tree().quit. The game runs ONLY in-container on a SCRUBBED env (no keys).
# ------------------------------------------------------------------------------

extends Node
class_name GameAPI

# Seeded by the serve host (inst.rng.seed = world_seed) BEFORE build() -- a game
# that reads it draws a deterministic stream; two builds at the same seed match.
var rng := RandomNumberGenerator.new()

# The 2D physics space RID of the world the game is built into. The host sets it
# after the game enters the tree; a game only needs it for direct-space queries.
var _space_rid: RID = RID()


func get_space() -> RID:
	return _space_rid

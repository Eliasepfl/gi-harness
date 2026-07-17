# chord_util.gd -- the SINGLE canonicalization boundary for the GDScript host side.
#
# CHORD PIVOT, Phase 1 (host-only). A decision-tick action arrives over the wire as
# either:
#   * a plain String  -- a single verb, e.g. "thrust_up". BYTE-IDENTICAL to every
#     pre-chord witness/act message; nothing about single-verb traffic changes.
#   * an Array of verb Strings -- a CHORD, e.g. ["thrust_forward", "thrust_up"]:
#     multiple keys pressed in the SAME decision tick (Elias-approved).
#
# apply() canonicalizes a chord by sorting a LOCAL copy lexicographically, then calls
# the game's act() ONCE PER VERB in that sorted order -- all synchronously, BEFORE the
# tick's K physics frames (the caller runs the physics burst after apply() returns). A
# single verb is exactly one act() call.
#
# PHASE 2 -- the IDLE tick. An EMPTY Array [] is a well-formed "press nothing" tick:
# apply() makes ZERO act() calls and the caller steps physics as normal. That is the
# natural fall-through of the per-verb loop (no verbs -> no calls). Whether an empty
# chord is LEGAL on the wire is a CAPABILITY the serve host guards (its `allow_idle`
# init flag): with the capability off the host rejects [] as a protocol error BEFORE
# calling apply(); with it on the host lets [] through to apply() as an idle tick. The
# policy therefore lives at the host boundary; apply() itself only executes verbs.
# `is_empty_chord()` is the shared predicate the host uses for that guard.
#
# CANONICAL ORDER. Sorting means ["a","b"] and ["b","a"] produce the identical act()
# sequence, so the sender's element order never matters -> determinism. The flip side
# (documented, not hidden -- see notes/engines/CHORD_PIVOT.md): order-SENSITIVE game
# logic sees verbs in sorted order, never the sender's order. Impulse/velocity
# composition (the common case) is commutative, so this is invisible there.
#
# ONE HELPER PER SIDE. Both hosts -- serve_game.gd (certification) and capture_host.gd
# (render/replay) -- call ChordUtil.apply(). Keeping the rule in exactly one place is
# why serve and capture cannot drift: capture parity is structural, not coincidental.
#
# NO `class_name`. A global class name resolves through Godot's EDITOR-GENERATED
# `.godot/global_script_class_cache.cfg`, which is gitignored -- so a warm worktree
# resolves `ChordUtil` while a fresh checkout does not, and the host dies with
# "Identifier ChordUtil not declared" at parse time. The hosts preload this file by
# path instead (`const ChordUtil = preload("res://chord_util.gd")`), which is explicit
# and cache-independent -- the same idiom runner.gd already uses for its sensors.
extends RefCounted


# True IFF `action` is the Phase-2 IDLE tick: an empty Array (press nothing). The serve
# host uses this to guard the empty chord against its `allow_idle` init capability BEFORE
# stepping. A String (even "") is never an idle chord -- only an empty Array is.
static func is_empty_chord(action) -> bool:
	return typeof(action) == TYPE_ARRAY and (action as Array).is_empty()


# Apply one decision-tick action to `game`. `action` is a String (single verb) or an
# Array of verb Strings (a chord; an EMPTY Array is the idle tick -> zero act() calls).
# Never awaits: every act() lands before the caller's physics frames, exactly as a lone
# verb does today.
static func apply(game: Node, action) -> void:
	if typeof(action) == TYPE_ARRAY:
		# Canonicalize on a LOCAL copy so the caller's array is never mutated. An empty
		# Array iterates zero times -> ZERO act() calls (the idle tick); a lone verb is
		# one call, byte-identical to the single-verb String path below.
		var verbs := []
		for v in action:
			verbs.append(str(v))
		verbs.sort()                      # lexicographic (code-point) -> matches Python
		for v in verbs:
			game.act(v)
	else:
		# Single verb: exactly one act(), byte-identical to pre-chord behaviour.
		game.act(str(action))

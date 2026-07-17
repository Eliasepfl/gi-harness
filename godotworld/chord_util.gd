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
# CANONICAL ORDER. Sorting means ["a","b"] and ["b","a"] produce the identical act()
# sequence, so the sender's element order never matters -> determinism. The flip side
# (documented, not hidden -- see notes/engines/CHORD_PIVOT.md): order-SENSITIVE game
# logic sees verbs in sorted order, never the sender's order. Impulse/velocity
# composition (the common case) is commutative, so this is invisible there.
#
# ONE HELPER PER SIDE. Both hosts -- serve_game.gd (certification) and capture_host.gd
# (render/replay) -- call ChordUtil.apply(). Keeping the rule in exactly one place is
# why serve and capture cannot drift: capture parity is structural, not coincidental.
class_name ChordUtil
extends RefCounted


# Apply one decision-tick action to `game`. `action` is a String (single verb) or an
# Array of verb Strings (a chord). Never awaits: every act() lands before the caller's
# physics frames, exactly as a lone verb does today.
static func apply(game: Node, action) -> void:
	if typeof(action) == TYPE_ARRAY:
		# Canonicalize on a LOCAL copy so the caller's array is never mutated.
		var verbs := []
		for v in action:
			verbs.append(str(v))
		verbs.sort()                      # lexicographic (code-point) -> matches Python
		for v in verbs:
			game.act(v)
	else:
		# Single verb: exactly one act(), byte-identical to pre-chord behaviour.
		game.act(str(action))

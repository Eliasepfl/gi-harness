"""Unit tests for the G3 solidity scan (sustained interpenetration detector)."""

from harness.verify.gameverify import SOLIDITY_FRAC, SOLIDITY_TICKS, _solidity_scan


def _frame(tick, ents):
    return {"tick": tick, "entities": ents}


def _q(x, y, w, h, *, static=False, sensor=False):
    return {"bbox": [x - w / 2, y - h / 2, x + w / 2, y + h / 2],
            "static": static, "sensor": sensor}


def test_sustained_deep_overlap_is_flagged():
    # 30px ball 16px inside a 40px crate (>50% of 30) for 3 consecutive ticks.
    frames = [_frame(t, {"ball": _q(100 + 0.1 * t, 50, 30, 30),
                         "crate": _q(107, 50, 40, 40)}) for t in range(3)]
    worst = _solidity_scan(frames)
    assert worst is not None
    assert set(worst["pair"]) == {"ball", "crate"}
    assert worst["depth"] > SOLIDITY_FRAC * 30
    assert worst["frac"] > SOLIDITY_FRAC


def test_transient_single_tick_overlap_is_physics_not_failure():
    deep = {"ball": _q(100, 50, 30, 30), "crate": _q(107, 50, 40, 40)}
    apart = {"ball": _q(300, 50, 30, 30), "crate": _q(107, 50, 40, 40)}
    frames = [_frame(0, apart), _frame(1, deep), _frame(2, apart),
              _frame(3, deep), _frame(4, apart)]
    assert SOLIDITY_TICKS >= 2
    assert _solidity_scan(frames) is None


def test_shallow_contact_overlap_is_fine():
    # 3px overlap on 30px bodies (10%) — normal resting contact slop.
    frames = [_frame(t, {"ball": _q(100, 50, 30, 30),
                         "crate": _q(133.5, 50, 40, 40)}) for t in range(5)]
    assert _solidity_scan(frames) is None


def test_sensor_and_static_static_pairs_ignored():
    frames = [_frame(t, {
        "zone": _q(100, 50, 60, 60, static=True, sensor=True),
        "ball": _q(100, 50, 30, 30),                      # inside the sensor: fine
        "wall_a": _q(300, 50, 40, 200, static=True),
        "wall_b": _q(310, 50, 40, 200, static=True),      # static x static: fine
    }) for t in range(4)]
    assert _solidity_scan(frames) is None


def test_zero_thickness_segment_pairs_skipped():
    frames = [_frame(t, {"rope": _q(100, 50, 200, 0.0, static=True),
                         "ball": _q(100, 50, 30, 30)}) for t in range(4)]
    assert _solidity_scan(frames) is None


def test_poly_and_rotated_boxes_are_not_judged():
    # A ball riding a poly ramp overlaps the ramp's AABB massively on honest
    # contact — polys and tilted boxes must be excluded, not flagged.
    ramp = _q(100, 50, 120, 120, static=True)
    ramp["shape"] = "poly"
    tilted = _q(300, 50, 40, 40)
    tilted["angle"] = 0.7  # ~40deg mid-topple crate
    frames = [_frame(t, {"ramp": ramp,
                         "ball": _q(100, 50, 30, 30),
                         "tilted": tilted,
                         "ball2": _q(305, 50, 30, 30)}) for t in range(4)]
    assert _solidity_scan(frames) is None


def test_right_angle_rotated_box_still_judged():
    # An upright crate rotated a full 90deg keeps a tight AABB — still judged.
    crate = _q(107, 50, 40, 40)
    crate["angle"] = 1.5707963  # pi/2
    frames = [_frame(t, {"crate": crate,
                         "ball": _q(100, 50, 30, 30)}) for t in range(3)]
    worst = _solidity_scan(frames)
    assert worst is not None and set(worst["pair"]) == {"ball", "crate"}


def test_worst_offender_reported():
    frames = [_frame(t, {
        "a": _q(100, 50, 30, 30), "b": _q(119, 50, 40, 40),    # 16px deep (53%)
        "c": _q(300, 50, 30, 30), "d": _q(310.5, 50, 40, 40),  # 24.5px deep (82%)
    }) for t in range(3)]
    worst = _solidity_scan(frames)
    assert worst is not None
    assert set(worst["pair"]) == {"c", "d"}

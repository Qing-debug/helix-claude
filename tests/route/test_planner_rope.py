"""Tests for `planner.rope_move` (rope fix plan, Task 6; H88, H93): the order of moves for a character read on a rope.

The positions and marks are Fossil Foundry 2's: rope 107 (top 43, bottom 61) and rope 46 (top 28, bottom 46).
"""

from pathlib import Path

import pytest

from src.classes.classes import Kanna
from src.controller.node import Node
from src.controller.rope_file import RopeMark, read_ropes, rope_file_for
from src.input.protocol import Key
from src.route.planner import Move, MoveKind, rope_move

CSV = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"
LEFT_ROPE, RIGHT_ROPE = read_ropes(rope_file_for(CSV)).unwrap().ropes
REACH_V = Kanna().max_vertical_traversal_dist

UP = Move(MoveKind.TELEPORT, Key.UP, None)
DOWN = Move(MoveKind.TELEPORT, Key.DOWN, None)


def _target(x: int = 105, y: int = 42) -> Node:
    return Node("blue_node", (x, y))


def _order(mark, pos, target=None, blink_tries=2, upto=6):
    target = _target() if target is None else target
    return [rope_move(mark, pos, target, tries, REACH_V, blink_tries) for tries in range(upto)]


def test_the_nearer_platform_above_gets_two_blinks_then_a_jump_off_then_none():
    """(106, 49) on rope 107: 6 up to the top, 12 down to the bottom."""
    target = _target(113, 60)
    moves = _order(RIGHT_ROPE, (106, 49), target)
    assert moves == [UP, UP, Move(MoveKind.JUMP_OFF, Key.RIGHT, None), None, None, None]


def test_the_nearer_platform_below_gets_blinks_down():
    """(106, 55): 6 down, 12 up."""
    moves = _order(RIGHT_ROPE, (106, 55))
    assert moves[:2] == [DOWN, DOWN]
    assert moves[2] is not None and moves[2].kind is MoveKind.JUMP_OFF and moves[3] is None


def test_equal_gaps_go_down():
    mark = RopeMark(107, 40, 60)
    assert rope_move(mark, (106, 50), _target(), 0, REACH_V, 2) == DOWN


def test_rope_46_blinks_up_from_35():
    """(46, 35) on rope 46: 7 up to `top_y` 28, 11 down to `bottom_y` 46."""
    assert rope_move(LEFT_ROPE, (46, 35), _target(60, 28), 0, REACH_V, 2) == UP


def test_a_platform_only_one_side_in_reach_is_the_one_blinked_to():
    reach = int(REACH_V)
    mark = RopeMark(107, 0, 2 * reach + 2)
    y = reach                       # up `reach` (in reach), down `reach` + 2 (not)
    assert rope_move(mark, (106, y), _target(), 1, REACH_V, 2) == UP
    third = rope_move(mark, (106, y), _target(), 2, REACH_V, 2)
    assert third is not None and third.kind is MoveKind.JUMP_OFF


def test_a_gap_of_exactly_reach_blinks_and_one_more_does_not():
    reach = int(REACH_V)
    mark = RopeMark(107, 0, 100)
    assert rope_move(mark, (106, reach), _target(), 0, REACH_V, 2) == UP               # up == reach_v
    beyond = rope_move(mark, (106, reach + 1), _target(), 0, REACH_V, 2)               # up == reach_v + 1, down far
    assert beyond is not None and beyond.kind is MoveKind.JUMP_OFF


def test_both_ends_beyond_reach_is_the_jump_off_only():
    gap = int(REACH_V) + 1                                    # never a literal: derived from the class
    mark = RopeMark(107, 50 - gap, 50 + gap)
    moves = _order(mark, (106, 50), upto=3)
    assert moves[0] is not None and moves[0].kind is MoveKind.JUMP_OFF
    assert moves[1] is None and moves[2] is None


@pytest.mark.parametrize("target_x, key", [(100, Key.LEFT), (112, Key.RIGHT), (106, Key.RIGHT)],
                         ids=["target left", "target right", "level goes right"])
def test_the_jump_off_goes_toward_the_targets_x(target_x, key):
    assert rope_move(RIGHT_ROPE, (106, 49), _target(target_x, 42), 2, REACH_V, 2) == Move(MoveKind.JUMP_OFF, key, None)


def test_jump_off_keys_are_only_left_or_right():
    for x in range(95, 120):
        move = rope_move(RIGHT_ROPE, (106, 49), _target(x, 42), 2, REACH_V, 2)
        assert move is not None and move.direction in (Key.LEFT, Key.RIGHT) and move.walk_ms is None


def test_blink_tries_sets_the_number_of_blinks():
    assert _order(RIGHT_ROPE, (106, 49), blink_tries=1, upto=3) == [UP, Move(MoveKind.JUMP_OFF, Key.LEFT, None), None]   # the default target is left of x 106
    four = _order(RIGHT_ROPE, (106, 49), blink_tries=4, upto=7)
    assert four[:4] == [UP] * 4 and four[4] is not None and four[4].kind is MoveKind.JUMP_OFF and four[5] is None


def test_rope_move_takes_no_rng_and_gives_the_same_answer_twice():
    args = (RIGHT_ROPE, (106, 49), _target(), 1, REACH_V, 2)
    assert rope_move(*args) == rope_move(*args) == UP

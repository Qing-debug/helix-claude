"""Move executor: play one planned move through the input layer (plan contract C8)."""

from returns.result import Result

from src.input.input_layer import ActionRecord, InputLayer
from src.input.link import InputError
from src.route.planner import Move, MoveKind


def execute(layer: InputLayer, move: Move, stimulus_ns: int | None) -> Result[ActionRecord, InputError]:
    """Make exactly one layer call for `move` and return its Result unchanged.

    `stimulus_ns` is the layer's stimulus-clock reading that the reaction wait is measured from; it is passed through
    untouched, and None means no reaction wait.
    """
    if move.kind is MoveKind.WALK:
        return layer.walk(move.direction, move.walk_ms, stimulus_ns)
    if move.kind is MoveKind.TELEPORT:
        return layer.teleport(move.direction, stimulus_ns)
    return layer.drop(stimulus_ns)

"""Check S3b on route previews (spec section 7; plan Task RV-4), shared with RV-5.

A flat test helper, imported as `from preview_checks import s3b_problems, follows` (`tests/CLAUDE.md`, "Layout"). It
has no `test_` prefix, so pytest does not collect it. Both functions take only what any caller has: the route graph,
a preview and its target.
"""

import numpy as np
from returns.pipeline import is_successful

from src.controller.node import Node
from src.route.planner import Decision, next_move
from src.route.route_graph import RouteGraph
from src.route.route_preview import Hop, Preview, preview


def _xy(node: Node | None) -> tuple[int, int] | None:
    return None if node is None else node.minimap_position


def s3b_problems(rg: RouteGraph, pv: Preview, target: Node) -> list[str]:
    """One message per failed S3b check on `pv` (a preview toward `target` on `rg`), `[]` when S3b holds.

    The checks: `pv.complete`; for each hop k from 1 (0-based), `next_move` at hop k-1's landing makes one of hop k's
    options, toward the same node, at a bit-equal cost to go, and the preview from that landing has the hops from k
    on and the same `complete`; at the last landing, `next_move` and `preview` both say arrived. A Failure where a
    Success is expected is a message, never a raise.
    """
    where = f"preview {pv.pos} -> {target.minimap_position}"
    problems: list[str] = []
    if not pv.complete:
        problems.append(f"{where}: not complete after {len(pv.hops)} hops")
    for k in range(1, len(pv.hops)):
        at, hop = pv.hops[k - 1].landing, pv.hops[k]
        planned = next_move(rg, at, target, np.random.default_rng(k))
        if not is_successful(planned):
            problems.append(f"{where}, hop {k}: next_move at {at} failed: {planned.failure()}")
        else:
            d = planned.unwrap()
            if d.move not in hop.options:
                problems.append(f"{where}, hop {k}: next_move at {at} makes {d.move}, the hop offers {hop.options}")
            if d.toward is not hop.toward:
                problems.append(
                    f"{where}, hop {k}: next_move at {at} heads for {_xy(d.toward)}, the hop for {_xy(hop.toward)}"
                )
            if d.cost_to_go_s != hop.cost_to_go_s:
                problems.append(
                    f"{where}, hop {k}: next_move at {at} costs {d.cost_to_go_s!r}, the hop {hop.cost_to_go_s!r}"
                )
        sub_result = preview(rg, at, target)
        if not is_successful(sub_result):
            problems.append(f"{where}, hop {k}: preview at {at} failed: {sub_result.failure()}")
            continue
        sub = sub_result.unwrap()
        if sub.hops != pv.hops[k:]:
            problems.append(f"{where}, hop {k}: preview at {at} has hops {_hops_text(sub.hops)}, "
                            f"the rest of this one {_hops_text(pv.hops[k:])}")
        if sub.complete != pv.complete:
            problems.append(f"{where}, hop {k}: preview at {at} has complete {sub.complete}, this one {pv.complete}")
    if pv.hops:
        last = pv.hops[-1].landing
        planned = next_move(rg, last, target, np.random.default_rng(len(pv.hops)))
        if not is_successful(planned):
            problems.append(f"{where}, last landing {last}: next_move failed: {planned.failure()}")
        elif planned.unwrap() != Decision(None, 0.0, None):
            problems.append(f"{where}, last landing {last}: next_move is not arrived: {planned.unwrap()}")
        sub_result = preview(rg, last, target)
        if not is_successful(sub_result):
            problems.append(f"{where}, last landing {last}: preview failed: {sub_result.failure()}")
        elif not sub_result.unwrap().arrived:
            problems.append(f"{where}, last landing {last}: preview is not arrived")
    return problems


def follows(path: tuple[Node, ...], hops: tuple[Hop, ...]) -> bool:
    """Every hop's `toward` is in `path` (by identity), at a path index no lower than the hop before's.

    A measure of how often the hops leave the route `path` names, not part of S3b's pass/fail (RV3-D5 A2).
    """
    lowest = 0
    for hop in hops:
        index = next((i for i, node in enumerate(path) if node is hop.toward), None)
        if index is None or index < lowest:
            return False
        lowest = index
    return True


def _hops_text(hops: tuple[Hop, ...]) -> str:
    return "; ".join(f"{hop.options} -> {_xy(hop.toward)} @ {hop.landing} from {hop.from_pos}" for hop in hops)

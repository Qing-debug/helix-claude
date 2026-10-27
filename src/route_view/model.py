"""The route view's pure model (route view plan task RV-6, contract V5): what step k of a run looks like.

`RunModel` is built from a `RunHeader` and `StepEvent`s (`src.route.step_log`), the route graph rebuilt from the
folder's map and the header's params (as `walk.py` builds it), and the frame index. `step(k)` answers "what does step k
look like" as a `StepView` for the window and the follower; `render_text` renders the same answer as plain text for
golden tests. No Tk, no Windows call, no subprocess (the git head comes in as `current_git_head`); the module imports
only what spec 5.8 allows, which import-linter enforces ("route_view boundaries").

`RunModel.extend` works on the new events only, so the live follower can call it every poll. For each event it keeps a
small slot (the leg, the target, the character, the plan in force, the recent moves, the why line) and builds the
"why" lines at once; a `planned` event's route preview is computed there too (cached per position and target).

Where the plan is silent (each decided here, local and reversible):
- The planned why line's route is `preview.path` and the panel's Route line is the hops' route (`hop_route`); where the
  hops leave `path` (RV3-D5 A2's 9 FF2 pairs; none in the sim runs) the two differ, by design (RV4 m7/q14).
- When the logged hop 1 differs from the preview's (an old folder, or code that changed), aim and route are the
  preview's (PF81 m14).
- `recent` is kept across legs (the faint trail of the last 5 finished moves of the run); only the plan in force is left
  out. A move needs a `settled` with a position to count.
- "on <...>" (Position line, planned why line): a node `on #5`, a segment `on o7-o8`; past a platform end the phrase is
  `left of <right>` or `right of <left>` and off the graph `off the graph`, both without "on".
- Without a graph the Position line is `Position (x, y)`, the settled line has no aim words (`settled (x, y); plan again
  from there`), and a route-changed line is never built. With no plan in force the panel has no Route and no Hops lines.
- Panel lines: `Position unknown` while the leg has no logged position; no `Target` line before the first `leg`.
- A route-changed line whose last settle is exactly on the aim ends `(on the aim)` (V5: "on the aim" in place of the
  bracket). Several other starts give `; via <label> <c> s` once each, inside the one bracket.
- A `sent` with no key changes reads `sent: no key changes`; a logged move whose kind or direction is unknown to the
  planner is shown as the logged words with keys `?` and takes no part in a route-changed test.
- A time before the run's first event is shown as `00:00.000`.
- `ended` is also True from the start for a legs-only run (origin "legs"): it has no `end` event but is complete
  (`RunSteps.ended`, V1), so the window must not follow it live.
- A frame index line must be a JSON object with an int `t_ns` and a str `file`; a blank line is a bad line (as
  `StepTail`).
- `RunModel.open` catches every exception of the graph build (the contract says it never fails): the text becomes the
  `route not shown:` warning.
"""

import bisect
import contextlib
import hashlib
import io
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from returns.pipeline import is_successful
from returns.result import Failure, Result, Success

from src.classes.classes import CharacterClass, Kanna
from src.controller.controller import BuildOutcome, MapleAgent
from src.controller.map_csv import read_editor_nodes
from src.controller.node import Node
from src.input.protocol import Key, Step, transitions
from src.route.locate import attach
from src.route.params import MovementParams
from src.route.planner import Move, MoveKind
from src.route.route_graph import RouteGraph
from src.route.route_preview import Hop, Preview, preview
from src.route.step_log import MAP_COPY, RunHeader, StepEvent, read_run

SCALES = (2, 3, 4)
DEFAULT_SCALE = 4  # H51 (RV1-D8 B)
WARN_MAP = "map changed since this run: the route shown may not be the one the bot used"  # spec 5.3, word for word
WARN_CODE = "code changed since this run"  # spec 5.3
FRAMES_DIR = "frames"
FRAME_INDEX = "index.jsonl"  # record_minimap.py:8-10 (walk records into <out>/frames)

STATE_WORDS = {
    "leg": "starting leg", "planned": "planned", "sent": "moving", "settled": "settled", "read_failed": "no character",
    "no_plan": "cannot plan", "stall": "stalled", "reached": "reached", "leg_end": "leg ended", "end": "ended",
}

Pos = tuple[int, int]
_CLASSES: dict[str, type[CharacterClass]] = {"kanna": Kanna}  # the model's own copy: it may not import walk.py (5.8)
_NO_GRAPH = ": route not shown"
_PLAY_DEFAULT_MS = 100
_RECENT = 5
_COIN_TEXT = "drop or teleport down, 50/50"
_COIN_KEYS = "Down+Alt or Down+E"


@dataclass(frozen=True)
class NodeMark:
    pos: Pos
    blue: bool
    label: str  # "#1".."#n" blue nodes in rotation order (= file order); "o1".."om" orange nodes in file order


@dataclass(frozen=True)
class HopView:
    n: int  # 1-based
    from_pos: Pos
    landing: Pos
    text: str  # "teleport right" | "walk right 1.4 s" | "drop" | "teleport down" | "drop or teleport down, 50/50"
    keys: str  # "Right+E", "Up+E", "hold Right", "Down+Alt", "Down+Alt or Down+E"
    sent: bool  # True for hop 1 of a logged plan


@dataclass(frozen=True)
class StepView:
    index: int  # into RunModel.events
    ev: str
    lap: int | None
    leg: int | None  # of the leg the step is in; None before the first `leg`
    target: NodeMark | None  # that leg's `to`; a NodeMark with label "(x, y)" if no node is there
    character: Pos | None  # newest logged position in this leg up to the step, else None
    aim: Pos | None  # hops[0].landing of the plan in force
    route: tuple[Pos, ...]  # the plan in force: its pos, then each hop's landing
    hops: tuple[HopView, ...]
    recent: tuple[tuple[Pos, Pos], ...]  # up to 5 (planned pos, settled pos), oldest first, plan in force excluded
    panel: tuple[str, ...]  # side panel lines (spec 5.4)
    state: str  # the STATE_WORDS entry of `ev`
    why_line: int  # index into RunModel.why of the step's last line
    frame: str | None  # file name in frames/ of the newest frame with t_ns <= the step's time


@dataclass(frozen=True, slots=True)
class _Slot:
    lap: int | None
    leg: int | None
    target: Pos | None
    character: Pos | None
    plan: int | None  # event index of the `planned` in force
    recent: tuple[tuple[Pos, Pos], ...]
    why_line: int
    time_ns: int | None  # the event's own t_ns, or the previous event's when None


# ---------------------------------------------------------------------------------------------------------------
# words


def _xy(pos: Pos) -> str:
    return f"({pos[0]}, {pos[1]})"


def _pair(pos: Pos) -> str:
    return f"{pos[0]},{pos[1]}"


def _cap(key: Key) -> str:
    return (key.name or "?").capitalize()


def _dir(direction: Key | None) -> str:
    return (direction.name or "?").lower() if direction is not None else "?"


def _move_text(move: Move) -> str:
    if move.kind is MoveKind.WALK:
        return f"walk {_dir(move.direction)} {(move.walk_ms or 0.0) / 1000:.1f} s"
    if move.kind is MoveKind.TELEPORT:
        return f"teleport {_dir(move.direction)}"
    return "drop"


def _move_keys(move: Move) -> str:
    if move.kind is MoveKind.DROP:
        return "Down+Alt"
    name = _cap(move.direction) if move.direction is not None else "?"
    return f"hold {name}" if move.kind is MoveKind.WALK else f"{name}+E"


def _options_words(options: tuple[Move, ...]) -> tuple[str, str]:
    if len(options) == 1:
        return _move_text(options[0]), _move_keys(options[0])
    return _COIN_TEXT, _COIN_KEYS  # V4: two options are always (DROP, TELEPORT DOWN)


def _logged_move(data: Any) -> Move | None:
    """The `Move` a `planned` event logged, or None when its kind or direction is not one the planner has."""
    try:
        kind = MoveKind(data["kind"])
        name = data["dir"]
        direction = None if name is None else Key[name]
    except (ValueError, KeyError):
        return None
    return Move(kind, direction, data["walk_ms"])


def _logged_words(data: Any) -> tuple[str, str]:
    move = _logged_move(data)
    if move is not None:
        return _move_text(move), _move_keys(move)
    return f"{data['kind']} {str(data['dir']).lower()}", "?"


def _past(move: Move | None, aim: Pos, pos: Pos) -> int | None:
    """How far `pos` is past `aim` along the move's axis: positive past, negative short; None for an unknown move."""
    if move is None:
        return None
    if move.direction in (Key.LEFT, Key.RIGHT):
        return (pos[0] - aim[0]) * (1 if move.direction == Key.RIGHT else -1)
    return (pos[1] - aim[1]) * (-1 if move.direction == Key.UP else 1)


def _param(params: Mapping[str, Any], name: str, default: float) -> float:
    """A number from the header's params, else `default` (a header the walk did not write may lack it or hold junk)."""
    value = params.get(name, default)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _dedupe(positions: Sequence[Pos]) -> tuple[Pos, ...]:
    out: list[Pos] = []
    for pos in positions:
        if not out or out[-1] != pos:
            out.append(pos)
    return tuple(out)


def hop_route(hops: Sequence[Hop]) -> tuple[Pos, ...]:
    """The hops' `toward` positions, consecutive repeats dropped (RV3-D5 A2: the route the hops take, not `path`)."""
    return _dedupe([hop.toward.minimap_position for hop in hops])


def route_changed(prev: Preview, move: Move, toward: Pos | None, cur: Preview | None) -> bool:
    """True when the plan logged after `prev` is not what `prev` expected next (spec 5.4, last row; RV3-D8 B).

    `prev` is the preview of the earlier plan, `move` and `toward` the later plan's logged move and target node, `cur`
    the later plan's own preview. The walk length is not compared.
    """
    if len(prev.hops) < 2:
        return True
    expected = prev.hops[1]
    if all((option.kind, option.direction) != (move.kind, move.direction) for option in expected.options):
        return True
    if toward is not None and toward != expected.toward.minimap_position:
        return True
    return cur is not None and hop_route(cur.hops) != hop_route(prev.hops[1:])


# ---------------------------------------------------------------------------------------------------------------
# frame index


def read_frame_index(data: bytes) -> tuple[list[tuple[int, str]], int]:
    """`(frames, bad_lines)` of the complete lines of a `frames/index.jsonl` (a torn last line is left out).

    `"\\r\\n"` is accepted: `record_minimap.py:78` opens the index in text mode, so its lines end `"\\r\\n"` on Windows.
    """
    frames: list[tuple[int, str]] = []
    bad = 0
    for raw in data[: data.rfind(b"\n") + 1].split(b"\n")[:-1]:
        try:
            item = json.loads(raw.removesuffix(b"\r").decode("utf-8"))
            t_ns, name = item["t_ns"], item["file"]
        except (ValueError, KeyError, TypeError):
            bad += 1
            continue
        if isinstance(t_ns, int) and not isinstance(t_ns, bool) and isinstance(name, str):
            frames.append((t_ns, name))
        else:
            bad += 1
    return frames, bad


# ---------------------------------------------------------------------------------------------------------------
# the graph


def _build_graph(map_path: Path, header: RunHeader) -> Result[tuple[RouteGraph, MapleAgent], str]:
    """The route graph as `walk.py:164-178` builds it, from the header's class and params."""
    maker = _CLASSES.get(header.character)
    if maker is None:
        return Failure(f"unknown class {header.character}")
    try:
        params = MovementParams(**header.params)
        agent = MapleAgent(characterClass=maker())
        with contextlib.redirect_stdout(io.StringIO()):  # the CSV parser prints its progress
            loaded = agent.loadMap(map_path)
        if not is_successful(loaded):
            return Failure(f"cannot load map {map_path}: {loaded.failure()}")
        if loaded.unwrap() is not BuildOutcome.BUILT or agent.pathGraph is None:
            return Failure(f"map {map_path} loaded but no graph was built: {loaded.unwrap().name}")
        built = RouteGraph.build(agent.pathGraph, params)
    except Exception as e:  # RunModel.open never fails: any problem becomes a warning
        return Failure(f"{e}" if isinstance(e, TypeError) else f"{type(e).__name__}: {e}")
    if not is_successful(built):
        return Failure(f"route graph: {built.failure()}")
    return Success((built.unwrap(), agent))


class RunModel:
    folder: Path
    header: RunHeader
    origin: str
    ended: bool
    end_reason: str | None
    nodes: tuple[NodeMark, ...]
    rg: RouteGraph | None
    minimap_size: tuple[int, int] | None
    warnings: tuple[str, ...]
    events: list[StepEvent]
    why: list[str]
    frames: list[tuple[int, str]]

    def __init__(self, folder: Path, header: RunHeader, origin: str, *, rg: RouteGraph | None,
                 nodes: tuple[NodeMark, ...], node_at: dict[Pos, Node], minimap_size: tuple[int, int] | None,
                 warnings: tuple[str, ...]) -> None:
        self.folder, self.header, self.origin = folder, header, origin
        self.rg, self.nodes, self.minimap_size, self.warnings = rg, nodes, minimap_size, warnings
        self.ended, self.end_reason = origin == "legs", None  # a legs-only run has no `end` event and is complete
        self.events, self.why, self.frames = [], [], []
        self._node_at = node_at
        self._mark_at = {mark.pos: mark for mark in reversed(nodes)}  # the first node in file order wins
        self._settle_s = _param(header.params, "settle_timeout_s", MovementParams.settle_timeout_s)
        self._stall_s = _param(header.params, "stall_s", MovementParams.stall_s)
        self._slots: list[_Slot] = []
        self._previews: dict[tuple[Pos, Pos], Result[Preview, str]] = {}
        self._plan_preview: dict[int, Preview | None] = {}  # planned event index -> its preview
        self._frame_times: list[int] = []
        self._planned_idx: list[int] = []
        self._leg_idx: list[int] = []
        self._legs: dict[tuple[int, int], int] = {}
        self._t0: int | None = None
        self._last_t: int | None = None
        self._lap: int | None = None
        self._leg: int | None = None
        self._target: Pos | None = None
        self._character: Pos | None = None
        self._plan: int | None = None  # event index of the plan in force
        self._since: list[Pos | None] = []  # the `settled` positions since the plan in force
        self._completed: list[tuple[int, Pos, Pos]] = []  # (planned index, planned pos, settled pos), settle order
        self._collapse: tuple[str, int] | None = None  # (why, repeats so far) of the read_failed line being repeated
        self._handlers: dict[str, Callable[[int, StepEvent, str], list[str]]] = {
            "leg": self._on_leg, "planned": self._on_planned, "sent": self._on_sent, "settled": self._on_settled,
            "read_failed": self._on_read_failed, "no_plan": self._on_no_plan, "stall": self._on_stall,
            "reached": self._on_reached, "leg_end": self._on_leg_end, "end": self._on_end,
        }

    @classmethod
    def open(cls, folder: Path, header: RunHeader, origin: str, *, repo_root: Path,
             current_git_head: str | None) -> "RunModel":
        """An empty model for `header`: the graph, the nodes, the minimap size and the warnings. Never raises."""
        folder = Path(folder)
        map_path = folder / header.map if header.map == MAP_COPY else Path(header.map)
        if not map_path.is_absolute() and header.map != MAP_COPY:
            map_path = Path(repo_root) / map_path
        warnings: list[str] = []
        problem: str | None = None
        try:
            data: bytes | None = map_path.read_bytes()
        except FileNotFoundError:
            data, problem = None, f"map file {map_path} not found"
        except OSError as e:
            data, problem = None, f"cannot read map file {map_path}: {e}"
        if data is not None and header.map_sha256 is not None and hashlib.sha256(data).hexdigest() != header.map_sha256:
            warnings.append(WARN_MAP)
        if header.git_head and current_git_head and header.git_head != current_git_head:
            warnings.append(WARN_CODE)
        rg: RouteGraph | None = None
        marks: list[NodeMark] = []
        node_at: dict[Pos, Node] = {}
        if problem is None:
            built = _build_graph(map_path, header)
            if is_successful(built):
                rg, agent = built.unwrap()
                blue = {id(node) for node in agent.map_rotation_order}
                nodes = list(agent.node_map.values())
                blues = [node for node in nodes if id(node) in blue]
                oranges = [node for node in nodes if id(node) not in blue]
                marks = [NodeMark(n.minimap_position, True, f"#{i}") for i, n in enumerate(blues, 1)]
                marks += [NodeMark(n.minimap_position, False, f"o{i}") for i, n in enumerate(oranges, 1)]
                for node in blues + oranges:
                    node_at.setdefault(node.minimap_position, node)
            else:
                problem = built.failure()
        if problem is not None:
            warnings.append(f"route not shown: {problem}")
        size: tuple[int, int] | None = None
        if data is not None:
            read = read_editor_nodes(str(map_path))
            if is_successful(read):
                size = read.unwrap()[2].get("minimap_size")
        return cls(folder, header, origin, rg=rg, nodes=tuple(marks), node_at=node_at, minimap_size=size,
                   warnings=tuple(warnings))

    # -----------------------------------------------------------------------------------------------------------
    # labels and places

    def label(self, pos: Pos) -> str:
        mark = self._mark_at.get(pos)
        return mark.label if mark is not None else _xy(pos)

    def _where(self, pos: Pos) -> str:
        """Where `pos` is on the graph, as the phrase that follows "Position (x, y)," (needs a graph)."""
        assert self.rg is not None
        found = attach(self.rg, pos)
        if not is_successful(found):
            return "off the graph"
        anchor = found.unwrap()
        if anchor.node is not None:
            return f"on {self.label(anchor.node.minimap_position)}"
        if anchor.left is not None and anchor.right is not None:
            return f"on {self.label(anchor.left.minimap_position)}-{self.label(anchor.right.minimap_position)}"
        if anchor.right is not None:
            return f"left of {self.label(anchor.right.minimap_position)}"
        if anchor.left is not None:
            return f"right of {self.label(anchor.left.minimap_position)}"
        return "off the graph"

    def _stamp(self, t_ns: int | None) -> str:
        ms = 0 if t_ns is None or self._t0 is None else max(0, (t_ns - self._t0 + 500_000) // 1_000_000)
        seconds, ms = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{ms:03d}" if hours else f"{minutes:02d}:{seconds:02d}.{ms:03d}"

    # -----------------------------------------------------------------------------------------------------------
    # events in

    def extend(self, events: Sequence[StepEvent]) -> None:
        """Append `events`: the why lines, the slots and the indexes are built for the new ones only."""
        for event in events:
            i = len(self.events)
            self.events.append(event)
            if event.t_ns is not None:
                if self._t0 is None:
                    self._t0 = event.t_ns
                self._last_t = event.t_ns
            lines = self._handlers[event.ev](i, event, self._stamp(self._last_t))
            if event.ev != "read_failed":
                self._collapse = None
            self.why.extend(lines)
            self._slots.append(_Slot(self._lap, self._leg, self._target, self._character, self._plan,
                                     self._recent_now(), len(self.why) - 1, self._last_t))

    def add_frames(self, frames: Sequence[tuple[int, str]]) -> None:
        for frame in frames:
            if self._frame_times and frame[0] < self._frame_times[-1]:  # not in time order: keep the list sorted
                at = bisect.bisect_right(self._frame_times, frame[0])
                self._frame_times.insert(at, frame[0])
                self.frames.insert(at, frame)
            else:
                self._frame_times.append(frame[0])
                self.frames.append(frame)

    def _recent_now(self) -> tuple[tuple[Pos, Pos], ...]:
        tail = [move for move in self._completed[-(_RECENT + 1):] if move[0] != self._plan]
        return tuple((planned, settled) for _, planned, settled in tail[-_RECENT:])

    def _preview_for(self, pos: Pos) -> tuple[Preview | None, str]:
        """The preview from `pos` to the leg's target (cached), and the rest of the planned why line when none."""
        if self.rg is None:
            return None, _NO_GRAPH
        if self._target is None:
            return None, ": no target yet"
        node = self._node_at.get(self._target)
        if node is None:
            return None, f": target {_xy(self._target)} is not on this map"
        key = (pos, self._target)
        result = self._previews.get(key)
        if result is None:
            result = self._previews[key] = preview(self.rg, pos, node)
        if is_successful(result):
            return result.unwrap(), ""
        return None, f" {self._where(pos)}; {result.failure()}"

    # -----------------------------------------------------------------------------------------------------------
    # one handler per event: update the state, return the why lines

    def _on_leg(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        d = event.data
        self._lap, self._leg, self._target = d["lap"], d["leg"], d["to"]
        self._character, self._plan, self._since = None, None, []
        self._legs.setdefault((d["lap"], d["leg"]), i)
        self._leg_idx.append(i)
        name, where = self.label(d["to"]), _xy(d["to"])
        return [f"{stamp} lap {d['lap']} leg {d['leg']}: target {name if name == where else f'{name} {where}'}"]

    def _on_planned(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        d = event.data
        pos = d["pos"]
        pv, tail = self._preview_for(pos)
        lines: list[str] = []
        if self._plan is not None and self._since:
            changed = self._route_changed_line(stamp, self._plan, d, pv)
            if changed is not None:
                lines.append(changed)
        if pv is None:
            lines.append(f"{stamp} at {_xy(pos)}{tail}")
        elif pv.arrived:
            lines.append(f"{stamp} at {_xy(pos)} {self._where(pos)}; already at the target")
        else:
            costs = {id(start.node): start.cost_s for start in pv.starts}
            chosen = pv.start
            assert chosen is not None
            vias = "".join(f"; via {self.label(s.node.minimap_position)} {s.cost_s:.2f} s"
                           for s in pv.starts if s.node is not chosen)
            route = " ".join(self.label(node.minimap_position) for node in pv.path)
            lines.append(f"{stamp} at {_xy(pos)} {self._where(pos)}; start {self.label(chosen.minimap_position)} "
                         f"({costs[id(chosen)]:.2f} s{vias}); route {route}")
        text, _ = _logged_words(d)
        toward = d["toward"] or (pv.hops[0].toward.minimap_position if pv is not None and pv.hops else None)
        plan = f"{stamp} plan: {text}"
        if toward is not None:
            plan += f" toward {self.label(toward)}"
        if pv is not None and pv.hops:
            plan += f" (aim x {pv.hops[0].landing[0]})"
        lines.append(plan)
        self._plan_preview[i] = pv
        self._planned_idx.append(i)
        self._plan, self._since, self._character = i, [], pos
        return lines

    def _route_changed_line(self, stamp: str, p_idx: int, q: Any, q_preview: Preview | None) -> str | None:
        p_preview = self._plan_preview.get(p_idx)
        move = _logged_move(q)
        if p_preview is None or move is None or not route_changed(p_preview, move, q["toward"], q_preview):
            return None
        if len(p_preview.hops) < 2:
            expected = "arrival"
        else:
            hop = p_preview.hops[1]
            expected = f"{_options_words(hop.options)[0]} toward {self.label(hop.toward.minimap_position)}"
        now = _move_text(move)
        toward = q["toward"] or (q_preview.hops[0].toward.minimap_position if q_preview and q_preview.hops else None)
        if toward is not None:
            now += f" toward {self.label(toward)}"
        landed = [pos for pos in self._since if pos is not None]
        bracket = ""
        if landed and p_preview.hops:
            d = _past(_logged_move(self.events[p_idx].data), p_preview.hops[0].landing, landed[-1])
            if d is not None:
                bracket = " (on the aim)" if d == 0 else f" (landed {abs(d)} px {'past' if d > 0 else 'short'})"
        return f"{stamp} route changed: expected next {expected}, now {now}{bracket}"

    def _on_sent(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        d = event.data
        if d["input"] != "ok":
            return [f"{stamp} send failed: {d['input']}"]
        changes = transitions([Step(delta, Key(mask)) for delta, mask in d["keys"]])
        words = []
        for n, (t_100us, key, is_down) in enumerate(changes):
            word = f"{_cap(key)} {'down' if is_down else 'up'}"
            words.append(word if n == 0 else f"{word} +{round(t_100us / 10)} ms")
        waited = "" if d["waited_ms"] is None else f" (waited {d['waited_ms']:.0f} ms)"
        return [f"{stamp} sent: {', '.join(words) or 'no key changes'}{waited}"]

    def _on_settled(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        d = event.data
        pos = d["pos"]
        aim_words = ""
        if pos is not None and self._plan is not None:
            pv = self._plan_preview.get(self._plan)
            if pv is not None and pv.hops:
                miss = _past(_logged_move(self.events[self._plan].data), pv.hops[0].landing, pos)
                if miss is not None:
                    aim_words = ("on the aim" if miss == 0 else
                                 f"{miss} px past the aim" if miss > 0 else f"{-miss} px short of the aim")
        if pos is None:
            text = f"did not settle within {self._settle_s:g} s"
        else:
            text = f"settled {_xy(pos)}{': ' + aim_words if aim_words else ''}; plan again from there"
        self._since.append(pos)
        if pos is not None:
            self._character = pos
            if self._plan is not None:
                self._completed.append((self._plan, self.events[self._plan].data["pos"], pos))
        return [f"{stamp} {text}{' (stop requested)' if d['stopped'] else ''}"]

    def _on_read_failed(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        why = event.data["why"]
        text = "no new frame yet" if why == "stale" else f"no character on the minimap ({why})"
        if self._collapse is not None and self._collapse[0] == why:
            count = self._collapse[1] + 1
            self._collapse = (why, count)
            self.why[-1] = f"{stamp} {text} x{count}"
            return []
        self._collapse = (why, 1)
        return [f"{stamp} {text}"]

    def _on_no_plan(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        d = event.data
        self._character, self._plan, self._since = d["pos"], None, []
        return [f"{stamp} cannot plan from {_xy(d['pos'])}: {d['why']}"]

    def _on_stall(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        best = event.data["best_cost_s"]
        cost = "no plan yet" if best is None else f"best cost to go {best:.2f} s"
        return [f"{stamp} no progress for {self._stall_s:g} s ({cost}): stall counted"]

    def _on_reached(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        self._character, self._plan, self._since = event.data["pos"], None, []
        return [f"{stamp} reached {self.label(self._target) if self._target is not None else 'the target'}"]

    def _on_leg_end(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        d = event.data
        self._plan, self._since = None, []
        return [f"{stamp} leg ended: {d['outcome']} ({d['misses']} misses, {d['off_graph']} off-graph, "
                f"{d['stalls']} stalls)"]

    def _on_end(self, i: int, event: StepEvent, stamp: str) -> list[str]:
        d = event.data
        self.ended, self.end_reason = True, d["reason"]
        code = "" if d["code"] is None else f" (exit code {d['code']})"
        return [f"{stamp} run ended: {d['reason'] or 'no reason'}{code}"]

    # -----------------------------------------------------------------------------------------------------------
    # step k

    def step(self, k: int) -> StepView:
        if not 0 <= k < len(self.events):
            raise IndexError(f"step {k} is not in 0..{len(self.events) - 1}")
        slot, event = self._slots[k], self.events[k]
        target = None
        if slot.target is not None:
            target = self._mark_at.get(slot.target) or NodeMark(slot.target, False, _xy(slot.target))
        panel: list[str] = []
        if target is not None:
            where = _xy(target.pos)
            panel.append(f"Target  {target.label if target.label == where else f'{target.label} {where}'}")
        if slot.character is None:
            panel.append("Position unknown")
        elif self.rg is None:
            panel.append(f"Position {_xy(slot.character)}")
        else:
            panel.append(f"Position {_xy(slot.character)}, {self._where(slot.character)}")
        hops: list[HopView] = []
        route: tuple[Pos, ...] = ()
        aim: Pos | None = None
        if slot.plan is not None:
            hops, route, aim, lines = self._plan_view(slot.plan)
            panel.extend(lines)
        frame = None
        if slot.time_ns is not None:
            at = bisect.bisect_right(self._frame_times, slot.time_ns) - 1
            frame = self.frames[at][1] if at >= 0 else None
        return StepView(k, event.ev, slot.lap, slot.leg, target, slot.character, aim, route, tuple(hops), slot.recent,
                        tuple(panel), STATE_WORDS[event.ev], slot.why_line, frame)

    def _plan_view(self, p_idx: int) -> tuple[list[HopView], tuple[Pos, ...], Pos | None, list[str]]:
        """The hops, route, aim and panel lines (Route, Hops, one line per hop) of the plan at event `p_idx`."""
        data = self.events[p_idx].data
        pos, pv = data["pos"], self._plan_preview.get(p_idx)
        text, keys = _logged_words(data)
        first_landing = pv.hops[0].landing if pv is not None and pv.hops else None
        hops = [HopView(1, pos, first_landing or pos, text, keys, True)]
        route: tuple[Pos, ...] = ()
        towards: list[Pos] = []
        if data["toward"] is not None:
            towards.append(data["toward"])
        elif pv is not None and pv.hops:
            towards.append(pv.hops[0].toward.minimap_position)
        if pv is not None:
            route = (pos, *(hop.landing for hop in pv.hops))
            for n, hop in enumerate(pv.hops[1:], start=2):
                hops.append(HopView(n, hop.from_pos, hop.landing, *_options_words(hop.options), False))
                towards.append(hop.toward.minimap_position)
        lines: list[str] = []
        names = [self.label(p) for p in _dedupe(towards)]
        if names:
            lines.append(f"Route   you -> {' -> '.join(names)}   {data['cost_to_go_s']:.1f} s")
        lines.append("Hops")
        lines.extend(f"  {h.n} {h.text}   {h.keys}{'   SENT' if h.sent else ''}" for h in hops)
        return hops, route, first_landing, lines

    # -----------------------------------------------------------------------------------------------------------
    # navigation

    def seek(self, k: int, unit: str, delta: int) -> int:
        """The step `delta` (+1 or -1) events, moves (a `planned`) or legs (a `leg`) from `k`; `k` when none."""
        if unit == "event":
            moved = k + (1 if delta > 0 else -1)
            return moved if 0 <= moved < len(self.events) else k
        if unit not in ("move", "leg"):
            raise ValueError(f"unknown seek unit {unit!r}")
        indexes = self._planned_idx if unit == "move" else self._leg_idx
        if delta > 0:
            at = bisect.bisect_right(indexes, k)
            return indexes[at] if at < len(indexes) else k
        at = bisect.bisect_left(indexes, k)
        return indexes[at - 1] if at > 0 else k

    def find_leg(self, lap: int, leg: int) -> int | None:
        return self._legs.get((lap, leg))

    def play_delay_ms(self, k: int, speed: float) -> int:
        """How long to show step `k` when playing at `speed`: the logged gap to step k+1, `100` when unknown."""
        if k + 1 >= len(self.events):
            return _PLAY_DEFAULT_MS
        now, then = self.events[k].t_ns, self.events[k + 1].t_ns
        if now is None or then is None:
            return _PLAY_DEFAULT_MS
        return max(0, round((then - now) / 1e6 / speed))


def load_folder(folder: Path, *, repo_root: Path, current_git_head: str | None) -> Result[RunModel, str]:
    """`read_run` + the frame index + `RunModel.open` + `extend` + `add_frames`; Failure only when `read_run` fails."""
    folder = Path(folder)
    run = read_run(folder)
    if not is_successful(run):
        return Failure(run.failure())
    steps = run.unwrap()
    model = RunModel.open(folder, steps.header, steps.origin, repo_root=repo_root, current_git_head=current_git_head)
    model.extend(steps.events)
    try:
        index = (folder / FRAMES_DIR / FRAME_INDEX).read_bytes()
    except OSError:
        index = b""
    model.add_frames(read_frame_index(index)[0])
    return Success(model)


def render_text(model: RunModel, view: StepView) -> str:
    """The golden-text form of a step (S5): one value per line, positions as `x,y`, `none` for an empty one."""

    def points(items: Sequence[Pos]) -> str:
        return " ".join(_pair(p) for p in items) or "none"

    def number(value: int | None) -> str:
        return "none" if value is None else str(value)

    target = view.target.label if view.target is not None else "none"
    lines = [
        f"title: {view.state} lap {number(view.lap)} leg {number(view.leg)} -> {target}",
        f"warnings: {' | '.join(model.warnings) or 'none'}",
        "nodes: " + (" ".join(f"{m.label}@{_pair(m.pos)}" for m in model.nodes) or "none"),
        f"target: {target}",
        f"character: {points([view.character] if view.character is not None else [])}",
        f"aim: {points([view.aim] if view.aim is not None else [])}",
        f"route: {points(view.route)}",
        "recent: " + (" ".join(f"{_pair(a)}>{_pair(b)}" for a, b in view.recent) or "none"),
        *view.panel,
        f"why: {model.why[view.why_line]}",
        f"frame: {view.frame or 'none'}",
    ]
    return "\n".join(lines)

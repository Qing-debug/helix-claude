"""Tests of the route view's pure model (route view plan task RV-6, tests 1-11; contract V5).

The hand-written run `G` and the folder helpers are in `view_runs.py`. Test 1 holds the plan's golden text: every line
that comes from the model's own rules is written by hand from V5; the lines that come from `rg.path` and `rg.cost_to`
(the planned line's `start ... route ...`) were written in after one run, checked against `preview()` (the test also
asserts them against labels computed from `preview(...)` directly) and are not regenerated.
"""

import dataclasses
import time

import pytest
from returns.pipeline import is_successful
from returns.result import Failure
from view_runs import FF2, G, GIT_HEAD, REPO_ROOT, default_header, event_lines, make_folder, make_legs_folder, ns

from src.input.protocol import Key
from src.route.params import MovementParams
from src.route.planner import Move, MoveKind
from src.route.route_preview import preview
from src.route.step_log import STEPS_FILE, header_line, read_run
from src.route_view.model import (
    FRAME_INDEX, FRAMES_DIR, WARN_CODE, WARN_MAP, RunModel, hop_route, load_folder, read_frame_index, render_text,
    route_changed,
)


def _load(folder, git_head=GIT_HEAD):
    result = load_folder(folder, repo_root=REPO_ROOT, current_git_head=git_head)
    assert is_successful(result), result
    return result.unwrap()


@pytest.fixture
def model(tmp_path):
    return _load(make_folder(tmp_path))


def _node(model, pos):
    return next(n for n in model.rg.graph.nodes if n.minimap_position == pos)


def _lines(model, k):
    """`render_text` of step k as a list of lines, without the `nodes:` line (test 3 checks it once)."""
    return [line for line in render_text(model, model.step(k)).split("\n") if not line.startswith("nodes: ")]


T1_PANEL = [
    "Target  #6 (101, 24)",
    "Position (49, 28), on #5",
    "Route   you -> o7 -> o8 -> #6   5.2 s",
    "Hops",
    "  1 teleport right   Right+E   SENT",
    "  2 walk right 1.4 s   hold Right",
    "  3 teleport right   Right+E",
    "  4 walk right 0.4 s   hold Right",
]

GOLDEN = {
    1: ["title: planned lap 0 leg 5 -> #6", "warnings: none", "target: #6", "character: 49,28", "aim: 68,27",
        "route: 49,28 68,27 79,24 98,24 101,24", "recent: none", *T1_PANEL,
        "why: 00:00.100 plan: teleport right toward o7 (aim x 68)", "frame: none"],
    3: ["title: settled lap 0 leg 5 -> #6", "warnings: none", "target: #6", "character: 68,27", "aim: 68,27",
        "route: 49,28 68,27 79,24 98,24 101,24", "recent: none",
        "Target  #6 (101, 24)", "Position (68, 27), on o7", *T1_PANEL[2:],
        "why: 00:00.900 settled (68, 27): on the aim; plan again from there", "frame: none"],
    6: ["title: settled lap 0 leg 5 -> #6", "warnings: none", "target: #6", "character: 80,24", "aim: 79,24",
        "route: 68,27 79,24 98,24 101,24", "recent: 49,28>68,27",
        "Target  #6 (101, 24)", "Position (80, 24), on o8", "Route   you -> o8 -> #6   4.6 s", "Hops",
        "  1 walk right 1.4 s   hold Right   SENT", "  2 teleport right   Right+E", "  3 walk right 0.4 s   hold Right",
        "why: 00:02.600 settled (80, 24): 1 px past the aim; plan again from there", "frame: none"],
    7: ["title: planned lap 0 leg 5 -> #6", "warnings: none", "target: #6", "character: 80,24", "aim: 99,24",
        "route: 80,24 99,24", "recent: 49,28>68,27 68,27>80,24",
        "Target  #6 (101, 24)", "Position (80, 24), on o8", "Route   you -> #6   3.4 s", "Hops",
        "  1 walk right 2.6 s   hold Right   SENT",
        "why: 00:02.650 plan: walk right 2.6 s toward #6 (aim x 99)", "frame: none"],
    12: ["title: no character lap 0 leg 5 -> #6", "warnings: none", "target: #6", "character: 80,24", "aim: 99,24",
         "route: 80,24 99,24", "recent: 49,28>68,27 68,27>80,24",
         "Target  #6 (101, 24)", "Position (80, 24), on o8", "Route   you -> #6   3.4 s", "Hops",
         "  1 walk right 2.6 s   hold Right   SENT",
         "why: 00:05.100 no character on the minimap (miss) x3", "frame: none"],
    14: ["title: cannot plan lap 0 leg 5 -> #6", "warnings: none", "target: #6", "character: 86,37", "aim: none",
         "route: none", "recent: 49,28>68,27 68,27>80,24",
         "Target  #6 (101, 24)", "Position (86, 37), off the graph",
         "why: 00:05.200 cannot plan from (86, 37): off-graph: (86, 37)", "frame: none"],
    17: ["title: planned lap 0 leg 6 -> #7", "warnings: none", "target: #7", "character: 101,24", "aim: 105,24",
         "route: 101,24 105,24 105,42", "recent: 49,28>68,27 68,27>80,24",
         "Target  #7 (105, 42)", "Position (101, 24), on #6", "Route   you -> #7   1.1 s", "Hops",
         "  1 walk right 0.5 s   hold Right   SENT", "  2 drop or teleport down, 50/50   Down+Alt or Down+E",
         "why: 00:06.200 plan: walk right 0.5 s toward #7 (aim x 105)", "frame: none"],
    20: ["title: planned lap 0 leg 6 -> #7", "warnings: none", "target: #7", "character: 105,24", "aim: 105,42",
         "route: 105,24 105,42", "recent: 49,28>68,27 68,27>80,24 101,24>105,24",
         "Target  #7 (105, 42)", "Position (105, 24), on #6-o10", "Route   you -> #7   0.5 s", "Hops",
         "  1 drop   Down+Alt   SENT",
         "why: 00:07.000 plan: drop toward #7 (aim x 105)", "frame: none"],
    25: ["title: ended lap 0 leg 6 -> #7", "warnings: none", "target: #7", "character: 105,42", "aim: none",
         "route: none", "recent: 49,28>68,27 68,27>80,24 101,24>105,24 105,24>105,42",
         "Target  #7 (105, 42)", "Position (105, 42), on #7",
         "why: 00:08.000 run ended: max_laps (exit code 0)", "frame: none"],
}

WHY_G = [
    "00:00.000 lap 0 leg 5: target #6 (101, 24)",
    "00:00.100 at (49, 28) on #5; start #5 (5.17 s); route #5 o7 o8 o9 #6",
    "00:00.100 plan: teleport right toward o7 (aim x 68)",
    "00:00.331 sent: Right down, E down +31 ms, E up +112 ms, Right up +140 ms (waited 228 ms)",
    "00:00.900 settled (68, 27): on the aim; plan again from there",
    "00:00.950 at (68, 27) on o7; start o7 (4.38 s); route o7 o8 o9 #6",
    "00:00.950 plan: walk right 1.4 s toward o8 (aim x 79)",
    "00:01.160 sent: Right down, Right up +1375 ms (waited 205 ms)",
    "00:02.600 settled (80, 24): 1 px past the aim; plan again from there",
    "00:02.650 route changed: expected next teleport right toward #6, now walk right 2.6 s toward #6 (landed 1 px past)",
    "00:02.650 at (80, 24) on o8; start o8 (2.75 s); route o8 o9 #6",
    "00:02.650 plan: walk right 2.6 s toward #6 (aim x 99)",
    "00:02.850 sent: Right down, Right up +2625 ms (waited 190 ms)",
    "00:04.900 did not settle within 2 s",
    "00:05.100 no character on the minimap (miss) x3",
    "00:05.150 no new frame yet",
    "00:05.200 cannot plan from (86, 37): off-graph: (86, 37)",
    "00:06.000 leg ended: timed_out (3 misses, 1 off-graph, 0 stalls)",
    "00:06.100 lap 0 leg 6: target #7 (105, 42)",
    "00:06.200 at (101, 24) on #6; start #6 (1.05 s); route #6 #7",
    "00:06.200 plan: walk right 0.5 s toward #7 (aim x 105)",
    "00:06.400 sent: Right down, Right up +500 ms (waited 196 ms)",
    "00:06.950 settled (105, 24): on the aim; plan again from there",
    "00:07.000 at (105, 24) on #6-o10; start #6 (1.55 s; via o10 1.55 s); route #6 #7",
    "00:07.000 plan: drop toward #7 (aim x 105)",
    "00:07.100 sent: Down down, Alt down +30 ms, Alt up +110 ms, Down up +135 ms (waited 148 ms)",
    "00:07.600 settled (105, 42): on the aim; plan again from there",
    "00:07.650 reached #7",
    "00:07.650 leg ended: reached (0 misses, 0 off-graph, 0 stalls)",
    "00:08.000 run ended: max_laps (exit code 0)",
]

# the index into WHY_G of each step's last line
LAST_LINE = [0, 2, 3, 4, 6, 7, 8, 11, 12, 13, 14, 14, 14, 15, 16, 17, 18, 20, 21, 22, 24, 25, 26, 27, 28, 29]


# 1 ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("k", sorted(GOLDEN))
def test_1_golden_text(model, k):
    assert _lines(model, k) == GOLDEN[k]


def test_1_the_route_changed_line_opens_step_7(model):
    assert model.why[model.step(7).why_line - 2] == WHY_G[9]
    assert WHY_G[9].startswith("00:02.650 route changed: expected next teleport right toward #6, now walk right 2.6 s "
                               "toward #6 (landed 1 px past)")


@pytest.mark.parametrize("k, pos, target", [(1, (49, 28), (101, 24)), (4, (68, 27), (101, 24)),
                                            (7, (80, 24), (101, 24)), (17, (101, 24), (105, 42)),
                                            (20, (105, 24), (105, 42))])
def test_1_the_start_and_route_parts_agree_with_preview(model, k, pos, target):
    pv = preview(model.rg, pos, _node(model, target)).unwrap()
    costs = {s.node.minimap_position: s.cost_s for s in pv.starts}
    start = pv.start.minimap_position
    via = "".join(f"; via {model.label(p)} {c:.2f} s" for p, c in costs.items() if p != start)
    route = " ".join(model.label(n.minimap_position) for n in pv.path)
    suffix = f"; start {model.label(start)} ({costs[start]:.2f} s{via}); route {route}"
    assert model.why[model.step(k).why_line - 1].endswith(suffix)


# 2 ---------------------------------------------------------------------------------------------------------------


def test_2_the_why_lines_of_g_in_full(model):
    assert model.why == WHY_G
    assert [model.step(k).why_line for k in range(len(G))] == LAST_LINE


def test_2_a_repeated_read_failed_line_is_one_line_for_all_its_steps(model):
    assert model.step(10).why_line == model.step(11).why_line == model.step(12).why_line
    assert model.step(13).why_line == model.step(12).why_line + 1


def test_2_stall_send_failed_and_stop_lines(tmp_path):
    events = [("leg", 1.0, 0, 5, (49, 28), (101, 24)), ("stall", 2.0, None), ("stall", 3.0, 1.2345),
              ("sent", 4.0, "LINK_LOST", None, None, ()), ("sent", 4.5, "ok", None, None, ()),
              ("settled", 5.0, (49, 28), True), ("settled", 6.0, None, True), ("no_plan", 7.0, (1, 2), "boom")]
    model = _load(make_folder(tmp_path, events))
    assert model.why[1:] == [
        "00:01.000 no progress for 10 s (no plan yet): stall counted",
        "00:02.000 no progress for 10 s (best cost to go 1.23 s): stall counted",
        "00:03.000 send failed: LINK_LOST",
        "00:03.500 sent: no key changes",
        "00:04.000 settled (49, 28); plan again from there (stop requested)",
        "00:05.000 did not settle within 2 s (stop requested)",
        "00:06.000 cannot plan from (1, 2): boom",
    ]


def test_2_times_from_one_hour_have_hours(tmp_path):
    events = [("leg", 1.0, 0, 5, (49, 28), (101, 24)), ("reached", 3601.5, (101, 24)), ("end", 3725.25, None, None)]
    assert _load(make_folder(tmp_path, events)).why[1:] == ["1:00:00.500 reached #6", "1:02:04.250 run ended: no reason"]


# 3 ---------------------------------------------------------------------------------------------------------------


def test_3_nodes_and_targets(model):
    blue = [("#1", (113, 60)), ("#2", (82, 60)), ("#3", (48, 61)), ("#4", (58, 46)), ("#5", (49, 28)),
            ("#6", (101, 24)), ("#7", (105, 42))]
    assert [(m.label, m.pos) for m in model.nodes[:7]] == blue and all(m.blue for m in model.nodes[:7])
    assert [m.label for m in model.nodes[7:]] == [f"o{i}" for i in range(1, 15)]
    assert not any(m.blue for m in model.nodes[7:])
    positions = {m.label: m.pos for m in model.nodes}
    assert positions["o1"] == (65, 60) and positions["o7"] == (66, 28) and positions["o14"] == (105, 60)
    assert model.minimap_size == (162, 82) and model.warnings == ()
    assert [model.step(k).target.label for k in range(26)] == ["#6"] * 16 + ["#7"] * 10
    nodes_line = render_text(model, model.step(1)).split("\n")[2]
    assert nodes_line.startswith("nodes: #1@113,60 #2@82,60 #3@48,61 #4@58,46 #5@49,28 #6@101,24 #7@105,42 o1@65,60")


def test_3_a_target_with_no_node_is_named_by_its_position(tmp_path):
    events = [("leg", 1.0, 0, 5, (49, 28), (7, 7)), ("planned", 1.1, (49, 28), "walk", "RIGHT", 500.0, 1.0, None)]
    model = _load(make_folder(tmp_path, events))
    assert model.step(0).target.label == "(7, 7)" and model.step(0).target.blue is False
    assert model.why[0] == "00:00.000 lap 0 leg 5: target (7, 7)"
    assert model.why[1] == "00:00.100 at (49, 28): target (7, 7) is not on this map"
    assert model.why[2] == "00:00.100 plan: walk right 0.5 s"
    assert model.step(1).panel[0] == "Target  (7, 7)"


# 4 ---------------------------------------------------------------------------------------------------------------


def _open_live(folder):
    run = read_run(folder).unwrap()
    live = RunModel.open(folder, run.header, run.origin, repo_root=REPO_ROOT, current_git_head=GIT_HEAD)
    return live, run.events


def test_4_incremental_equals_whole(tmp_path):
    folder = make_folder(tmp_path)
    whole = _load(folder)
    live, events = _open_live(folder)
    live.extend(events[0:10])
    live.extend(events[10:12])
    live.extend(events[12:26])
    assert live.why == whole.why and live.ended and live.end_reason == "max_laps"
    for k in range(len(G)):
        assert render_text(live, live.step(k)) == render_text(whole, whole.step(k)), k


def test_4_extending_with_the_third_repeat_replaces_the_line_in_place(tmp_path):
    live, events = _open_live(make_folder(tmp_path))
    live.extend(events[0:12])
    count = len(live.why)
    assert live.why[-1] == "00:05.050 no character on the minimap (miss) x2"
    live.extend(events[12:13])
    assert len(live.why) == count and live.why[-1] == "00:05.100 no character on the minimap (miss) x3"
    assert not live.ended


# 5 ---------------------------------------------------------------------------------------------------------------


def test_5_a_changed_map_warns_and_still_draws(tmp_path):
    folder = make_folder(tmp_path)
    data = (folder / "map.csv").read_bytes()
    (folder / "map.csv").write_bytes(data.replace(b"minimap_size=162,82", b"minimap_size=162,83"))
    model = _load(folder)
    assert model.warnings == (WARN_MAP,)
    assert len(model.nodes) == 21 and model.rg is not None


def test_5_a_changed_code_warns_unless_a_head_is_missing(tmp_path):
    folder = make_folder(tmp_path)
    assert _load(folder, git_head="b" * 40).warnings == (WARN_CODE,)
    assert _load(folder, git_head=None).warnings == ()
    (folder / STEPS_FILE).write_bytes(header_line(default_header(git_head=None)).unwrap() + b"".join(event_lines(G)))
    assert _load(folder, git_head="b" * 40).warnings == ()


def test_5_both_warnings_come_in_order(tmp_path):
    folder = make_folder(tmp_path)
    (folder / "map.csv").write_bytes(b"# minimap_size=1,1\n" + (folder / "map.csv").read_bytes())
    assert _load(folder, git_head="b" * 40).warnings == (WARN_MAP, WARN_CODE)


def test_5_a_deleted_map_gives_one_warning_and_no_route(tmp_path):
    folder = make_folder(tmp_path)
    (folder / "map.csv").unlink()
    model = _load(folder)
    assert len(model.warnings) == 1 and model.warnings[0].startswith("route not shown:")
    assert "map.csv" in model.warnings[0]
    assert model.nodes == () and model.rg is None and model.minimap_size is None
    for k in range(len(G)):
        assert render_text(model, model.step(k))
    assert "00:00.100 at (49, 28): route not shown" in model.why
    view = model.step(1)
    assert view.hops[0].sent and view.aim is None and view.route == ()


def test_5_an_unknown_class_and_a_bad_params_key_give_a_warning_not_a_traceback(tmp_path):
    folder = make_folder(tmp_path)
    (folder / STEPS_FILE).write_bytes(
        header_line(default_header(character="nobody")).unwrap() + b"".join(event_lines(G)))
    model = _load(folder)
    assert model.warnings == ("route not shown: unknown class nobody",) and model.nodes == ()
    assert model.minimap_size == (162, 82)
    params = {**dataclasses.asdict(MovementParams()), "bogus": 1}
    (folder / STEPS_FILE).write_bytes(header_line(default_header(params=params)).unwrap() + b"".join(event_lines(G)))
    model = _load(folder)
    assert len(model.warnings) == 1 and model.warnings[0].startswith("route not shown:")
    assert "bogus" in model.warnings[0] and "unexpected keyword argument" in model.warnings[0]
    for k in range(len(G)):
        render_text(model, model.step(k))


def test_5_junk_in_the_params_the_why_lines_read_falls_back_to_the_defaults(tmp_path):
    folder = make_folder(tmp_path)
    params = {**dataclasses.asdict(MovementParams()), "settle_timeout_s": None, "stall_s": "ten"}
    (folder / STEPS_FILE).write_bytes(header_line(default_header(params=params)).unwrap() + b"".join(event_lines(G)))
    model = _load(folder)
    assert "00:04.900 did not settle within 2 s" in model.why


def test_5_a_map_that_cannot_be_parsed_is_a_warning_too(tmp_path):
    folder = make_folder(tmp_path)
    (folder / "map.csv").write_bytes(b"not a map\n")
    model = _load(folder)
    assert model.warnings[0] == WARN_MAP
    assert model.warnings[-1].startswith("route not shown: cannot load map") and model.nodes == ()


# 6 ---------------------------------------------------------------------------------------------------------------


def test_6_a_folder_of_legs_only(tmp_path):
    model = _load(make_legs_folder(tmp_path))
    assert model.origin == "legs" and model.header.rotation is None and model.minimap_size == (162, 82)
    assert model.warnings == () and len(model.nodes) == 21 and model.ended
    events = [e.ev for e in model.events]
    assert events == ["leg", "planned", "settled", "planned", "settled", "leg_end",
                      "leg", "planned", "settled", "planned", "leg_end"]
    assert all(e.data["toward"] is None for e in model.events if e.ev == "planned")
    planned = [model.why[model.step(k).why_line] for k, ev in enumerate(events) if ev == "planned"]
    assert all(" toward " in line and "(aim x " in line for line in planned), planned
    assert not any("sent" in line for line in model.why)
    for k in (k for k, ev in enumerate(events) if ev == "settled"):  # t_ns None: the previous event's time
        assert model.why[model.step(k).why_line][:9] == model.why[model.step(k - 1).why_line][:9]
    assert model.step(0).target.label == "#1" and model.step(6).target.label == "#2"
    assert model.step(1).hops[0].sent and model.step(1).hops[0].text == "teleport right"
    assert model.step(1).panel[2].startswith("Route   you -> ")


# 7 ---------------------------------------------------------------------------------------------------------------


def test_7_read_frame_index():
    two = (b'{"i": 0, "t_ns": 5, "file": "frame_000000.png", "w": 162, "h": 82}\r\n'
           b'{"i": 1, "t_ns": 9, "file": "frame_000001.png", "w": 162, "h": 82}\r\n{"i": 2, "t_')
    assert read_frame_index(two) == ([(5, "frame_000000.png"), (9, "frame_000001.png")], 0)
    assert read_frame_index(b'not json\n{"t_ns": 5, "file": "a.png"}\n') == ([(5, "a.png")], 1)
    assert read_frame_index(b'\n[1]\n{"t_ns": "5", "file": "a.png"}\n{"t_ns": 1, "file": 2}\n') == ([], 4)
    assert read_frame_index(b"") == ([], 0) and read_frame_index(b'{"t_ns": 5, "file": "a.png"}') == ([], 0)


def test_7_frames_follow_the_step_time(model):
    model.add_frames([(ns(1.05), "f1.png"), (ns(1.5), "f2.png"), (ns(3.0), "f3.png")])
    assert model.step(0).frame is None  # 1.000 s: before the first frame (PF81 m2: the first frame is at 1.05 s)
    assert model.step(1).frame == "f1.png"  # 1.100 s
    assert model.step(4).frame == "f2.png"  # 1.950 s
    assert model.step(6).frame == "f3.png"  # 3.600 s
    assert model.frames == [(ns(1.05), "f1.png"), (ns(1.5), "f2.png"), (ns(3.0), "f3.png")]


def test_7_a_frame_at_exactly_the_step_time_counts(model):
    model.add_frames([(ns(1.1), "same.png")])  # step 1 is at 1.100 s: "the newest frame with t_ns <= the step's time"
    assert model.step(1).frame == "same.png" and model.step(0).frame is None


def test_7_a_frame_that_arrives_out_of_order_is_kept_in_time_order(model):
    model.add_frames([(ns(3.0), "f3.png"), (ns(1.05), "f1.png")])
    assert model.step(1).frame == "f1.png" and model.step(6).frame == "f3.png"
    assert [t for t, _ in model.frames] == [ns(1.05), ns(3.0)]


def test_7_load_folder_reads_the_frame_index_with_crlf(tmp_path):
    folder = make_folder(tmp_path)
    (folder / FRAMES_DIR).mkdir()
    (folder / FRAMES_DIR / FRAME_INDEX).write_bytes(
        f'{{"i": 0, "t_ns": {ns(1.05)}, "file": "frame_000000.png", "w": 162, "h": 82}}\r\n'.encode())
    model = _load(folder)
    assert model.frames == [(ns(1.05), "frame_000000.png")] and model.step(1).frame == "frame_000000.png"


def test_7_an_event_without_a_time_uses_the_previous_events_time_for_its_frame(tmp_path):
    model = _load(make_legs_folder(tmp_path))
    first = model.events[0].t_ns  # the move at +1 s, its settled (no time) after it, the next move at +4 s
    model.add_frames([(first + 1_500_000_000, "late.png")])
    assert model.step(1).frame is None and model.step(2).frame is None and model.step(3).frame == "late.png"


# 8 ---------------------------------------------------------------------------------------------------------------


def test_8_navigation(model):
    assert model.seek(0, "move", 1) == 1
    assert model.seek(1, "move", 1) == 4
    assert model.seek(4, "leg", 1) == 16
    assert model.seek(16, "leg", -1) == 0
    assert model.seek(25, "event", 1) == 25 and model.seek(0, "event", -1) == 0 and model.seek(3, "event", 1) == 4
    assert model.seek(1, "move", -1) == 1 and model.seek(20, "move", 1) == 20 and model.seek(25, "leg", 1) == 25
    assert model.seek(7, "move", -1) == 4 and model.seek(25, "leg", -1) == 16
    assert model.find_leg(0, 6) == 16 and model.find_leg(0, 5) == 0 and model.find_leg(3, 0) is None
    assert model.play_delay_ms(1, 1.0) == 231 and model.play_delay_ms(1, 4.0) == 58
    assert model.play_delay_ms(25, 1.0) == 100
    with pytest.raises(ValueError):
        model.seek(0, "lap", 1)
    with pytest.raises(IndexError):
        model.step(26)
    with pytest.raises(IndexError):
        model.step(-1)


def test_8_a_step_without_a_time_plays_for_100_ms(tmp_path):
    model = _load(make_legs_folder(tmp_path))
    assert model.play_delay_ms(1, 1.0) == 100 and model.play_delay_ms(2, 1.0) == 100


# 9 ---------------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_9_a_sim_folder(tmp_path):
    from src.route.sim.run import run_sim

    out = tmp_path / "run"
    result = run_sim(FF2, REPO_ROOT / "tests" / "fixtures" / "ff2_terrain.json", 1, 1, out_dir=out)
    assert is_successful(result), result
    model = _load(out, git_head=None)
    assert model.warnings == () and model.origin == "steps" and model.ended
    rotation = model.header.rotation
    index = None  # the position of the leg's target in the rotation
    planned = 0
    for k, event in enumerate(model.events):
        assert render_text(model, model.step(k))
        if event.ev == "leg":
            index = rotation.index(event.data["to"])
        if index is not None:
            assert model.step(k).target.label == f"#{index + 1}"
        if event.ev == "planned":
            planned += 1
            d = event.data
            logged = Move(MoveKind(d["kind"]), None if d["dir"] is None else Key[d["dir"]], d["walk_ms"])
            pv = preview(model.rg, d["pos"], _node(model, rotation[index])).unwrap()
            assert logged in pv.hops[0].options
            view = model.step(k)
            assert view.hops[0].sent and view.hops[0].from_pos == d["pos"]
    assert planned > 0


# 10 --------------------------------------------------------------------------------------------------------------


def test_10_route_changed_unit_cases(model):
    rg = model.rg
    to6, to97 = _node(model, (101, 24)), _node(model, (97, 43))
    t1 = preview(rg, (49, 28), to6).unwrap()
    at68 = preview(rg, (68, 27), to6).unwrap()
    walk_right = Move(MoveKind.WALK, Key.RIGHT, 1375.0)
    assert route_changed(t1, walk_right, (79, 24), at68) is False
    assert route_changed(t1, Move(MoveKind.TELEPORT, Key.RIGHT, None), (79, 24), at68) is True
    assert route_changed(t1, walk_right, (66, 28), at68) is True
    assert hop_route(t1.hops) == ((66, 28), (79, 24), (101, 24))

    d1 = preview(rg, (129, 60), to97).unwrap()
    assert hop_route(d1.hops) == ((97, 43), (105, 42), (97, 43))
    logged = d1.hops[1].options[0]
    assert (logged.kind, logged.direction, logged.walk_ms) == (MoveKind.WALK, Key.LEFT, 625.0)
    assert d1.hops[0].landing == (110, 60)
    assert route_changed(d1, logged, (105, 42), preview(rg, (110, 60), to97).unwrap()) is False
    assert route_changed(d1, logged, (105, 42), preview(rg, (129, 60), to97).unwrap()) is True

    hop2 = t1.hops[1].options[0]  # the walk length is not compared (RV3-D8 B)
    assert route_changed(t1, Move(MoveKind.WALK, Key.RIGHT, hop2.walk_ms - 100), (79, 24), None) is False
    assert route_changed(t1, Move(MoveKind.WALK, Key.LEFT, hop2.walk_ms), (79, 24), None) is True
    assert route_changed(t1, walk_right, None, None) is False


def test_10_a_preview_with_one_hop_always_changes(model):
    one = preview(model.rg, (80, 24), _node(model, (101, 24))).unwrap()
    assert len(one.hops) == 1
    assert route_changed(one, Move(MoveKind.WALK, Key.RIGHT, 500.0), None, None) is True


def test_10_only_the_one_changed_plan_of_g_gets_a_route_changed_line(model):
    assert [line for line in model.why if "route changed" in line] == [WHY_G[9]]


def _two_plans(tmp_path, settle, second):
    events = [("leg", 1.0, 0, 5, (49, 28), (101, 24)),
              ("planned", 1.1, (49, 28), "teleport", "RIGHT", None, 5.175, (66, 28)), settle, second]
    return _load(make_folder(tmp_path, events))


def test_10_a_settle_with_no_position_counts_and_leaves_the_bracket_out(tmp_path):
    model = _two_plans(tmp_path, ("settled", 2.0, None, False),
                       ("planned", 2.1, (68, 27), "walk", "LEFT", 800.0, 4.0, (66, 28)))
    assert model.why[4] == ("00:01.100 route changed: expected next walk right 1.4 s toward o8, "
                            "now walk left 0.8 s toward o7")


def test_10_the_bracket_says_short_and_on_the_aim(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    walk_left = ("planned", 2.1, (65, 28), "walk", "LEFT", 100.0, 4.0, (66, 28))
    short = _two_plans(tmp_path / "a", ("settled", 2.0, (65, 28), False), walk_left)
    assert short.why[4].endswith("(landed 3 px short)")
    exact = _two_plans(tmp_path / "b", ("settled", 2.0, (68, 27), False), walk_left)
    assert exact.why[4].endswith("(on the aim)")


def test_10_no_line_when_the_plan_follows_a_reached_or_a_missed_plan(tmp_path):
    events = [("leg", 1.0, 0, 5, (49, 28), (101, 24)),
              ("planned", 1.1, (49, 28), "teleport", "RIGHT", None, 5.175, (66, 28)),
              ("no_plan", 1.5, (49, 28), "boom"), ("settled", 1.6, (68, 27), False),
              ("planned", 2.0, (68, 27), "walk", "LEFT", 800.0, 4.0, (66, 28))]
    assert not any("route changed" in line for line in _load(make_folder(tmp_path, events)).why)


# 11 --------------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_11_fifty_thousand_events_load_and_scrub_fast(tmp_path):
    copies = 2000
    events = []
    for c in range(copies):
        events.extend((ev, seconds + 10 * c, *values) for ev, seconds, *values in G[:25])
    events.append(("end", 10 * copies + 1.0, None, None))
    assert len(events) == 50_001
    (tmp_path / "map.csv").write_bytes(FF2.read_bytes())
    (tmp_path / STEPS_FILE).write_bytes(b"".join([header_line(default_header()).unwrap(), *event_lines(events)]))
    started = time.perf_counter()
    model = _load(tmp_path)
    load_s = time.perf_counter() - started
    started = time.perf_counter()
    for k in range(0, 50_001, 500):
        model.step(k)
    scrub_s = time.perf_counter() - started
    print(f"load_folder of 50,001 events: {load_s:.2f} s; 101 step() calls: {scrub_s:.3f} s; "
          f"{len(model._previews)} cached previews")
    assert len(model.events) == 50_001 and model.ended
    assert len(model._previews) <= 10
    assert load_s < 3.0 and scrub_s < 0.5


def test_load_folder_fails_when_the_folder_has_no_run(tmp_path):
    result = load_folder(tmp_path, repo_root=REPO_ROOT, current_git_head=None)
    assert isinstance(result, Failure) and str(tmp_path) in result.failure()

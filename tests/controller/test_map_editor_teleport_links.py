"""The map editor's preview of the walker's teleport links (any-map plan Task 4, Hong's anymap182-1, H111).

The walker adds a teleport link from a platform end that stops a teleport to the platform the 4 px landing rule says the
teleport reaches (`RouteGraph.teleport_links`). These links are not PathGraph edges, so the editor builds the walker's route
graph itself (`MapEditor._buildPreviewRoute`) and, under "Show edges", draws each link the walker offers dashed and magenta
(ANYMAP194-D1); the orphan check (the Highlight orphans X and the Save warning) counts them as links. When the route graph
cannot be built, no link is drawn and the orphan check uses the PathGraph edges alone, silently (D1).

Task 4b (at the end, ANYMAP194-D2): a PathGraph gap edge the walker drops gets no arrowhead, a pair it drops every way is
drawn grey and dashed, and a dropped edge does not count for the orphan check.

Like `test_map_editor_nodemove.py`, these share `test_map_editor_ropes.py`'s fixture and helpers (there is no conftest.py
under tests/): a real `MapEditor` on a withdrawn Tk root, never shown, every box patched. Maps are rows
`(x, y, marked left, marked right)` as in `tests/route/test_teleport_links.py`.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from returns.pipeline import is_successful
from returns.result import Failure

import src.gui.map_editor as map_editor
import test_map_editor_ropes as ropes
from src.classes.classes import Kanna
from src.controller.controller import MapleAgent
from src.controller.map_csv import EditorNode, read_editor_nodes
from src.route.params import MovementParams
from src.route.route_graph import RouteGraph

make_editor = ropes.make_editor  # the fixture, shared

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
Row = tuple[int, int, bool, bool]
PATH_GRAPH_PRINTS = {"adding nodes to graph...", "nodes added to graph.", "creating edges between nodes...", "edges created."}


def step(dy: int) -> list[Row]:
    """A (24, 50), (40, 50) marked right; B (50, 50 + dy) marked left, (66, 50 + dy): Task 2's stepped map."""
    return [(24, 50, False, False), (40, 50, False, True), (50, 50 + dy, True, False), (66, 50 + dy, False, False)]


def lone(x: int, y: int) -> list[Row]:
    """A (24, 50), (40, 50) marked right; a single node (x, y) marked left."""
    return [(24, 50, False, False), (40, 50, False, True), (x, y, True, False)]


FAR_NODE = [(24, 50, False, False), (40, 50, False, True), (50, 53, True, False), (58, 50, False, False)]  # test_gap_cap.py


def put_nodes(editor, rows: list[Row]) -> None:
    """The map as editor nodes: the first a blue node (the rotation), the rest orange."""
    editor.state.nodes = {
        i: EditorNode(i, "blue_node" if i == 1 else "orange_node", (x, y), is_left_platform_edge=left,
                      is_right_platform_edge=right)
        for i, (x, y, left, right) in enumerate(rows, start=1)
    }
    editor.state.rotation_order = [1]


def preview(make_editor, rows: list[Row] | None = None, *, edges: bool = True, orphans: bool = False):
    """A rig whose editor has Kanna picked, the map `rows`, the two toggles set and one render done."""
    rig = make_editor()
    editor = rig.editor
    editor.controller.agent.setCharacterClass(Kanna())  # the preview needs a character class
    editor.onCharacterClassChanged()
    if rows is not None:
        put_nodes(editor, rows)
    editor._show_edges_var.set(edges)
    editor._show_orphans_var.set(orphans)
    editor._renderNodes()
    return rig


def lines(editor, tag: str) -> list[SimpleNamespace]:
    """Each canvas line with `tag`: its two ends in native minimap px, `arrow`, `fill`, `dash`, `width`, `tags`."""
    canvas = editor.minimap_canvas
    out = []
    for item in canvas.find_withtag(tag):
        x1, y1, x2, y2 = (round(c / map_editor.RENDER_SCALE) for c in canvas.coords(item))
        out.append(SimpleNamespace(
            a=(x1, y1), b=(x2, y2), arrow=canvas.itemcget(item, "arrow"), fill=canvas.itemcget(item, "fill"),
            dash=canvas.itemcget(item, "dash"), width=float(canvas.itemcget(item, "width")), tags=canvas.gettags(item),
        ))
    return out


def directed(line: SimpleNamespace) -> set[tuple[tuple[int, int], tuple[int, int]]]:
    """The directions a line's arrowheads show, as (from, to) pairs, whichever end the line starts at."""
    return {"both": {(line.a, line.b), (line.b, line.a)}, "last": {(line.a, line.b)}, "first": {(line.b, line.a)},
            "none": set()}[line.arrow]


def link_lines(editor) -> list[SimpleNamespace]:
    return lines(editor, map_editor.CANVAS_TAG_TELEPORT_LINK)


def drawn_links(editor) -> set:
    return {pair for line in link_lines(editor) for pair in directed(line)}


def orphan_xs(editor) -> set[tuple[int, int]]:
    """The centre of each red X (two diagonals through the node)."""
    return {((l.a[0] + l.b[0]) // 2, (l.a[1] + l.b[1]) // 2) for l in lines(editor, map_editor.CANVAS_TAG_ORPHAN)}


# -- Show edges draws the links --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("dy", [4, -4])
def test_show_edges_draws_the_teleport_links(make_editor, dy):
    rig = preview(make_editor, step(dy))
    editor = rig.editor
    (link,) = link_lines(editor)  # one line for the pair, arrowheads both ways
    assert {link.a, link.b} == {(40, 50), (50, 50 + dy)}
    assert link.arrow == "both" and directed(link) == {((40, 50), (50, 50 + dy)), ((50, 50 + dy), (40, 50))}
    assert (link.fill, link.dash, link.width) == ("magenta", "4 2", 1.0)
    assert map_editor.CANVAS_TAG_EDGE in link.tags  # so the preview's own delete clears it
    # the PathGraph lines are drawn as before: A's and B's own side edges
    path_lines = [l for l in lines(editor, map_editor.CANVAS_TAG_EDGE) if map_editor.CANVAS_TAG_TELEPORT_LINK not in l.tags]
    assert sorted(sorted((l.a, l.b)) for l in path_lines) == [[(24, 50), (40, 50)], [(50, 50 + dy), (66, 50 + dy)]]
    assert {(l.fill, l.arrow) for l in path_lines} == {("limegreen", "both")}

    editor._show_edges_var.set(False)
    editor._renderNodes()
    assert link_lines(editor) == [] and lines(editor, map_editor.CANVAS_TAG_EDGE) == []


def test_no_links_on_a_map_without_them(make_editor):
    rig = preview(make_editor, step(2))  # PathGraph links the 2 px step itself: the walker adds nothing
    assert link_lines(rig.editor) == [] and len(lines(rig.editor, map_editor.CANVAS_TAG_EDGE)) == 3

    nodes, rotation, _ = read_editor_nodes(str(FIXTURES / "ff2_marked.csv")).unwrap()
    editor = rig.editor
    editor.state.nodes = {n.local_id: n for n in nodes}
    editor.state.rotation_order = list(rotation)
    editor._renderNodes()
    assert link_lines(editor) == [] and len(lines(editor, map_editor.CANVAS_TAG_EDGE)) > 0


# -- the orphan check counts the links -------------------------------------------------------------------------------------

def test_a_node_reached_only_by_a_teleport_link_is_no_orphan(make_editor):
    # (50, 54) has no PathGraph edge (4 px off A's line, nothing above or below it): only the walker's teleport links
    rig = preview(make_editor, lone(50, 54), orphans=True)
    editor = rig.editor
    assert drawn_links(editor) == {((40, 50), (50, 54)), ((50, 54), (40, 50))}
    assert orphan_xs(editor) == set()
    assert editor._validateForSave() == ("ok", "")

    put_nodes(editor, lone(50, 55))  # 5 px off: no link (Hong's 4 px rule), so the node is an orphan again
    editor._renderNodes()
    assert link_lines(editor) == []
    assert orphan_xs(editor) == {(50, 55)}
    verdict, message = editor._validateForSave()
    assert verdict == "warn" and "  • #3 at (50, 55)" in message.split("\n")


# -- the editor draws what the walker uses (Review Focus 5) ----------------------------------------------------------------

@pytest.mark.parametrize("x, y", [(58, 53), (58, 54)])
def test_a_teleport_link_the_gap_cap_leaves_with_no_edge_stays_undrawn(make_editor, x, y):
    # ANYMAP198-R4 m1: a 3-4 px step across an 18 px gap. The walker's step adds the links (4 px rule) and the cap (H112)
    # then leaves them with no edge, so `_previewTeleportLinks` must not offer them: no magenta line, no link for the
    # orphan check (a drawn link the walker never uses would hide a real orphan)
    rig = preview(make_editor, lone(x, y), orphans=True)
    editor = rig.editor
    path_graph, _ = editor._buildPreviewGraph()
    route = editor._buildPreviewRoute(path_graph)
    assert {(u.minimap_position, v.minimap_position) for u, v in route.teleport_links} == {((40, 50), (x, y)), ((x, y), (40, 50))}
    assert not any(route.graph.has_edge(u, v) for u, v in route.teleport_links)  # the case is not vacuous
    assert editor._previewTeleportLinks(route) == []

    assert link_lines(editor) == []
    assert orphan_xs(editor) == {(x, y)}  # the lone node is still an orphan: the red X is drawn on it
    verdict, message = editor._validateForSave()
    assert verdict == "warn" and f"  • #3 at ({x}, {y})" in message.split("\n")


@pytest.mark.parametrize("rows", [step(3), step(-4), FAR_NODE, lone(50, 54)], ids=["step3", "step-4", "far_node", "lone"])
def test_the_editor_draws_what_the_walker_uses(make_editor, tmp_path, rows):
    rig = preview(make_editor, rows)
    editor = rig.editor
    rig.dialogs.save_path = str(tmp_path / "m.csv")
    editor._saveAs()
    assert rig.dialogs.errors == []

    agent = MapleAgent(characterClass=Kanna())
    assert is_successful(agent.loadMap(tmp_path / "m.csv")) and agent.pathGraph is not None
    rg = RouteGraph.build(agent.pathGraph, MovementParams()).unwrap()
    walker = {(u.minimap_position, v.minimap_position) for u, v in rg.teleport_links if rg.graph.has_edge(u, v)}
    assert walker != set()
    assert drawn_links(editor) == walker


# -- a route graph that cannot be built: no link, PathGraph orphans, nothing said (ANYMAP194-D1) ----------------------------

def test_a_failed_route_build_draws_no_link(make_editor, monkeypatch, capsys):
    monkeypatch.setattr(map_editor.RouteGraph, "build", staticmethod(lambda path_graph, params: Failure("x")))
    capsys.readouterr()
    rig = preview(make_editor, lone(50, 54), orphans=True)
    editor = rig.editor
    assert link_lines(editor) == []
    # the PathGraph line is drawn as it is with no route graph at all (nothing counts as dropped: m-new1, Task 4b below)
    (line,) = lines(editor, map_editor.CANVAS_TAG_EDGE)
    assert sorted((line.a, line.b)) == [(24, 50), (40, 50)] and (line.fill, line.dash, line.arrow) == ("limegreen", "", "both")
    assert orphan_xs(editor) == {(50, 54)}  # PathGraph edges alone: the lone node has none
    verdict, message = editor._validateForSave()
    assert verdict == "warn" and "  • #3 at (50, 54)" in message.split("\n")
    out, err = capsys.readouterr()
    assert set(out.splitlines()) <= PATH_GRAPH_PRINTS and err == ""  # only the PathGraph build's own lines
    assert rig.dialogs.errors == [] and rig.dialogs.infos == [] and editor._status_label.cget("text") == ""


# -- Task 4b: gaps the walker does not cross show as not crossed (ANYMAP194-D2, Ruling ANYMAP195-PR-I1) ----------------------

def gap(w: int) -> list[Row]:
    """A (24, 50), (40, 50) marked right; B (40 + w, 50) marked left, (56 + w, 50): Task 1's map."""
    return [(24, 50, False, False), (40, 50, False, True), (40 + w, 50, True, False), (56 + w, 50, False, False)]


INTERCEPT_C = [(24, 50, False, False), (40, 50, False, True), (48, 53, False, False), (56, 50, True, False),
               (72, 50, False, False)]  # ANYMAP195-ADJ "intercept C"
ASYM_C = [(24, 50, False, False), (40, 50, False, True), (45, 46, False, False), (52, 51, False, False),
          (68, 51, False, False)]  # ANYMAP195-PR "asym C"


def line_between(editor, p, q) -> SimpleNamespace:
    """The one PathGraph line (not a teleport link) between native points p and q, whichever end it starts at."""
    (line,) = [l for l in lines(editor, map_editor.CANVAS_TAG_EDGE)
               if {l.a, l.b} == {p, q} and map_editor.CANVAS_TAG_TELEPORT_LINK not in l.tags]
    return line


@pytest.mark.parametrize("w, style", [(18, ("grey50", "2 2", "none")), (16, ("limegreen", "", "both"))])
def test_a_gap_the_walker_drops_both_ways_is_grey_and_dashed(make_editor, w, style):
    rig = preview(make_editor, gap(w))
    line = line_between(rig.editor, (40, 50), (40 + w, 50))
    assert (line.fill, line.dash, line.arrow) == style
    assert (line_between(rig.editor, (24, 50), (40, 50)).fill, link_lines(rig.editor)) == ("limegreen", [])


def test_a_gap_whose_teleport_lands_on_a_nearer_platform_is_grey_and_its_links_are_drawn(make_editor):
    rig = preview(make_editor, INTERCEPT_C)  # the 16 px gap's teleport lands on C, 3 px lower and nearer
    editor = rig.editor
    line = line_between(editor, (40, 50), (56, 50))
    assert (line.fill, line.dash, line.arrow) == ("grey50", "2 2", "none")
    assert drawn_links(editor) == {((40, 50), (48, 53)), ((48, 53), (40, 50)), ((56, 50), (48, 53)), ((48, 53), (56, 50))}
    assert {(l.fill, l.dash, l.arrow) for l in link_lines(editor)} == {("magenta", "4 2", "both")}


def test_a_pair_the_walker_drops_one_way_keeps_one_arrowhead(make_editor):
    # from A's end the teleport lands on C (4 px up, 5 px on), so the walker drops (40, 50) -> (52, 51); from B's end the
    # nearest platform in reach is A, so it offers (52, 51) -> (40, 50)
    rig = preview(make_editor, ASYM_C, orphans=True)
    editor = rig.editor
    line = line_between(editor, (40, 50), (52, 51))
    assert (line.fill, line.dash) == ("limegreen", "") and directed(line) == {((52, 51), (40, 50))}
    assert drawn_links(editor) == {((40, 50), (45, 46)), ((45, 46), (40, 50))}
    assert orphan_xs(editor) == set() and editor._validateForSave() == ("ok", "")  # the offered direction connects both


@pytest.mark.parametrize("x, orphan", [(58, True), (56, False)])
def test_a_node_whose_only_edge_is_a_dropped_gap_is_an_orphan(make_editor, x, orphan):
    rig = preview(make_editor, lone(x, 50), orphans=True)
    editor = rig.editor
    verdict, message = editor._validateForSave()
    if orphan:  # its only PathGraph edge is the 18 px gap, dropped both ways
        assert verdict == "warn"
        assert message.split("\n")[:2] == ["Orphan nodes (no link the walker can use):", "  • #3 at (58, 50)"]
        assert orphan_xs(editor) == {(58, 50)}
    else:
        assert (verdict, orphan_xs(editor)) == ("ok", set())


def test_a_failed_route_build_drops_nothing(make_editor, monkeypatch):
    # plan minor m-new1: with no route graph the editor cannot tell what the walker drops, so, as ANYMAP194-D1 rules for
    # the links, it shows and counts the PathGraph edges as they are, saying nothing
    monkeypatch.setattr(map_editor.RouteGraph, "build", staticmethod(lambda path_graph, params: Failure("x")))
    rig = preview(make_editor, gap(18))
    line = line_between(rig.editor, (40, 50), (58, 50))
    assert (line.fill, line.dash, line.arrow) == ("limegreen", "", "both")

    rig = preview(make_editor, lone(58, 50), orphans=True)
    assert orphan_xs(rig.editor) == set() and rig.editor._validateForSave() == ("ok", "")
    assert rig.dialogs.errors == [] and rig.dialogs.infos == []

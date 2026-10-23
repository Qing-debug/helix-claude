"""Characterization tests: the FF2 map fixture through both CSV parsers and the agent."""
from pathlib import Path

from returns.pipeline import is_successful
from returns.result import Success

from src.classes.classes import Kanna
from src.controller.controller import BuildOutcome, MapleAgent
from src.controller.csv_parser import CSVParser
from src.controller.map_csv import read_editor_nodes

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fossil_foundry_2.csv"

ROTATION_POSITIONS = [(113, 60), (82, 60), (48, 61), (58, 46), (49, 28), (101, 24), (105, 42)]
CAN_FALL_TRUE = {(58, 46), (49, 28), (101, 24), (105, 42), (48, 45), (74, 45), (42, 28),
                 (66, 28), (79, 24), (90, 24), (109, 23), (118, 43), (97, 43)}
CAN_FALL_FALSE = {(113, 60), (82, 60), (48, 61), (65, 60), (99, 60), (129, 60), (37, 60), (105, 60)}


def _parse():
    rot, nm = [], {}
    res = CSVParser(CSV=FIXTURE).parseCSV(rot, nm)
    return res, rot, nm


def test_parse_counts_and_rotation_order():
    res, rot, nm = _parse()
    assert res == Success(None)
    assert len(nm) == 21
    assert len(rot) == 7
    assert [n.minimap_position for n in rot] == ROTATION_POSITIONS


def test_can_fall_flags_and_other_flags_false():
    _, _, nm = _parse()
    nodes = list(nm.values())
    assert len(CAN_FALL_TRUE) == 13 and len(CAN_FALL_FALSE) == 8
    assert {n.minimap_position for n in nodes if n.can_fall} == CAN_FALL_TRUE
    assert {n.minimap_position for n in nodes if not n.can_fall} == CAN_FALL_FALSE
    for n in nodes:
        assert n.fall_dist_exceeds_vertical_tp is False
        assert n.is_left_platform_edge is False
        assert n.is_right_platform_edge is False


def test_editor_reader():
    res = read_editor_nodes(str(FIXTURE))
    assert is_successful(res)
    nodes, rotation_order, metadata = res.unwrap()
    assert len(nodes) == 21
    assert rotation_order == [1, 2, 3, 4, 5, 6, 7]
    assert metadata == {"minimap_size": (162, 82)}


def test_agent_load_map_builds_graph():
    agent = MapleAgent(characterClass=Kanna())
    assert agent.loadMap(FIXTURE) == Success(BuildOutcome.BUILT)
    assert agent.pathGraph.graph.number_of_nodes() == 21

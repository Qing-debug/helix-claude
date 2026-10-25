from pathlib import Path
from src.controller.csv_parser import CSVParser
from returns.result import Failure, Success, Result
from returns.pipeline import is_successful
import networkx as nx
from src.controller.path_graph import PathGraph
from src.controller.node import Node, CharacterNode
from src.classes.classes import CharacterClass
import math
from enum import Enum


class BuildOutcome(Enum):
    """Payload of a Success result from setCharacterClass / loadMap.

    The graph requires BOTH a character class AND a CSV. Either setter
    can succeed with the graph built (BUILT) or succeed with the
    counterpart still missing (PENDING_*). A Failure result means a
    genuine error — parse, build, or bad path — not a missing counterpart."""
    BUILT = "built"
    PENDING_CHARACTER = "pending_character"
    PENDING_CSV = "pending_csv"



#only exposes methods of the controller that the GUI should be able to use - adding nodes, removing nodes, stopping controller, starting controller, etc
class Controller:

    def __init__(self, sharedInfo = None, CSV = None, characterClass = None):
        self.agent = MapleAgent(sharedInfo = sharedInfo, CSV = CSV, characterClass = characterClass)

    #will throw error if called before setupAgent() method in the agent class is called
    def getGraph(self) -> nx.MultiDiGraph:
        return self.agent.pathGraph.graph

    def getNodeMap(self) -> dict[int, Node]:
        return self.agent.node_map

    def connectCharacterNode(self, char_node: CharacterNode = None) -> None:
        self.agent.connectCharacterNode(char_node)

    def deleteNode(self, node: Node) -> None:
        self.agent.deleteNode(node)

    def setCharacterClass(self, character_class: CharacterClass) -> Result[BuildOutcome, str]:
        return self.agent.setCharacterClass(character_class)

    def loadMap(self, csv_path) -> Result[BuildOutcome, str]:
        """Forwards to MapleAgent.loadMap — see there for behavior."""
        return self.agent.loadMap(csv_path)


class MapleAgent:
    def __init__(self, sharedInfo = None, CSV = None, characterClass = None):
        self.characterClass = characterClass
        self.map_rotation_order = []
        self.sharedInfo = sharedInfo
        self.CSVParser = CSVParser(CSV = CSV)
        self.node_map = {}
        self.pathGraph = None #deffered initialisation
        self.y_visit_radius = 2
        self.x_visit_radius = 2
        self.edge_y_tolerance_level = 2
        self.edge_x_tolerance_level = 2

    def _clearMapState(self) -> None:
        """Discard the current map's parsed data (node_map, map_rotation_order)
        and the graph built from it, so a new map can be loaded. Leaves the
        character class and CSV path untouched."""
        self.map_rotation_order = []
        self.node_map = {}
        self.pathGraph = None


    def setCharacterClass(self, character_class: CharacterClass) -> Result[BuildOutcome, str]:
        self.characterClass = character_class

        # No valid map loaded yet — loadMap drops CSV_file on parse failure,
        # so CSV_file is the single source of truth for "a map is loaded."
        if not self.CSVParser.CSV_file:
            return Success(BuildOutcome.PENDING_CSV)

        res_agent_setup = self.setupAgent() #assumes map loaded
        if not is_successful(res_agent_setup):
            self.pathGraph = None
            return res_agent_setup
        return Success(BuildOutcome.BUILT)

    #Note - needs to handle the ability to reconstruct graph upon loading of a new map given the other existing conditions for creating a graph are present (character class is selected)
    def loadMap(self, csv_path) -> Result[BuildOutcome, str]:
        self._clearMapState()
        self.setCSV(csv_path)

        res_parse = self.CSVParser.parseCSV(self.map_rotation_order, self.node_map)  # validate-then-parse is atomic (validation gates the mutation, so no half-parsed state); fills the freshly-cleared containers in-place
        if not is_successful(res_parse):
            self.setCSV(None)
            return res_parse

        # Parse succeeded -> node_map is populated and survives from here.
        if self.characterClass is None:
            return Success(BuildOutcome.PENDING_CHARACTER)  # genuinely waiting for a character, not an error

        # Character present: any setupAgent failure is a real build error
        # (the no-character case is already handled above), so surface it as
        # Failure with its reason. Keep the parsed data; only drop the graph.
        res_agent_setup = self.setupAgent()
        if not is_successful(res_agent_setup):
            self.pathGraph = None
            return res_agent_setup
        return Success(BuildOutcome.BUILT)


    def setCSV(self, csv_path) -> None:
        """
        Replace the CSV the agent loads from. Caller is responsible for
        calling setupAgent() afterward to rebuild the graph.

        csv_path may be a str or a pathlib.Path. Stored as a Path so
        CSVParser._readRows's .exists() check works.
        """
        self.CSVParser.CSV_file = Path(csv_path) if csv_path is not None else None

    def setupAgent(self) -> Result[None, str]:
        # STEP 1: Initialize PathGraph with references to MapleAgent's data containers + populate self.pathGraph with a functional pathgraph object
        setup_path_graph_res = self._setupPathGraph()
        if not is_successful(setup_path_graph_res):
            return setup_path_graph_res

        # STEP 2: Build graph edges using the pathgraph object
        create_edges_res = self.pathGraph.buildGraph()
        if not is_successful(create_edges_res):
            return create_edges_res

        return Success(None)


    def connectCharacterNode(self, char_node: CharacterNode) -> None:
        self.pathGraph.connectCharacterNode(char_node)

    #will be used to delete the character node during runtime at the outermost while loop level
    def deleteNode(self, node: Node) -> None:
        self.pathGraph.deleteNode(node)

    def buildPathGraphFor(self, node_map) -> Result[PathGraph, str]:
        """Single construction site for PathGraph. Returns Failure when no
        character class is selected — PathGraph requires the character's
        max horizontal traversal distance, which we cannot synthesize.

        Used by both runtime (_setupPathGraph) and the Map Editor preview
        (MapEditor._buildPreviewGraph). Keeps PathGraph unaware of
        CharacterClass — it consumes the int directly."""
        if self.characterClass is None:
            return Failure(
                "No character selected. Pick a character in the main window "
                "before building the path graph."
            )
        return Success(PathGraph(
            node_map=node_map,
            edge_y_alignment_allowance=self.edge_y_tolerance_level,
            edge_x_alignment_allowance=self.edge_x_tolerance_level,
            max_horizontal_traversal_dist=self.characterClass.max_horizontal_traversal_dist,
            max_vertical_traversal_dist=self.characterClass.max_vertical_traversal_dist,
        ))

    def _setupPathGraph(self) -> Result[None, str]:
        res = self.buildPathGraphFor(self.node_map)
        if not is_successful(res):
            return res
        self.pathGraph = res.unwrap()
        return Success(None)

    def _computeDirectionOfTravel(self, char_node: CharacterNode, target_node: Node):
        char_x, char_y = char_node.minimap_position
        target_x, target_y = target_node.minimap_position
        x_component = target_x - char_x
        y_component = (-target_y) - (-char_y)  # account for top-left as origin. The graph uses bottom-left as origin, but the image uses top_left as origin. By doing this, we get the correct vector direction relative to the image.
        angle_vec = math.degrees(math.atan2(y_component, x_component))
        min_angle, max_angle = 45, 135
        return "vertical" if max_angle >= angle_vec >= min_angle or -min_angle >= angle_vec >= -max_angle else "horizontal"

    def _isOnSamePlaneAsTargetNode(self, char_node: CharacterNode, target_node: Node, char_movement_axis: str) -> bool:
        char_x , char_y = char_node.minimap_position
        target_x, target_y = target_node.minimap_position
        abs_x_diff, abs_y_diff = abs(char_x - target_x), abs(char_y - target_y)
        if char_movement_axis == "vertical":
            if abs_x_diff <= self.edge_x_tolerance_level:
                return True
        elif char_movement_axis == "horizontal":
            if abs_y_diff <= self.edge_y_tolerance_level:
                return True
        return False

    #trying to generalise this since some characters can tp, some can double jump, so can dash, etc. movement_ability_traversal_dist is a tuple of (x_dist, y_dist) as the ability's max traversal distance in each axis may be difference.
    def _distAfterMovementAbility(self, movement_ability_traversal_dist : (int, int) , char_node: CharacterNode, target_node: Node) -> (int, int):
        (char_x, char_y), (target_x, target_y) = char_node.minimap_position, target_node.minimap_position
        movement_ability_x_dist, movement_ability_y_dist = movement_ability_traversal_dist
        new_char_x, new_char_y = char_x + movement_ability_x_dist, char_y + movement_ability_y_dist
        dist_to_target_after_tp_x, dist_to_target_after_tp_y = abs(target_x - new_char_x), abs(target_y - new_char_y)
        return dist_to_target_after_tp_x, dist_to_target_after_tp_y
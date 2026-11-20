import networkx as nx
from returns.result import Failure, Success, Result
from returns.pipeline import is_successful
from src.controller.node import Node, CharacterNode
from collections import namedtuple
import math



class Path(list):
    def __init__(self, nodes : list[Node] = None):
        super().__init__(nodes if nodes else [])

    #improve readability of nodes
    def __str__(self) -> str:
        return '[' + ' -> '.join(map(lambda node: f"(uuid:{node.uuid}@{node.minimap_position})", self)) + ']'


AxisAngleThreshold = namedtuple("AxisAngleThreshold", ["min_angle", "max_angle"]) #creates an immutable data structure - we can think of it as a class with only field attributes, no methods.
VectorInfo = namedtuple("VectorInfo", ["dom_direction", "node_tuple"])

# One row per edge direction, in the order the edges are built (left, right, up, down). `axis` is the coordinate the
# direction runs along (0 = x, 1 = y); `sign` is which way it points in minimap pixels, where y grows downward (up is -1).
_Direction = namedtuple("_Direction", ["name", "axis", "sign"])
_DIRECTIONS = (
    _Direction("left", 0, -1),
    _Direction("right", 0, 1),
    _Direction("up", 1, -1),
    _Direction("down", 1, 1),
)

class PathGraph:
    def __init__(self, node_map, edge_y_alignment_allowance, edge_x_alignment_allowance, max_horizontal_traversal_dist, max_vertical_traversal_dist):
        self.node_map = node_map #used to build the graph
        self.graph = nx.MultiDiGraph()  # multi-edged directed graph
        self.edge_y_alignment_allowance = edge_y_alignment_allowance#allow some vertical leeway when connecting nodes horizontally
        self.edge_x_alignment_allowance = edge_x_alignment_allowance #allow some horizontal leeway when connecting nodes vertically
        self.max_horizontal_traversal_dist = max_horizontal_traversal_dist #character's max horizontal reach; horizontal edges are not created between nodes farther apart than this on the x-axis
        self.max_vertical_traversal_dist = max_vertical_traversal_dist #character's max vertical reach; vertical edges (both up and down) are not created between nodes farther apart than this on the y-axis
        self.axis_angle_threshold = AxisAngleThreshold(min_angle=45, max_angle=135) #hold values used to determine predominant axis a vector points in - x or y axis.

    # TODO: test if this function works as expected. Test various character positions and double-check that the pathfinding makes sense.
    def getPathToGoalNode(self, char : CharacterNode, goal : Node) -> Path:
        """
        Computes the shortest path from a character node to a target goal node in the graph.

        This method first uses NetworkX's `shortest_path` algorithm to find a sequence of
        nodes connecting the character to the goal node. It then converts this path into
        a simplified polyline path, where unnecessary intermediate nodes are filtered out
        to produce a cleaner route aligned with axis-dominant directions (horizontal or vertical).

        Args:
            char (CharacterNode):
                The starting node representing the character's current position in the graph.
            goal (Node):
                The destination node to reach.

        Returns:
            Path:
                - A `Path` object (list of nodes) representing the simplified polyline path
                  from the character node to the goal node.
                - If no path exists, returns an empty `Path` instance.

        Raises:
            Catches `networkx.NetworkXNoPath` internally and returns an empty path to
            signal that no valid route was found.

        Notes:
            - The initial path is based on the weighted shortest path according to edge distances.
            - The `_convertToPolylinePath` method is applied to get rid of unnecessary nodes,
              keeping only nodes where the predominant axis of movement changes.
        """
        try:
            path = Path(nx.shortest_path(self.graph, source = char, target = goal, weight = "weight"))
            print(f"path to goal node: {path} before converting to polyline path")
            poly_line_path = self._convertToPolylinePath(self, path)
            print(f"path to goal node: {poly_line_path} after converting to polyline path")
            return poly_line_path
        except nx.NetworkXNoPath:
            empty_path = Path()
            return empty_path #signifies no path

    @staticmethod
    def _convertToPolylinePath(self, path : Path) -> Path:
        """
        Converts a detailed node-by-node path into a simplified polyline path.

        This method reduces path complexity by only keeping nodes where the direction
        of movement changes. It analyzes vectors between consecutive nodes and determines
        their dominant axis direction (horizontal or vertical). When the dominant direction
        changes between consecutive vectors, the connecting node is preserved in the
        simplified path.

        Args:
            self: PathGraph instance (due to static method limitation accessing instance vars)
            path (Path): The original path containing all nodes from source to destination.
                        Must contain at least 2 nodes.

        Returns:
            Path: A simplified path containing only nodes where direction changes occur,
                  plus the final destination node.

        Note:
            - Coordinate system conversion: Converts from the image's top-left origin
              (minimap pixels) to a bottom-left frame for correct vector calculations by negating y-component of vectors
            - Always includes the final destination node

        Example:
            Original path: [A, B, C, D, E] (A→B→C horizontal, C→D→E vertical)
            Simplified path: [C, E] (C is the direction change point, E is destination)
        """

        #at minimum, there are always two points in the path.
        vectors = []

        for index in range (len(path) - 1):
            curr = path[index]
            nxt = path[index + 1]

            curr_x_px, curr_y_px = curr.minimap_position
            nxt_x_px, nxt_y_px = nxt.minimap_position

            x_component = nxt_x_px - curr_x_px
            y_component = (-nxt_y_px) - (-curr_y_px) #minimap pixels have a top-left origin (y grows downward); negate y to get the vector in a bottom-left frame, where up is positive, for atan2

            angle_vec = math.degrees(math.atan2(y_component, x_component))

            #compute vectors and the involved nodes, add to list
            vectors.append(VectorInfo(self._computeDominantAxisDirection(angle_vec), (curr, nxt)))


        new_path = Path()

        #compares every vector to the next vector in-sequence and adds the common node present in both vectors where the compared vectors have differing directions
        for index in range(len(vectors) - 1):
            curr = vectors[index]
            nxt = vectors[index + 1]
            if curr.dom_direction != nxt.dom_direction:
                new_path.append(curr.node_tuple[1])

        new_path.append(path[-1]) #add last node

        return new_path


    def connectCharacterNode(self, char_node: CharacterNode) -> None:
        if not isinstance(char_node, Node):
            print("Failed to add character Node to graph. Provided argument is not of type Node.")
            return
        self.node_map.update({char_node.uuid: char_node}) #update our own tracker that represents all existing nodes in the graph
        self.graph.add_node(char_node)
        print("character node added.")
        add_char_node_res = self._createEdges(char_node = char_node, is_for_character_node = True)
        print(f"result of edge creation for character node: {add_char_node_res}")

    def deleteNode(self, node: Node) -> None:
        if not isinstance(node, Node):
            print(f"Failed to delete node from graph. Expected type Node, received {type(node)}.")
            return
        try:
            del self.node_map[node.uuid] #delete from hashmap
            self.graph.remove_node(node) #delete from graph
        except nx.NetworkXError:
            print("Failed to remove node from graph. Node does not exist.")
        except Exception as e:
            print(f"Error occurred while removing node from graph: {e} ")

    def buildGraph(self) -> Result[None, str]:
        print("adding nodes to graph...")
        self.graph.add_nodes_from(self.node_map.values())
        print("nodes added to graph.")
        print("creating edges between nodes...")
        create_edges_res = self._createEdges()
        if not is_successful(create_edges_res):
            return create_edges_res
        print("edges created.")
        return Success(None)

    def _computeDominantAxisDirection(self, angle: int) -> str:
        min_angle, max_angle = self.axis_angle_threshold.min_angle, self.axis_angle_threshold.max_angle
        print(f"angle: {angle}")
        return "vertical" if max_angle >= angle >= min_angle or -min_angle >= angle >= -max_angle else "horizontal"

    def _createEdges(self, char_node: Node|None = None, is_for_character_node: bool = False) -> Result[None, str]:
        """
        Create edges between nodes in the graph based on their spatial relationships.

        This method creates all edges in the graph with one `_findClosestNode` call per node and direction,
        in the order of `_DIRECTIONS` (left, right, up, down). It performs several
        validation checks before proceeding with edge creation:
        1. Validates that character node parameters are consistent
        2. Ensures the node map is not empty
        3. Verifies that the graph and node map contain exactly the same nodes

        Args:
            char_node (Node|None, optional): The character node to connect to the graph.
                Only used when is_for_character_node is True.
            is_for_character_node (bool, optional): Flag indicating whether this operation
                is specifically for connecting a character node to the graph.

        Returns:
            Result[None, str]: Success(None) if edges were created successfully,
                or Failure(error_message) if any validation check fails.
        """
        if is_for_character_node and not isinstance(char_node, Node):
            return Failure(
                "is_for_character_node flag is true, expected a valid character node for creating edges on/to")

        if not is_for_character_node and isinstance(char_node, Node):
            return Failure("is_for_character_node flag must be set to true to create a character node")

        if not self.node_map:  # if empty dict
            return Failure("Node_dict is empty. Cannot create any edges.")

        # Ensure the list of nodes and the graph contain exactly the same nodes (1:1 match by object identity).
        # The set comparison ignores order but ensures each node exists in both, with no extra nodes allowed.
        if set(self.node_map.values()) != set(self.graph.nodes()):
            return Failure(f"mismatch between nodes in node_map ({len(self.node_map.values())}) and nodes in graph ({len(set(self.graph.nodes()))}). node_map should always contain all nodes in the graph.")

        source_nodes = [char_node] if is_for_character_node else self.node_map.values()
        for direction in _DIRECTIONS:
            for curr_node in source_nodes:
                closest = self._findClosestNode(curr_node, direction)
                if closest is not None:
                    axis = direction.axis
                    abs_dist = abs(curr_node.minimap_position[axis] - closest.minimap_position[axis])
                    self.graph.add_edge(curr_node, closest, weight=abs_dist)
        return Success(None)

    def _findClosestNode(self, curr_node: Node, direction: _Direction):
        """
        Find the closest node to the current node in one direction (left, right, up or down).

        This method searches through all nodes in the node map to find the closest node that:
        1. Lies on the `direction` side of the current node along the direction's axis (x for left and right, y for
           up and down; y grows downward since the origin is top-left, so up is a smaller y)
        2. Is within the reach along that axis (max_horizontal_traversal_dist on the x-axis for left and right,
           max_vertical_traversal_dist on the y-axis for up and down: the character-class reach gate)
        3. Is approximately level across the other axis (within edge_y_alignment_allowance for left and right,
           edge_x_alignment_allowance for up and down)
        4. Is not the same node as the current node (up and down)
        5. Up and down only: passes _isVerticalEdgeValid (the platform-edge rule), checked last: a candidate the
           character can't teleport to is skipped, so a farther valid node can win over a nearer invalid one
           (decision H17)

        Among equally close candidates the first one in node_map order wins (strict `>`), so node_map insertion
        order can change the edges.

        Args:
            curr_node (Node): The reference node to search from
            direction (_Direction): One row of `_DIRECTIONS`

        Returns:
            Node or None: The closest qualifying node, or None if no suitable node is found
        """
        axis = direction.axis
        vertical = axis == 1
        if vertical:
            reach, allowance = self.max_vertical_traversal_dist, self.edge_x_alignment_allowance
        else:
            reach, allowance = self.max_horizontal_traversal_dist, self.edge_y_alignment_allowance

        closest = None
        curr_along = curr_node.minimap_position[axis]
        curr_across = curr_node.minimap_position[1 - axis]

        for next_node in self.node_map.values():
            if vertical and curr_node == next_node: #ignore if same node
                continue
            next_along = next_node.minimap_position[axis]
            next_across = next_node.minimap_position[1 - axis]
            # Distance heuristic only — does NOT detect terrain gaps. Two nodes
            # within the reach get an edge even if the gap between them is
            # unwalkable air (no platform data available).
            if direction.sign * (next_along - curr_along) > 0 \
                    and abs(curr_along - next_along) <= reach \
                    and curr_across - allowance <= next_across <= curr_across + allowance \
                    and (not vertical or self._isVerticalEdgeValid(curr_node, next_node)):
                if closest is None or abs(curr_along - closest.minimap_position[axis]) > abs(curr_along - next_along):
                    closest = next_node
        return closest

    @staticmethod
    def _isVerticalEdgeValid(curr_node: Node, curr_node_closest_vertical_node: Node):
        """
        Determine if a vertical edge between two nodes is valid based on platform edge rules.

        This method checks if a vertical connection between nodes is valid by examining
        platform edge properties. The rules are:
        1. If the target node is a left platform edge, the current node must be to its right
        2. If the target node is a right platform edge, the current node must be to its left
        3. If the target node is not a platform edge, the connection is always valid

        Args:
            curr_node (Node): The source node for the potential edge
            curr_node_closest_vertical_node (Node): The target node for the potential edge

        Returns:
            bool: True if the vertical edge is valid according to platform edge rules,
                  False otherwise
        """
        is_valid_edge = False
        if curr_node_closest_vertical_node.is_left_platform_edge and curr_node.minimap_position[0] >= \
                curr_node_closest_vertical_node.minimap_position[0]:
            is_valid_edge = True
        elif curr_node_closest_vertical_node.is_right_platform_edge and curr_node.minimap_position[0] <= \
                curr_node_closest_vertical_node.minimap_position[0]:
            is_valid_edge = True
        elif not curr_node_closest_vertical_node.is_left_platform_edge and not curr_node_closest_vertical_node.is_right_platform_edge:
            is_valid_edge = True
        return is_valid_edge
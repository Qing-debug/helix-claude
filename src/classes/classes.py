from abc import ABC, abstractmethod


class CharacterClass(ABC):
    """Base class for MapleStory character classes the agent can drive.

    Concrete subclasses MUST declare `movement_type`,
    `max_horizontal_traversal_dist`, and `max_vertical_traversal_dist`
    at the class level. Forgetting any of them makes the subclass
    impossible to instantiate (TypeError at construction time).
    """

    @property
    @abstractmethod
    def movement_type(self) -> str:
        """e.g. 'double_jump', 'teleport'. The character's primary
        traversal ability. """
        ...

    @property
    @abstractmethod
    def max_horizontal_traversal_dist(self) -> int:
        """Maximum horizontal pixel distance (on the minimap) covered in
        a single traversal action. Consumed by PathGraph to gate
        horizontal edge creation — two nodes farther apart than this on
        the x-axis will not have a horizontal edge between them, even if
        no closer candidate exists."""
        ...

    @property
    @abstractmethod
    def max_vertical_traversal_dist(self) -> int:
        """Maximum vertical pixel distance (on the minimap) covered in a
        single traversal action. Consumed by PathGraph to gate vertical
        edge creation (_findClosestNodeAbove / _findClosestNodeBelow) —
        two nodes farther apart than this on the y-axis will not have a
        vertical edge between them, even if no closer candidate exists."""
        ...


class Kanna(CharacterClass):
    # Split from one (x, y) tuple so PathGraph reads each axis by name.
    movement_type = "teleport"
    max_horizontal_traversal_dist = 19
    max_vertical_traversal_dist = 23

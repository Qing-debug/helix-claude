from returns.result import Failure, Success, Result
from returns.pipeline import is_successful
from src.controller.map_format import (BLUE_NODE, HEADER, MapRow, not_found_message, problems_message, read_rows,
                                       unreadable_message)
from src.controller.node import Node
from typing import Dict

class CSVParser:
    def __init__(self, CSV = None):
        self.CSV_file = CSV
        self.CSV_fields = {field: field for field in HEADER}


    def parseCSV(self, map_rotation_order: list[Node], node_map: Dict[int, Node]) -> Result[None, str]:
        """Check the whole file first (map_format.read_rows, decision H16); build Nodes only if it is valid, so a bad
        file creates no Node and leaves both containers untouched."""
        res_rows = self._readRows()
        if not is_successful(res_rows):
            return Failure(res_rows.failure())
        for row in res_rows.unwrap():
            node = Node(row.nodetype, row.pos, row.can_fall, row.fall_dist_exceeds_vertical_tp,
                        row.is_left_platform_edge, row.is_right_platform_edge)
            node_map.update({node.uuid:node})
            if row.nodetype == BLUE_NODE:
                map_rotation_order.append(node)
            print(f"succesfully created node for line {row.line}")

        print("successfully created all nodes in memory, next step: graph creation.")
        return Success(None)

    def _readRows(self) -> Result[list[MapRow], str]:
        if self.CSV_file is None:
            return Failure("CSV file is a None object, failed to load CSV file.")

        if not self.CSV_file.exists():
            return Failure(not_found_message(self.CSV_file))

        try:
            with open(self.CSV_file) as csv_file:
                rows, problems = read_rows(csv_file)
        except (OSError, UnicodeDecodeError) as e:
            return Failure(unreadable_message(self.CSV_file, e))
        if problems:
            return Failure(problems_message(self.CSV_file, problems))
        print("Map file matches the expected format.")
        return Success(rows)

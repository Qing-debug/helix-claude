import csv
import ast
from returns.result import Failure, Success, Result
from returns.pipeline import is_successful
from src.controller.node import Node
from typing import Dict

class CSVParser:
    def __init__(self, CSV = None):
        self.CSV_file = CSV
        self.CSV_fields = {"nodetype": "nodetype", "pos_in_minimap": "pos_in_minimap" , "can_fall" : "can_fall",
                           "fall_dist_exceeds_vertical_tp": "fall_dist_exceeds_vertical_tp", "is_left_platform_edge": "is_left_platform_edge",
                           "is_right_platform_edge": "is_right_platform_edge"}
        self.valid_node_types = {"blue_node", "orange_node"}
        self.valid_boolean_values = {"TRUE", "FALSE"}


    def parseCSV(self, map_rotation_order: list[Node], node_map: Dict[int, Node]) -> Result[None, str]:
        res_of_csv_validation = self._validateCSV()
        if not is_successful(res_of_csv_validation):
            return res_of_csv_validation
        with open(self.CSV_file) as csv_file:
            row = 2 #values only start from second row
            csv_reader = csv.DictReader(
                (l for l in csv_file if not l.lstrip().startswith('#')),
                delimiter=',',
            )
            for dict_row in csv_reader:
                #converting to in-memory literals
                nodetype = dict_row[self.CSV_fields["nodetype"]]
                pos_in_minimap = ast.literal_eval(dict_row[self.CSV_fields["pos_in_minimap"]])
                can_fall = (dict_row[self.CSV_fields["can_fall"]] == "TRUE")
                fall_dist_exceeds_vertical_tp = (dict_row[self.CSV_fields["fall_dist_exceeds_vertical_tp"]] == "TRUE")
                is_left_platform_edge = (dict_row[self.CSV_fields["is_left_platform_edge"]] == "TRUE")
                is_right_platform_edge = (dict_row[self.CSV_fields["is_right_platform_edge"]] == "TRUE")

                #create node
                node = Node(nodetype, pos_in_minimap, can_fall, fall_dist_exceeds_vertical_tp, is_left_platform_edge, is_right_platform_edge)
                node_map.update({node.uuid:node})
                if nodetype == "blue_node":
                    map_rotation_order.append(node)

                print(f"succesfully created node for row {row}")
                row += 1

        print("successfully created all nodes in memory, next step: graph creation.")
        return Success(None)

    def _validateCSV(self) -> Result[None, str]:
        if self.CSV_file is None:
            return Failure("CSV file is a None object, failed to load CSV file.")

        if not self.CSV_file.exists():
            return Failure("CSV file points to an invalid path, failed to load CSV file.")

        with open(self.CSV_file, 'r') as csv_file:
            csv_reader = csv.DictReader(
                (l for l in csv_file if not l.lstrip().startswith('#')),
                delimiter=',',
            )

            if len(self.CSV_fields) != len(csv_reader.fieldnames):
                return Failure("number of expected CSV field names do not match actual number of CSV field names")

            invalid_fields = []
            for field_names in zip(self.CSV_fields, csv_reader.fieldnames):
                if field_names[0] != field_names[1]:
                    invalid_fields.append((field_names[0], field_names[1]))
            if invalid_fields:
                invalid_column_info = "\n".join([f"Expected: {expected}, Received: {received}"
                                                 for expected, received in invalid_fields])
                return Failure(f"Invalid CSV header column name detected. Here is what we received as opposed to what we expected:\n{invalid_column_info}")

            print("CSV Header field names are valid. Next step: CSV column value validation.")

            result_of_row_validation = self._validateRowValues(csv_reader)
            if not is_successful(result_of_row_validation):
                return result_of_row_validation
        return Success(None)

    def _validateRowValues(self, csv_reader: csv.DictReader) -> Result[None, str]:
        row_number = 2  # Keep track of the row number
        total_errors = []  # Collect all errors
        print("validating CSV column values...")
        for row_dict in csv_reader:
            row_errors = []  # Collect error messages for each invalid column

            # Iterate through each column in the row
            for key, value in row_dict.items():
                # Check if the node type is valid
                if key == self.CSV_fields["nodetype"]:
                    if value not in self.valid_node_types:
                        row_errors.append(f"Row {row_number}: Invalid node type '{value}' in column '{key}'. Expected 'blue_node' or 'orange_node'.")
                # Check for valid boolean values
                elif key == self.CSV_fields["pos_in_minimap"]:
                    try:
                        evaluated_string = ast.literal_eval(value)
                        if not isinstance (evaluated_string, tuple):
                            row_errors.append(f"Row {row_number}: Value '{value}' is not a tuple.")
                        elif len(evaluated_string) != 2:
                            row_errors.append(f"Row {row_number}: Value '{value}' should contain exactly 2 elements.")
                        elif not isinstance(evaluated_string[0], int) or not isinstance(evaluated_string[1], int):
                            row_errors.append(f"Row {row_number}: the tuple '{value}' should only contain ints.")
                    except (ValueError, SyntaxError, TypeError) as e:
                        row_errors.append(f"Row {row_number}: Value '{value}' failed to be evaluated as a tuple. Ensure it is a valid 2-element tuple. Here is the exception error: {e}")
                else:
                    if value not in self.valid_boolean_values:
                        row_errors.append(
                            f"Row {row_number}: Invalid value '{value}' in column '{key}'. Expected TRUE or FALSE.")

            # If there were any errors in this row, add them to total_errors
            if row_errors:
                formated_row_errors = "\n".join(row_errors)
                total_errors.append(formated_row_errors)  # Join row errors with a newline

            row_number += 1  # Increment row number after processing each row

        # If there are errors, format them and return Failure
        if total_errors:
            formatted_errors = '\n' + "\n\n".join(total_errors)  # start with newline, then separate row errors with a double newline for clarity
            return Failure(formatted_errors)

        # If no errors were found, return Success
        print("All CSV column values successfully validated.")
        return Success(None)
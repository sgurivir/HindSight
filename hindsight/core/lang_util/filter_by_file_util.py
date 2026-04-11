import json
import argparse
from typing import List


class FilterByFileUtil:
    """Utility class for filtering functions and classes by file paths."""

    @staticmethod
    def get_functions_by_files(merged_functions_path: str, file_paths: List[str]) -> List[str]:
        """
        Get list of functions implemented in the specified files.

        Args:
            merged_functions_path: Path to merged_functions.json file
            file_paths: List of relative file paths to filter by

        Returns:
            List of function names as strings
        """
        try:
            with open(merged_functions_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            functions = []
            file_paths_set = set(file_paths)

            # Handle new schema with "function_to_location" wrapper
            function_data = data['function_to_location']
            for func_name, locations in function_data.items():
                if isinstance(locations, list):
                    for location in locations:
                        if isinstance(location, dict):
                            file_name = location.get('file_name', '')
                            if file_name in file_paths_set:
                                functions.append(func_name)
                                break  # Found a match, no need to check other locations

            return functions

        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"Error reading merged_functions.json: {e}")
            return []

    @staticmethod
    def get_classes_by_files(defined_classes_path: str, file_paths: List[str]) -> List[str]:
        """
        Get list of classes implemented in the specified files.

        Args:
            defined_classes_path: Path to merged_defined_classes.json file
            file_paths: List of relative file paths to filter by

        Returns:
            List of class names as strings
        """
        try:
            with open(defined_classes_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            classes = []
            file_paths_set = set(file_paths)

            # Handle new schema with "data_type_to_location_and_checksum" wrapper
            class_entries = data['data_type_to_location_and_checksum']
            for class_name, class_entry in class_entries.items():
                if isinstance(class_entry, dict):
                    # The class name is already available as class_name from the dictionary key
                    if class_name:
                        # Check if any of the files for this class match our file paths
                        # In the new schema, file locations are in 'code' array
                        code_locations = class_entry.get('code', [])
                        if isinstance(code_locations, list):
                            # code_locations is a list of location objects
                            for location in code_locations:
                                if isinstance(location, dict):
                                    file_name = location.get('file_name', '')
                                    if file_name in file_paths_set:
                                        classes.append(class_name)
                                        break  # Found a match, no need to check other locations for this class

            return classes

        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"Error reading merged_defined_classes.json: {e}")
            return []


def main():
    """
    Test function for FilterByFileUtil.

    Usage: python FilterByFileUtil.py --classes <defined_classes_path> -f <merged_functions_path> -p <relative_file_path>
    """
    parser = argparse.ArgumentParser(description='Filter classes and functions by file path')
    parser.add_argument('--classes', '-c', required=True,
                       help='Path to merged_defined_classes.json file')
    parser.add_argument('--functions', '-f', required=True,
                       help='Path to merged_functions.json file')
    parser.add_argument('-p', '--path', required=True,
                       help='Relative file path to search for')

    args = parser.parse_args()

    # Create a list with the single file path
    file_paths = [args.path]

    print(f"Searching for classes and functions in file: {args.path}")
    print("=" * 60)

    # Get classes from the specified file
    classes = FilterByFileUtil.get_classes_by_files(args.classes, file_paths)
    print(f"Classes found ({len(classes)}):")
    if classes:
        for class_name in classes:
            print(class_name)
    else:
        print("No classes found")

    print()

    # Get functions from the specified file
    functions = FilterByFileUtil.get_functions_by_files(args.functions, file_paths)
    print(f"Functions found ({len(functions)}):")
    if functions:
        for function_name in functions:
            print(function_name)
    else:
        print("No functions found")


if __name__ == "__main__":
    main()
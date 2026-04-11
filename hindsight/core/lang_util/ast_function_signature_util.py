#!/usr/bin/env python3
"""
ASTFunctionSignatureGenerator - Utility class for generating checksums for function and data type definitions
Author: Sridhar Gurivireddy
"""

import argparse
import json
import logging
import traceback

from pathlib import Path
from typing import Dict, List, Any, Optional

from ...utils.hash_util import HashUtil

logger = logging.getLogger(__name__)


class ASTFunctionSignatureGenerator:
    """
    Utility class for generating checksums for function and data type definitions.
    Provides methods to:
      - Create checksums for entries in merged_defined_classes.json
      - Create checksums for entries in merged_functions.json
      - Use content-based hashing that survives machine reboots
    """

    @staticmethod
    def create_data_type_checksum(repo_path: Path, data_type_entry: Dict[str, Any]) -> str:
        """
        Create checksum for a data type entry from merged_defined_classes.json.

        Args:
            repo_path: Path to repository root
            data_type_entry: Dictionary with structure:
                {
                    "data_type_name": "ClassName",
                    "files": [
                        {"file_name": "path/to/file.ext", "start": 10, "end": 50},
                        ...
                    ]
                }

        Returns:
            MD5 hash of combined content as hexadecimal string, or "None" if error
        """
        try:
            data_type_name = data_type_entry.get("data_type_name", "")
            files = data_type_entry.get("files", [])

            if not files:
                logger.warning(f"No files found for data type: {data_type_name}")
                return "None"

            # Sort files by file path, then by start line for consistent ordering
            sorted_files = sorted(files, key=lambda x: (x.get("file_name", ""), x.get("start", 0), x.get("end", 0)))

            combined_content = []

            for file_entry in sorted_files:
                file_name = file_entry.get("file_name", "")
                start_line = file_entry.get("start", 0)
                end_line = file_entry.get("end", 0)

                if not file_name:
                    logger.debug(f"Skipping entry with no file_name: {file_entry}")
                    continue

                # Include start_line and end_line in checksum calculation for each block
                line_info = f"start_line: {start_line} end_line: {end_line}"

                # For invalid function blocks (start=0, end=0 or start >= end), use file name and line numbers
                if start_line <= 0 or end_line <= 0 or start_line >= end_line:
                    logger.debug(f"Using file name and line numbers for invalid function block in {file_name}: start={start_line}, end={end_line}")
                    fallback_content = f"{file_name}:{line_info}"
                    combined_content.append(fallback_content)
                    continue

                file_path = repo_path / file_name

                if not file_path.exists():
                    logger.warning(f"File not found: {file_path}, using file name and line numbers as fallback")
                    fallback_content = f"{file_name}:{line_info}"
                    combined_content.append(fallback_content)
                    continue

                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()

                    # Convert to 0-based indexing and validate bounds
                    start_idx = max(0, start_line - 1)
                    end_idx = min(len(lines) - 1, end_line - 1)

                    if start_idx <= end_idx and start_idx < len(lines):
                        # Extract the specified lines for this block
                        selected_lines = lines[start_idx:end_idx + 1]
                        content = ''.join(selected_lines)
                        # Include line information in the content for checksum - each block contributes to final checksum
                        content_with_lines = f"{line_info}\n{content}"
                        combined_content.append(content_with_lines)
                    else:
                        # Use file name and line numbers as fallback when bounds are invalid
                        logger.debug(f"Invalid line bounds for {file_name}:{start_line}-{end_line}, using fallback")
                        fallback_content = f"{file_name}:{line_info}"
                        combined_content.append(fallback_content)

                except Exception as e:
                    logger.warning(f"Cannot read content from {file_name}:{start_line}-{end_line}: {e}, using fallback")
                    fallback_content = f"{file_name}:{line_info}"
                    combined_content.append(fallback_content)

            if not combined_content:
                return "None"

            # Combine all content and compute hash
            full_content = ''.join(combined_content)
            md5_hash = HashUtil.hash_for_content_md5(full_content)

            return md5_hash

        except Exception as e:
            logger.warning(f"Cannot compute data type checksum: {e}")
            return "None"

    @staticmethod
    def create_function_checksum(repo_path: Path, function_entry: Dict[str, Any], function_name: str = None) -> str:
        """
        Create checksum for a function entry from merged_functions.json.

        Args:
            repo_path: Path to repository root
            function_entry: Dictionary with structure:
                {
                    "function_name": "functionName",
                    "locations": [
                        {"file_name": "path/to/file.ext", "start": 10, "end": 50},
                        ...
                    ]
                }
                OR (for function_to_location format):
                [
                    {"file_name": "path/to/file.ext", "start": 10, "end": 50},
                    ...
                ]
            function_name: Optional function name for dummy checksum generation

        Returns:
            MD5 hash of combined content as hexadecimal string, or dummy checksum if error
        """
        try:
            # Handle both formats: list of locations or dict with locations
            if isinstance(function_entry, list):
                locations = function_entry
            elif isinstance(function_entry, dict):
                locations = function_entry.get("locations", function_entry.get("code", []))
            else:
                logger.warning(f"Invalid function entry format: {type(function_entry)}")
                # Generate dummy checksum if function_name is provided
                if function_name:
                    return HashUtil.hash_for_dummy_checksum_md5(function_name)
                return "None"

            if not locations:
                logger.warning("No locations found for function")
                # Generate dummy checksum if function_name is provided
                if function_name:
                    return HashUtil.hash_for_dummy_checksum_md5(function_name)
                return "None"

            # Sort locations by file path, then by start line for consistent ordering
            sorted_locations = sorted(locations, key=lambda x: (x.get("file_name", ""), x.get("start", 0), x.get("end", 0)))

            combined_content = []

            for location in sorted_locations:
                file_name = location.get("file_name", "")
                start_line = location.get("start", 0)
                end_line = location.get("end", 0)

                if not file_name:
                    logger.debug(f"Skipping location with no file_name: {location}")
                    continue

                # Include start_line and end_line in checksum calculation
                line_info = f"start_line: {start_line} end_line: {end_line}"

                # For invalid function blocks (start=0, end=0 or start >= end), use file name and line numbers
                if start_line <= 0 or end_line <= 0 or start_line >= end_line:
                    logger.debug(f"Using file name and line numbers for invalid function block in {file_name}: start={start_line}, end={end_line}")
                    fallback_content = f"{file_name}:{line_info}"
                    combined_content.append(fallback_content)
                    continue

                file_path = repo_path / file_name

                if not file_path.exists():
                    logger.warning(f"File not found: {file_path}, using file name and line numbers as fallback")
                    fallback_content = f"{file_name}:{line_info}"
                    combined_content.append(fallback_content)
                    continue

                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()

                    # Convert to 0-based indexing and validate bounds
                    start_idx = max(0, start_line - 1)
                    end_idx = min(len(lines) - 1, end_line - 1)

                    if start_idx <= end_idx and start_idx < len(lines):
                        # Extract the specified lines
                        selected_lines = lines[start_idx:end_idx + 1]
                        content = ''.join(selected_lines)
                        # Include line information in the content for checksum
                        content_with_lines = f"{line_info}\n{content}"
                        combined_content.append(content_with_lines)
                    else:
                        # Use file name and line numbers as fallback when bounds are invalid
                        logger.debug(f"Invalid line bounds for {file_name}:{start_line}-{end_line}, using fallback")
                        fallback_content = f"{file_name}:{line_info}"
                        combined_content.append(fallback_content)

                except Exception as e:
                    logger.warning(f"Cannot read content from {file_name}:{start_line}-{end_line}: {e}, using fallback")
                    fallback_content = f"{file_name}:{line_info}"
                    combined_content.append(fallback_content)

            if not combined_content:
                # Generate dummy checksum if function_name is provided
                if function_name:
                    return HashUtil.hash_for_dummy_checksum_md5(function_name)
                return "None"

            # Combine all content and compute hash
            full_content = ''.join(combined_content)
            md5_hash = HashUtil.hash_for_content_md5(full_content)

            return md5_hash

        except Exception as e:
            logger.error(f"Failed to compute function checksum: {e}")
            logger.info(f"Using dummy checksum for function: {function_name}")
            
            # Generate dummy checksum if function_name is provided, otherwise return "None"
            if function_name:
                return HashUtil.hash_for_dummy_checksum_md5(function_name)
            return "None"

    @staticmethod
    def add_checksums_to_data_types(repo_path: Path, data_types_json: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add checksums to all entries in a merged_defined_classes.json structure.

        Args:
            repo_path: Path to repository root
            data_types_json: Dictionary with structure:
                {
                    "data_type_to_location": [
                        {
                            "data_type_name": "ClassName",
                            "files": [{"file_name": "...", "start": 10, "end": 50}]
                        },
                        ...
                    ]
                }

        Returns:
            Dictionary with checksums added:
                {
                    "data_type_to_location_and_checksum": {
                        "ClassName": {
                            "checksum": "abc123...",
                            "code": [{"file_name": "...", "start": 10, "end": 50}]
                        },
                        ...
                    }
                }
        """

        if "data_type_to_location" in data_types_json:
            data_type_entries = data_types_json["data_type_to_location"]
        else:
            data_type_entries = []

        result = {}

        try:
            for entry in data_type_entries:
                data_type_name = entry.get("data_type_name", "")
                files = entry.get("files", [])

                if not data_type_name:
                    continue

                # Compute checksum for this data type
                checksum = ASTFunctionSignatureGenerator.create_data_type_checksum(repo_path, entry)

                # Store in dictionary
                result[data_type_name] = {
                    "checksum": checksum,
                    "code": files
                }

            return {
                "data_type_to_location_and_checksum": result
            }

        except Exception as e:
            logger.error(f"Failed to add checksums to data types: {e}")
            return {"data_type_to_location_and_checksum": {}}

    @staticmethod
    def add_checksums_to_functions(repo_path: Path, functions_json: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add checksums to all entries in a merged_functions.json structure.

        Args:
            repo_path: Path to repository root
            functions_json: Dictionary with structure:
                {
                    "function_to_location": {
                        "functionName": [
                            {"file_name": "...", "start": 10, "end": 50}
                        ],
                        ...
                    }
                }

        Returns:
            Dictionary with checksums added:
                {
                    "function_to_location_and_checksum": {
                        "functionName": {
                            "checksum": "abc123...",
                            "code": [{"file_name": "...", "start": 10, "end": 50}]
                        },
                        ...
                    }
                }
        """

        # Handle new schema format
        function_entries = functions_json["function_to_location"]

        result = {}

        for function_name, locations in function_entries.items():
            if not function_name or not locations:
                continue

            # Compute checksum for this function (includes dummy checksum fallback)
            checksum = ASTFunctionSignatureGenerator.create_function_checksum(repo_path, locations, function_name)

            # Store in new format - direct mapping without nested structure
            result[function_name] = {
                "checksum": checksum,
                "code": locations
            }

        return result

    @staticmethod
    def process_data_types_file(repo_path: Path, input_file: Path, output_file: Path = None) -> Dict[str, Any]:
        """
        Process a merged_defined_classes.json file and add checksums.

        Args:
            repo_path: Path to repository root
            input_file: Path to input merged_defined_classes.json file
            output_file: Optional path to write output (if None, overwrites input)

        Returns:
            Dictionary with checksums added
        """
        try:
            if not input_file.exists():
                logger.warning(f"Input file not found: {input_file}")
                return {"data_type_to_location_and_checksum": {}}

            with open(input_file, 'r', encoding='utf-8') as f:
                data_types_json = json.load(f)

            result = ASTFunctionSignatureGenerator.add_checksums_to_data_types(repo_path, data_types_json)

            # Write to output file
            output_path = output_file if output_file else input_file
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, sort_keys=True)

            logger.info(f"Processed data types file: {input_file} -> {output_path}")
            return result

        except Exception as e:
            logger.error(f"Failed to process data types file {input_file}: {e}")
            return {"data_type_to_location_and_checksum": {}}

    @staticmethod
    def process_functions_file(repo_path: Path, input_file: Path, output_file: Path = None) -> Dict[str, Any]:
        """
        Process a merged_functions.json file and add checksums.

        Args:
            repo_path: Path to repository root
            input_file: Path to input merged_functions.json file
            output_file: Optional path to write output (if None, overwrites input)

        Returns:
            Dictionary with checksums added
        """
        try:
            if not input_file.exists():
                logger.warning(f"Input file not found: {input_file}")
                return {}

            with open(input_file, 'r', encoding='utf-8') as f:
                functions_json = json.load(f)

            result = ASTFunctionSignatureGenerator.add_checksums_to_functions(repo_path, functions_json)

            # Write to output file
            output_path = output_file if output_file else input_file
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, sort_keys=True)

            logger.info(f"Processed functions file: {input_file} -> {output_path}")
            return result

        except Exception as e:
            logger.error(f"Failed to process functions file {input_file}: {e}")
            return {}

    @staticmethod
    def process_functions_with_checksums_and_write(repo_path: Path, functions_data: Dict[str, Any], output_file: Path) -> bool:
        """
        Process functions data, add checksums, and write to file with proper exception handling.

        Args:
            repo_path: Path to repository root
            functions_data: Dictionary with function_to_location structure
            output_file: Path to write the output file

        Returns:
            bool: True if successful, False if fallback was used
        """
        try:
            # Add checksums to the merged functions
            merged_output_with_checksums = ASTFunctionSignatureGenerator.add_checksums_to_functions(
                repo_path, functions_data
            )
            
            # Write to output file
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(merged_output_with_checksums, f, indent=2, sort_keys=True)
            
            logger.info(f"[+] Wrote merged functions with checksums to {output_file}")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to add checksums to merged functions, writing without checksums: {e}")
            
            # Write without checksums as fallback
            try:
                output_file.parent.mkdir(parents=True, exist_ok=True)
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(functions_data, f, indent=2, sort_keys=True)
                
                logger.info(f"[+] Wrote merged functions (no checksums) to {output_file}")
                return False
                
            except Exception as write_error:
                logger.error(f"Failed to write functions file even without checksums: {write_error}")
                raise


    @staticmethod
    def _normalize_entry_to_string(entry: Any) -> str:
        """
        Convert an entry (string or dict) to a deterministic string for sorting and hashing.
        
        Args:
            entry: Either a string or a dictionary containing type/function information
            
        Returns:
            str: A deterministic string representation suitable for sorting
        """
        if isinstance(entry, str):
            return entry
        elif isinstance(entry, dict):
            # Use JSON serialization with sorted keys for deterministic output
            return json.dumps(entry, sort_keys=True)
        else:
            return str(entry)

    @staticmethod
    def _extract_name_from_entry(entry: Any, name_keys: List[str] = None) -> str:
        """
        Extract a name/identifier from an entry for checksum lookup.
        
        Args:
            entry: Either a string or a dictionary containing type/function information
            name_keys: List of keys to try when extracting name from dict (default: ["name", "type", "function"])
            
        Returns:
            str: The extracted name or a JSON representation
        """
        if name_keys is None:
            name_keys = ["name", "type", "function"]
        
        if isinstance(entry, str):
            return entry
        elif isinstance(entry, dict):
            for key in name_keys:
                if key in entry:
                    return entry[key]
            return json.dumps(entry, sort_keys=True)
        else:
            return str(entry)

    @staticmethod
    def create_call_graph_function_checksum(repo_path: Path, function_entry: Dict[str, Any],
                                           data_types_checksums: Dict[str, str] = None,
                                           functions_checksums: Dict[str, str] = None) -> str:
        """
        Create checksum for a function entry from merged_call_graph.json.

        Args:
            repo_path: Path to repository root
            function_entry: Dictionary with structure:
                {
                    "function": "TMMonotonicClock::coarseMonotonicTime()",
                    "context": {
                        "start": 120,
                        "end": 147
                    },
                    "data_types_used": ["TMMachTime", "TMMonotonicTime"],
                    "functions_invoked": ["TMMonotonicClock::machTime"]
                }
            data_types_checksums: Dictionary mapping data type names to their checksums
            functions_checksums: Dictionary mapping function names to their checksums

        Returns:
            MD5 hash combining file, context, data types, and functions checksums
        """
        try:
            # Extract basic information
            function_name = function_entry.get("function", "")
            context = function_entry.get("context", {})
            data_types_used = function_entry.get("data_types_used", [])
            functions_invoked = function_entry.get("functions_invoked", [])

            # Build checksum components
            checksum_components = []

            # Add file and context information
            file_name = context.get("file", "")

            if file_name:
                checksum_components.append(f"file:{file_name}")

            # Handle data_types_used checksums
            if data_types_used:
                # Sort data types alphabetically for consistent ordering (handles both strings and dicts)
                sorted_data_types = sorted(data_types_used, key=ASTFunctionSignatureGenerator._normalize_entry_to_string)
                data_type_checksum_parts = []
                missing_data_types = []

                if data_types_checksums:
                    for data_type in sorted_data_types:
                        # Extract the data type name/key for lookup
                        data_type_key = ASTFunctionSignatureGenerator._extract_name_from_entry(data_type, ["name", "type"])
                        
                        # Get checksum from data_types_checksums, use "None" if not found
                        data_type_checksum = data_types_checksums.get(data_type_key, None)
                        if data_type_checksum is not None:
                            data_type_checksum_parts.append(data_type_checksum)
                        else:
                            missing_data_types.append(data_type_key)
                            data_type_checksum_parts.append("None")

                    # Warn about missing data type checksums
                    if missing_data_types:
                        logger.warning(f"Missing checksums for data types in function '{function_name}': {missing_data_types}. Using default checksum 'None'.")
                else:
                    # No data_types_checksums available at all
                    if data_types_used:
                        logger.warning(f"No data type checksums available for function '{function_name}'. Using data type names directly.")
                        logger.debug(f"DEBUG: data_types_checksums is None or empty. data_types_used: {data_types_used}")
                    # Convert entries to strings for checksum calculation
                    data_type_checksum_parts = [ASTFunctionSignatureGenerator._normalize_entry_to_string(dt) for dt in sorted_data_types]

                if data_type_checksum_parts:
                    # Create combined checksum string and hash it
                    data_types_hash = HashUtil.hash_for_data_types_md5(data_type_checksum_parts)
                    checksum_components.append(f"data_types:{data_types_hash}")

            # Handle functions_invoked checksums
            if functions_invoked:
                # Sort functions alphabetically for consistent ordering (handles both strings and dicts)
                sorted_functions = sorted(functions_invoked, key=ASTFunctionSignatureGenerator._normalize_entry_to_string)
                function_checksum_parts = []
                missing_functions = []

                if functions_checksums:
                    for func in sorted_functions:
                        # Extract the function name for lookup
                        func_name = ASTFunctionSignatureGenerator._extract_name_from_entry(func, ["name", "function"])
                        
                        # Try exact match first
                        func_checksum = functions_checksums.get(func_name, None)

                        # If no exact match, try fuzzy matching
                        if func_checksum is None:
                            # Try to find functions that end with the invoked function name
                            for defined_func_name in functions_checksums:
                                if defined_func_name.endswith("." + func_name):
                                    func_checksum = functions_checksums[defined_func_name]
                                    break

                            # If still no match, try base name matching (last part after dot)
                            if func_checksum is None:
                                base_func_name = func_name.split(".")[-1] if "." in func_name else func_name
                                for defined_func_name in functions_checksums:
                                    defined_base_name = defined_func_name.split(".")[-1] if "." in defined_func_name else defined_func_name
                                    if defined_base_name == base_func_name:
                                        func_checksum = functions_checksums[defined_func_name]
                                        break

                        if func_checksum is not None:
                            function_checksum_parts.append(func_checksum)
                        else:
                            missing_functions.append(func_name)
                            function_checksum_parts.append("None")

                    # Warn about missing function checksums
                    if missing_functions:
                        logger.warning(f"Missing checksums for invoked functions in function '{function_name}': {missing_functions}. Using default checksum 'None'.")
                else:
                    # No functions_checksums available at all
                    logger.warning(f"No function checksums available for function '{function_name}'. Using function names directly.")
                    # Convert entries to strings for checksum calculation
                    function_checksum_parts = [ASTFunctionSignatureGenerator._normalize_entry_to_string(f) for f in sorted_functions]

                if function_checksum_parts:
                    # Create combined checksum string and hash it
                    functions_hash = HashUtil.hash_for_functions_md5(function_checksum_parts)
                    checksum_components.append(f"functions:{functions_hash}")

            # If no components, return a default checksum
            if not checksum_components:
                fallback_content = f"function:{function_name}"
                return HashUtil.hash_for_content_md5(fallback_content)

            # Combine all components and create final checksum
            final_checksum = HashUtil.hash_for_combined_components_md5(checksum_components)

            return final_checksum

        except Exception as e:
            logger.warning(f"Cannot compute call graph function checksum for {function_entry.get('function', 'unknown')}: {e}")
            # Return a fallback checksum that never fails
            fallback_content = f"function:{function_entry.get('function', 'unknown')}"
            return HashUtil.hash_for_content_md5(fallback_content)

    @staticmethod
    def add_checksums_to_call_graph(repo_path: Path, call_graph_json: Dict[str, Any],
                                   data_types_checksums: Dict[str, str] = None,
                                   functions_checksums: Dict[str, str] = None) -> Dict[str, Any]:
        """
        Add checksums to all function entries in a merged_call_graph.json structure.

        Args:
            repo_path: Path to repository root
            call_graph_json: Dictionary with structure:
                {
                    "call_graph": [
                        {
                            "file": "common/TMMonotonicClock.m",
                            "functions": [
                                {
                                    "function": "TMMonotonicClock::coarseMonotonicTime()",
                                    "context": {"start": 120, "end": 147},
                                    "data_types_used": ["TMMachTime", "TMMonotonicTime"],
                                    "functions_invoked": ["TMMonotonicClock::machTime"]
                                }
                            ]
                        }
                    ]
                }
            data_types_checksums: Dictionary mapping data type names to their checksums
            functions_checksums: Dictionary mapping function names to their checksums

        Returns:
            Dictionary with checksums added to each function entry
        """
        try:
            # Handle both formats: list directly or dict with "call_graph" key
            if isinstance(call_graph_json, list):
                call_graph = call_graph_json
            else:
                call_graph = call_graph_json.get("call_graph", [])
            result_call_graph = []

            for file_entry in call_graph:
                file_name = file_entry.get("file", "")
                functions = file_entry.get("functions", [])

                result_functions = []
                for function_entry in functions:
                    # Create a copy of the function entry
                    result_function = function_entry.copy()

                    # Add file information to context if not present
                    if "context" not in result_function:
                        result_function["context"] = {}
                    if "file" not in result_function["context"] and file_name:
                        result_function["context"]["file"] = file_name

                    # Generate checksum for this function
                    checksum = ASTFunctionSignatureGenerator.create_call_graph_function_checksum(
                        repo_path, result_function, data_types_checksums, functions_checksums
                    )

                    # Add checksum to the function entry
                    result_function["checksum"] = checksum
                    result_functions.append(result_function)

                # Add the file entry with updated functions
                result_file_entry = {
                    "file": file_name,
                    "functions": result_functions
                }
                result_call_graph.append(result_file_entry)

            return {
                "call_graph": result_call_graph
            }

        except Exception as e:
            logger.error(f"Failed to add checksums to call graph: {e}")
            # Return original structure on error
            return call_graph_json

    @staticmethod
    def process_call_graph_file(repo_path: Path, input_file: Path, output_file: Path = None,
                               data_types_file: Path = None, functions_file: Path = None) -> Dict[str, Any]:
        """
        Process a merged_call_graph.json file and add checksums.

        Args:
            repo_path: Path to repository root
            input_file: Path to input merged_call_graph.json file
            output_file: Optional path to write output (if None, overwrites input)
            data_types_file: Optional path to merged_defined_classes.json with checksums
            functions_file: Optional path to merged_functions.json with checksums

        Returns:
            Dictionary with checksums added
        """
        try:
            logger.info("[+] Adding checksums to merged call graph...")
            
            if not input_file.exists():
                logger.warning(f"Input file not found: {input_file}")
                return {"call_graph": []}

            with open(input_file, 'r', encoding='utf-8') as f:
                call_graph_json = json.load(f)

            # Load data types checksums if available
            data_types_checksums = {}
            if data_types_file and data_types_file.exists():
                try:
                    with open(data_types_file, 'r', encoding='utf-8') as f:
                        data_types_data = json.load(f)

                    # Extract checksums from data_type_to_location_and_checksum format
                    data_type_entries = data_types_data.get("data_type_to_location_and_checksum", {})
                    for data_type_name, entry in data_type_entries.items():
                        if isinstance(entry, dict) and "checksum" in entry:
                            data_types_checksums[data_type_name] = entry["checksum"]

                    logger.debug(f"Loaded {len(data_types_checksums)} data type checksums from {data_types_file}")
                except Exception as e:
                    logger.warning(f"Failed to load data types checksums from {data_types_file}: {e}")
            else:
                if data_types_file:
                    logger.debug(f"Data types file does not exist: {data_types_file}")
                else:
                    logger.debug("No data types file provided for call graph processing")

            # Load functions checksums if available
            functions_checksums = {}
            if functions_file and functions_file.exists():
                try:
                    with open(functions_file, 'r', encoding='utf-8') as f:
                        functions_data = json.load(f)

                    # Extract checksums from flattened format
                    for function_name, entry in functions_data.items():
                        if isinstance(entry, dict) and "checksum" in entry:
                            functions_checksums[function_name] = entry["checksum"]
                except Exception as e:
                    logger.warning(f"Failed to load functions checksums from {functions_file}: {e}")

            # Add checksums to call graph
            result = ASTFunctionSignatureGenerator.add_checksums_to_call_graph(
                repo_path, call_graph_json, data_types_checksums, functions_checksums
            )

            # Write to output file
            output_path = output_file if output_file else input_file
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, sort_keys=True)

            logger.info(f"[+] Added checksums to merged call graph: {output_path}")
            return result

        except Exception as e:
            logger.warning(f"Failed to add checksums to merged call graph: {e}")
            logger.warning("Call graph will be available without checksums")
            return {"call_graph": []}


def main():
    """Command line interface for ASTFunctionSignatureGenerator."""
    parser = argparse.ArgumentParser(
        description="Add checksums to function and data type definition files"
    )
    parser.add_argument("--repo", type=Path, required=True,
                        help="Path to project root directory")
    parser.add_argument("--functions-file", type=Path,
                        help="Path to merged_functions.json file to process")
    parser.add_argument("--data-types-file", type=Path,
                        help="Path to merged_defined_classes.json file to process")
    parser.add_argument("--call-graph-file", type=Path,
                        help="Path to merged_call_graph.json file to process")
    parser.add_argument("--output-dir", type=Path,
                        help="Directory to write output files (default: overwrite input files)")

    args = parser.parse_args()

    if not args.functions_file and not args.data_types_file and not args.call_graph_file:
        parser.error("Must specify at least one of --functions-file, --data-types-file, or --call-graph-file")

    try:
        if args.functions_file:
            output_file = None
            if args.output_dir:
                output_file = args.output_dir / args.functions_file.name

            result = ASTFunctionSignatureGenerator.process_functions_file(
                repo_path=args.repo,
                input_file=args.functions_file,
                output_file=output_file
            )
            print(f"✓ Processed functions file: {len(result)} functions")

        if args.data_types_file:
            output_file = None
            if args.output_dir:
                output_file = args.output_dir / args.data_types_file.name

            result = ASTFunctionSignatureGenerator.process_data_types_file(
                repo_path=args.repo,
                input_file=args.data_types_file,
                output_file=output_file
            )
            print(f"✓ Processed data types file: {len(result.get('data_type_to_location_and_checksum', {}))} data types")

        if args.call_graph_file:
            output_file = None
            if args.output_dir:
                output_file = args.output_dir / args.call_graph_file.name

            result = ASTFunctionSignatureGenerator.process_call_graph_file(
                repo_path=args.repo,
                input_file=args.call_graph_file,
                output_file=output_file,
                data_types_file=args.data_types_file,
                functions_file=args.functions_file
            )

            # Count total functions in call graph
            total_functions = 0
            for file_entry in result.get('call_graph', []):
                total_functions += len(file_entry.get('functions', []))

            print(f"✓ Processed call graph file: {total_functions} functions across {len(result.get('call_graph', []))} files")

    except Exception as e:
        logger.error(f"Processing failed: {e}")

        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
ASTMerger - Utility class for merging AST analysis results from multiple languages
Author: Sridhar Gurivireddy
"""

import logging
from pathlib import Path
from typing import Dict, List, Any

from .ast_function_signature_util import ASTFunctionSignatureGenerator
from ...utils.file_util import read_json_file, write_json_file

logger = logging.getLogger(__name__)


class ASTMerger:
    """
    Utility class for merging AST analysis results from multiple programming languages.
    Handles merging of symbols, call graphs, and data types from Clang, Swift, Kotlin, Java, and Go.
    """

    @staticmethod
    def merge_symbols(clang_symbols: List[Dict[str, Any]],
                      swift_symbols: List[Dict[str, Any]],
                      kotlin_symbols: List[Dict[str, Any]] = None,
                      java_symbols: List[Dict[str, Any]] = None,
                      go_symbols: List[Dict[str, Any]] = None,
                      js_ts_symbols: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Merge Clang, Swift, Kotlin, Java, Go, and JS/TS symbols into a unified list.
        
        Args:
            clang_symbols: List of symbols from Clang analysis
            swift_symbols: List of symbols from Swift analysis
            kotlin_symbols: List of symbols from Kotlin analysis (optional)
            java_symbols: List of symbols from Java analysis (optional)
            go_symbols: List of symbols from Go analysis (optional)
            js_ts_symbols: List of symbols from JS/TS analysis (optional)
            
        Returns:
            List of merged symbols sorted by name
        """        
        if clang_symbols is None:
            clang_symbols = []        
        if swift_symbols is None:
            swift_symbols = []
        if kotlin_symbols is None:
            kotlin_symbols = []
        if java_symbols is None:
            java_symbols = []
        if go_symbols is None:
            go_symbols = []
        if js_ts_symbols is None:
            js_ts_symbols = []

        logger.info(f"Merging symbols: {len(clang_symbols)} Clang + {len(swift_symbols)} Swift + {len(kotlin_symbols)} Kotlin + {len(java_symbols)} Java + {len(go_symbols)} Go + {len(js_ts_symbols)} JS/TS")

        merged = []

        # Add all symbols without language tags
        merged.extend(clang_symbols)
        merged.extend(swift_symbols)
        merged.extend(kotlin_symbols)
        merged.extend(java_symbols)
        merged.extend(go_symbols)
        merged.extend(js_ts_symbols)

        # Sort by name for consistent output
        merged.sort(key=lambda x: x["name"])

        return merged

    @staticmethod
    def merge_call_graphs(clang_graph: List[Dict[str, Any]],
                          swift_graph: List[Dict[str, Any]],
                          kotlin_graph: List[Dict[str, Any]] = None,
                          java_graph: List[Dict[str, Any]] = None,
                          go_graph: List[Dict[str, Any]] = None,
                          js_ts_graph: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Merge Clang, Swift, Kotlin, Java, Go, and JS/TS call graphs into a unified list.
        Also adds 'invoked_by' attribute to each function based on functions_invoked relationships.
        
        Args:
            clang_graph: List of call graph entries from Clang analysis
            swift_graph: List of call graph entries from Swift analysis
            kotlin_graph: List of call graph entries from Kotlin analysis (optional)
            java_graph: List of call graph entries from Java analysis (optional)
            go_graph: List of call graph entries from Go analysis (optional)
            js_ts_graph: List of call graph entries from JS/TS analysis (optional)
            
        Returns:
            List of merged call graph entries with invoked_by attributes added
        """
        if clang_graph is None:
            clang_graph = []
        if swift_graph is None:
            swift_graph = []
        if kotlin_graph is None:
            kotlin_graph = []
        if java_graph is None:
            java_graph = []
        if go_graph is None:
            go_graph = []
        if js_ts_graph is None:
            js_ts_graph = []

        logger.info(f"Merging call graphs: {len(clang_graph)} Clang + {len(swift_graph)} Swift + {len(kotlin_graph)} Kotlin + {len(java_graph)} Java + {len(go_graph)} Go + {len(js_ts_graph)} JS/TS")

        merged = []

        # Merge without language tags
        merged.extend(clang_graph)
        merged.extend(swift_graph)
        merged.extend(kotlin_graph)
        merged.extend(java_graph)
        merged.extend(go_graph)
        merged.extend(js_ts_graph)

        # Sort by function name for consistent output
        merged.sort(key=lambda x: x.get("function", ""))

        # Add invoked_by attributes by building reverse mapping
        merged_with_invoked_by = ASTMerger._add_invoked_by_attributes(merged)

        return merged_with_invoked_by

    @staticmethod
    def _add_invoked_by_attributes(merged_call_graph: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Add 'invoked_by' attributes to each function in the merged call graph.
        
        Args:
            merged_call_graph: List of file entries with functions and their functions_invoked
            
        Returns:
            List of file entries with invoked_by attributes added to each function
        """
        # Build a mapping of function_name -> list of (caller_function, caller_file, caller_line)
        invoked_by_map = {}
        
        # First pass: collect all invocation relationships
        for file_entry in merged_call_graph:
            file_path = file_entry.get("file", "")
            functions = file_entry.get("functions", [])
            
            for function_entry in functions:
                caller_function = function_entry.get("function", "")
                caller_context = function_entry.get("context", {})
                caller_start_line = caller_context.get("start")
                caller_end_line = caller_context.get("end")
                
                # Get the list of functions this function invokes
                functions_invoked = function_entry.get("functions_invoked", [])
                
                for invoked_item in functions_invoked:
                    # Handle both string format (legacy) and dict format (new)
                    if isinstance(invoked_item, dict):
                        invoked_function = invoked_item.get("function", "")
                    else:
                        invoked_function = invoked_item
                    
                    if not invoked_function:
                        continue
                    
                    if invoked_function not in invoked_by_map:
                        invoked_by_map[invoked_function] = []
                    
                    # Add only the caller function name (simplified format)
                    # File and line information can be looked up later using the function name
                    invoked_by_map[invoked_function].append(caller_function)
        
        # Second pass: add invoked_by attributes to each function
        result = []
        for file_entry in merged_call_graph:
            new_file_entry = {
                "file": file_entry.get("file", ""),
                "functions": []
            }
            
            functions = file_entry.get("functions", [])
            for function_entry in functions:
                function_name = function_entry.get("function", "")
                
                # Create new function entry with all existing attributes
                new_function_entry = dict(function_entry)
                
                # Add invoked_by attribute if this function is invoked by others
                if function_name in invoked_by_map:
                    new_function_entry["invoked_by"] = invoked_by_map[function_name]
                else:
                    new_function_entry["invoked_by"] = []
                
                new_file_entry["functions"].append(new_function_entry)
            
            result.append(new_file_entry)
        
        # Log statistics about invoked_by relationships
        total_functions = sum(len(file_entry["functions"]) for file_entry in result)
        functions_with_callers = sum(
            1 for file_entry in result
            for func in file_entry["functions"]
            if func.get("invoked_by")
        )
        
        logger.info(f"Added invoked_by attributes: {functions_with_callers}/{total_functions} functions have callers")
        
        return result

    @staticmethod
    def merge_data_types_outputs(clang_data_types_out: Path,
                                swift_data_types_out: Path,
                                kotlin_data_types_out: Path,
                                java_data_types_out: Path,
                                go_data_types_out: Path,
                                merged_data_types_out: Path,
                                repo_path: Path = None,
                                js_ts_data_types_out: Path = None) -> None:
        """
        Merge all language data types registry outputs into a unified JSON file.
        
        Args:
            clang_data_types_out: Path to Clang data types output file
            swift_data_types_out: Path to Swift data types output file
            kotlin_data_types_out: Path to Kotlin data types output file
            java_data_types_out: Path to Java data types output file
            go_data_types_out: Path to Go data types output file
            merged_data_types_out: Path to write merged data types output
            repo_path: Path to repository root (for checksum generation)
            js_ts_data_types_out: Path to JS/TS data types output file (optional)
        """
        merged_data_types = []

        # Helper function to extract data type entries from data_type_to_location_and_checksum format
        def extract_data_type_entries(data_dict, language_name):
            """Extract data type entries from data_type_to_location_and_checksum format"""
            if not data_dict or not isinstance(data_dict, dict):
                return []
            
            if 'data_type_to_location_and_checksum' in data_dict:
                checksum_entries = data_dict['data_type_to_location_and_checksum']
                # Convert from checksum format back to location format for merging
                location_entries = []
                for data_type_name, entry_data in checksum_entries.items():
                    if isinstance(entry_data, dict) and 'code' in entry_data:
                        location_entries.append({
                            'data_type_name': data_type_name,
                            'files': entry_data['code']
                        })
                return location_entries
            
            return []

        # Load Clang data types if file exists
        if clang_data_types_out and clang_data_types_out.exists():
            clang_data_types = read_json_file(str(clang_data_types_out))
            clang_entries = extract_data_type_entries(clang_data_types, "Clang")
            if clang_entries:
                merged_data_types.extend(clang_entries)
                logger.info(f"Loaded {len(clang_entries)} Clang data type entries")

        # Load Swift data types if file exists
        if swift_data_types_out and swift_data_types_out.exists():
            swift_data_types = read_json_file(str(swift_data_types_out))
            swift_entries = extract_data_type_entries(swift_data_types, "Swift")
            if swift_entries:
                merged_data_types.extend(swift_entries)
                logger.info(f"Loaded {len(swift_entries)} Swift data type entries")

        # Load Kotlin data types if file exists
        if kotlin_data_types_out and kotlin_data_types_out.exists():
            kotlin_data_types = read_json_file(str(kotlin_data_types_out))
            kotlin_entries = extract_data_type_entries(kotlin_data_types, "Kotlin")
            if kotlin_entries:
                merged_data_types.extend(kotlin_entries)
                logger.info(f"Loaded {len(kotlin_entries)} Kotlin data type entries")

        # Load Java data types if file exists
        if java_data_types_out and java_data_types_out.exists():
            java_data_types = read_json_file(str(java_data_types_out))
            java_entries = extract_data_type_entries(java_data_types, "Java")
            if java_entries:
                merged_data_types.extend(java_entries)
                logger.info(f"Loaded {len(java_entries)} Java data type entries")

        # Load Go data types if file exists
        if go_data_types_out and go_data_types_out.exists():
            go_data_types = read_json_file(str(go_data_types_out))
            go_entries = extract_data_type_entries(go_data_types, "Go")
            if go_entries:
                merged_data_types.extend(go_entries)
                logger.info(f"Loaded {len(go_entries)} Go data type entries")

        # Load JS/TS data types if file exists
        if js_ts_data_types_out and js_ts_data_types_out.exists():
            js_ts_data_types = read_json_file(str(js_ts_data_types_out))
            js_ts_entries = extract_data_type_entries(js_ts_data_types, "JS/TS")
            if js_ts_entries:
                merged_data_types.extend(js_ts_entries)
                logger.info(f"Loaded {len(js_ts_entries)} JS/TS data type entries")

        # Sort by data type name for consistent output
        merged_data_types.sort(key=lambda x: x.get("data_type_name", ""))

        # Wrap in the new dictionary schema
        final_output = {
            "data_type_to_location": merged_data_types
        }

        # Add checksums to the merged data types
        try:
            final_output_with_checksums = ASTFunctionSignatureGenerator.add_checksums_to_data_types(
                repo_path, final_output
            )

            # Write merged output with checksums
            logger.info(f"[+] Writing merged data type definitions with checksums to {merged_data_types_out}")
            write_json_file(str(merged_data_types_out), final_output_with_checksums)
            logger.info(f"Merged {len(merged_data_types)} total data type entries with checksums")
        except Exception as e:
            logger.warning(f"Failed to add checksums to merged data types, writing without checksums: {e}")
            # Write merged output without checksums
            logger.info(f"[+] Writing merged data type definitions to {merged_data_types_out}")
            write_json_file(str(merged_data_types_out), final_output)
            logger.info(f"Merged {len(merged_data_types)} total data type entries")
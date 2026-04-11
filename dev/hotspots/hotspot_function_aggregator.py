#!/usr/bin/env python3
"""
Hotspot Function Aggregator

Parses hotspot JSON and aggregates costs by function.
Creates a dictionary where:
- Key: (file_name, function_name, library_name)
- Value: {cost: <accumulated_cost>, normalized_cost: <percentage>}

Uses a two-pass algorithm:
1. First pass: Accumulate raw costs (normalized_cost = 0)
2. Second pass: Calculate normalized_cost as percentage of total

Only includes functions from libraries specified in the filter.

If --repo is provided, uses FileContentProvider to check if files exist in the repo.
If a file doesn't exist, the cost is assigned to the caller's frame in the callstack.

Default filter libraries (if not specified):
- locationd
- CoreLocation
- CoreLocationProtobuf
- CoreLocationTiles
- CoreMotion
- LocationSupport

Skip existing files:
By default (--skip-existing), the script will skip generating output files that already exist.
This allows incremental runs where only missing outputs are generated.
Use --no-skip-existing to force regeneration of all files.

Usage:
    python3 dev/hotspot_function_aggregator.py -i input.json output.json
    python3 dev/hotspot_function_aggregator.py -i input.json output.json --filter CoreLocation LocationSupport
    python3 dev/hotspot_function_aggregator.py -i input.json output.json --repo /path/to/repo
    python3 dev/hotspot_function_aggregator.py -i input.json output.json --no-skip-existing  # Force regeneration
"""

# Default libraries to filter by if user doesn't provide --filter
DEFAULT_FILTER_LIBRARIES = [
    "locationd",
    "CoreLocation",
    "CoreLocationProtobuf",
    "CoreLocationTiles",
    "CoreMotion",
    "LocationSupport"
]

import json
import sys
import re
import argparse
import random
from pathlib import Path
from collections import deque
from typing import Dict, Any, List, Set, Optional, Tuple, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

# FileContentProvider import (optional - only used if --repo is provided)
_file_content_provider_initialized = False
_file_exists_checker: Optional[Callable[[str], bool]] = None


def init_file_content_provider(repo_path: str) -> bool:
    """
    Initialize FileContentProvider with the given repo path.
    
    Returns True if successful, False otherwise.
    """
    global _file_content_provider_initialized, _file_exists_checker
    
    try:
        # Add hindsight to path if needed
        hindsight_path = Path(__file__).parent.parent / "hindsight"
        if str(hindsight_path) not in sys.path:
            sys.path.insert(0, str(hindsight_path.parent))
        
        from hindsight.utils.file_content_provider import FileContentProvider
        
        print(f"Initializing FileContentProvider with repo: {repo_path}")
        FileContentProvider.from_repo(repo_path)
        
        # Get the instance to access the index directly
        fcp_instance = FileContentProvider.get()
        
        # Create a fast checker that ONLY uses the pre-built index
        # This avoids slow repo scanning for files not in the index
        def check_file_exists_fast(filename: str) -> bool:
            if not filename:
                return False
            # Only check the index - don't scan repo
            # This is O(1) lookup instead of O(n) repo scan
            key = filename.lower()
            return key in fcp_instance.name_to_path_mapping and len(fcp_instance.name_to_path_mapping[key]) > 0
        
        _file_exists_checker = check_file_exists_fast
        _file_content_provider_initialized = True
        
        # Print index stats
        index_size = len(fcp_instance.name_to_path_mapping)
        print(f"FileContentProvider initialized successfully ({index_size} files indexed)")
        return True
        
    except Exception as e:
        print(f"Warning: Could not initialize FileContentProvider: {e}")
        print("Proceeding without file existence checking")
        _file_content_provider_initialized = False
        _file_exists_checker = None
        return False


def file_exists_in_repo(filename: str) -> bool:
    """
    Check if a file exists in the repo using FileContentProvider.
    
    If FileContentProvider is not initialized, returns True (assume file exists).
    """
    global _file_exists_checker
    
    if _file_exists_checker is None:
        return True  # No checking available, assume exists
    
    return _file_exists_checker(filename)


def iter_all_nodes_with_costs(callstack: Dict[str, Any],
                               check_file_exists: bool = False,
                               file_exists_cache: Optional[Dict[str, bool]] = None,
                               value_key: str = "value",
                               name_key: str = "frameName",
                               owner_key: str = "ownerName",
                               source_key: str = "sourcePath") -> List[Dict[str, Any]]:
    """
    Yield all nodes from the callstack tree with both self-time and inclusive costs.
    
    Self time = node's value - sum of ALL children's values
    Inclusive cost = node's value (total time including children)
    
    First pass filtering:
    - Drop nodes whose file doesn't exist in repo (if check_file_exists is True)
    - Drop nodes with no filename
    
    Args:
        callstack: The root callstack node
        check_file_exists: Whether to check if files exist in repo
        file_exists_cache: Cache for file existence checks (will be created if None)
        
    Returns:
        List of nodes with both self-time and inclusive costs (filtered)
    """
    all_nodes = []
    
    if file_exists_cache is None:
        file_exists_cache = {}
    
    def file_in_repo(filename: str) -> bool:
        """Check if file exists in repo, with caching."""
        if not filename:
            return False  # No filename = drop
        if not check_file_exists:
            return True  # No checking = assume exists
        
        extracted = extract_filename(filename)
        if extracted not in file_exists_cache:
            file_exists_cache[extracted] = file_exists_in_repo(extracted)
        return file_exists_cache[extracted]
    
    stack = deque([callstack])
    
    while stack:
        node = stack.pop()
        children = node.get("children", [])
        node_value = int(node.get(value_key) or 0)
        file_name = node.get(source_key, "")
        
        # Calculate self time (always subtract ALL children)
        if not children:
            self_time = node_value
        else:
            children_sum = sum(int(child.get(value_key) or 0) for child in children)
            self_time = node_value - children_sum
            # Add children to stack for processing
            stack.extend(children)
        
        # First pass filter: Drop if no file or file not in repo
        # Include nodes with either positive self_time OR positive inclusive cost
        if (self_time > 0 or node_value > 0) and file_in_repo(file_name):
            all_nodes.append({
                "function_name": node.get(name_key, "<unnamed>"),
                "self_cost": self_time,
                "inclusive_cost": node_value,
                "library_name": node.get(owner_key, ""),
                "file_name": file_name
            })
    
    return all_nodes




def is_valid_function(function_name: str) -> bool:
    """Check if function name is valid (not ??? or empty or <root>)."""
    return bool(function_name and 
                function_name.strip() and 
                function_name.strip() != "???" and 
                function_name.strip() != "<root>" and
                function_name.strip() != "<unnamed>")


def should_include_library(library_name: str, filter_libraries: Set[str]) -> bool:
    """Check if library should be included based on filter."""
    if not filter_libraries:
        return True
    return library_name in filter_libraries


def extract_filename(source_path: str) -> str:
    """Extract just the filename from a source path."""
    if not source_path:
        return ""
    return Path(source_path).name


def aggregate_functions_from_nested(data: Dict[str, Any],
                                     filter_libraries: Optional[Set[str]] = None,
                                     check_file_exists: bool = False) -> Tuple[Dict[str, Dict[str, Any]], int, Set[str]]:
    """
    Aggregate function costs from nested callstack format.
    
    Two-pass algorithm:
    1. First pass: Traverse tree, collect self-time and inclusive cost for each node, drop nodes with missing files
    2. Second pass: Aggregate by (file, function, library)
    
    Returns:
        Tuple of (aggregated_dict, total_cost, all_libraries_found)
    """
    callstack = data.get("callstack", {})
    
    # Get total cost from root node
    total_cost = int(callstack.get("value") or 0)
    
    # First pass: Get all nodes with both self-time and inclusive cost (drops nodes with missing files)
    file_exists_cache: Dict[str, bool] = {}
    all_nodes = iter_all_nodes_with_costs(
        callstack,
        check_file_exists=check_file_exists,
        file_exists_cache=file_exists_cache
    )
    
    # Accumulate costs and collect all libraries
    aggregated: Dict[str, Dict[str, Any]] = {}
    all_libraries: Set[str] = set()
    
    for node in all_nodes:
        function_name = node["function_name"]
        library_name = node["library_name"]
        file_name = extract_filename(node["file_name"])
        self_cost = node["self_cost"]
        inclusive_cost = node["inclusive_cost"]
        
        # Collect all libraries (before filtering)
        if library_name:
            all_libraries.add(library_name)
        
        # Skip invalid functions
        if not is_valid_function(function_name):
            continue
        
        # Apply library filter
        if filter_libraries and not should_include_library(library_name, filter_libraries):
            continue
        
        # Create composite key
        key = f"{file_name}|{function_name}|{library_name}"
        
        if key in aggregated:
            # Add to existing costs (both are additive across occurrences)
            aggregated[key]["self_cost"] += self_cost
            aggregated[key]["inclusive_cost"] += inclusive_cost
        else:
            # Initialize new entry
            aggregated[key] = {
                "file_name": file_name,
                "function_name": function_name,
                "library_name": library_name,
                "self_cost": self_cost,
                "inclusive_cost": inclusive_cost,
                "normalized_self_cost": 0.0,  # Will be calculated in second pass
                "normalized_inclusive_cost": 0.0  # Will be calculated in second pass
            }
    
    return aggregated, total_cost, all_libraries




def aggregate_functions_from_array(data: List,
                                    filter_libraries: Optional[Set[str]] = None) -> Tuple[Dict[str, Dict[str, Any]], int, Set[str]]:
    """
    Aggregate function costs from array format.
    
    In array format, each entry already has cost, so we just aggregate.
    Total cost is the sum of all leaf costs.
    
    Returns:
        Tuple of (aggregated_dict, total_cost, all_libraries_found)
    """
    aggregated: Dict[str, Dict[str, Any]] = {}
    all_libraries: Set[str] = set()
    total_cost = 0
    
    for callstack_group in data:
        if not isinstance(callstack_group, list):
            continue
        
        # In array format, the last entry in each group is typically the leaf
        # But we should process all entries and track unique functions
        for entry in callstack_group:
            if not isinstance(entry, dict):
                continue
            
            function_name = entry.get('path', '')
            library_name = entry.get('ownerName', '')
            file_name = extract_filename(entry.get('sourcePath', ''))
            cost = int(entry.get('cost', 0))
            
            # Collect all libraries (before filtering)
            if library_name:
                all_libraries.add(library_name)
            
            # Skip invalid functions
            if not is_valid_function(function_name):
                continue
            
            # Apply library filter
            if filter_libraries and not should_include_library(library_name, filter_libraries):
                continue
            
            # Create composite key
            key = f"{file_name}|{function_name}|{library_name}"
            
            if key in aggregated:
                # For array format, we need to be careful about double counting
                # Each callstack group represents a unique path, so we add costs
                aggregated[key]["cost"] += cost
            else:
                aggregated[key] = {
                    "file_name": file_name,
                    "function_name": function_name,
                    "library_name": library_name,
                    "cost": cost,
                    "normalized_cost": 0.0
                }
            
            total_cost += cost
    
    return aggregated, total_cost, all_libraries


def calculate_normalized_costs(aggregated: Dict[str, Dict[str, Any]],
                                total_cost: int) -> Dict[str, Dict[str, Any]]:
    """
    Second pass: Calculate normalized costs as percentages for both self_cost and inclusive_cost.
    """
    if total_cost <= 0:
        return aggregated
    
    for key in aggregated:
        self_cost = aggregated[key].get("self_cost", aggregated[key].get("cost", 0))
        inclusive_cost = aggregated[key].get("inclusive_cost", self_cost)
        aggregated[key]["normalized_self_cost"] = round((self_cost / total_cost) * 100.0, 4)
        aggregated[key]["normalized_inclusive_cost"] = round((inclusive_cost / total_cost) * 100.0, 4)
        # Keep backward compatibility
        aggregated[key]["cost"] = self_cost
        aggregated[key]["normalized_cost"] = aggregated[key]["normalized_self_cost"]
    
    return aggregated


def convert_to_output_format(aggregated: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Convert internal format to output format.
    
    Output format:
    {
        "(file_name, function_name, library_name)": {
            "self_cost": <int>,
            "inclusive_cost": <int>,
            "normalized_self_cost": <float>,
            "normalized_inclusive_cost": <float>
        }
    }
    """
    output = {}
    
    for key, value in aggregated.items():
        # Create readable key
        output_key = f"({value['file_name']}, {value['function_name']}, {value['library_name']})"
        self_cost = value.get("self_cost", value.get("cost", 0))
        inclusive_cost = value.get("inclusive_cost", self_cost)
        output[output_key] = {
            "self_cost": self_cost,
            "inclusive_cost": inclusive_cost,
            "normalized_self_cost": value.get("normalized_self_cost", value.get("normalized_cost", 0.0)),
            "normalized_inclusive_cost": value.get("normalized_inclusive_cost", value.get("normalized_cost", 0.0))
        }
    
    return output


def aggregate_hotspot_functions(input_file: str,
                                 output_file: str,
                                 filter_libraries: Optional[List[str]] = None,
                                 user_provided_filter: bool = False,
                                 repo_path: Optional[str] = None,
                                 skip_existing: bool = True) -> bool:
    """
    Main function to aggregate hotspot functions.
    
    Algorithm:
    1. First pass: Traverse tree, collect self-time, drop nodes with missing/unknown files
    2. Second pass: Aggregate by (file, function, library)
    3. Calculate normalized costs and filter out entries < 0.0001%
    
    Args:
        input_file: Path to input JSON file
        output_file: Path to output JSON file
        filter_libraries: Optional list of library names to filter by
        user_provided_filter: Whether the user explicitly provided a filter
        repo_path: Optional path to repo for file existence checking
        skip_existing: If True, skip generating output file if it already exists
    """
    # Check if output file already exists and skip_existing is enabled
    if skip_existing and Path(output_file).exists():
        print(f"SKIPPING: Output file already exists: {output_file}")
        # Load and return the existing data
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
            print(f"  Loaded {len(existing_data)} functions from existing file")
            return True, existing_data
        except Exception as e:
            print(f"  Warning: Could not load existing file: {e}")
            print(f"  Proceeding with regeneration...")
    
    print(f"Processing {input_file}")
    
    # Initialize FileContentProvider if repo path provided
    if repo_path:
        if init_file_content_provider(repo_path):
            pass  # Success message already printed by init_file_content_provider
        else:
            print("File existence checking DISABLED - proceeding without it")
    
    # Load JSON data
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON file: {e}")
        return False
    
    # Use default filter if not provided
    if filter_libraries is None:
        filter_libraries = DEFAULT_FILTER_LIBRARIES
        print("\n" + "!" * 60)
        print("WARNING: No --filter provided. Using default filter libraries:")
        for lib in DEFAULT_FILTER_LIBRARIES:
            print(f"  - {lib}")
        print("Use --filter to specify custom libraries.")
        print("!" * 60 + "\n")
    
    filter_set = set(filter_libraries)
    print(f"Filtering by libraries: {filter_libraries}")
    
    # First pass: Aggregate costs (file existence checking is done during traversal if --repo provided)
    print("\nFirst pass: Aggregating costs...")
    if _file_content_provider_initialized:
        print("  (File existence checking enabled - costs from missing files attributed to callers)")
    
    if isinstance(data, dict) and 'callstack' in data:
        process_name = data.get('processName', 'unknown')
        print(f"Detected nested callstack format for process: {process_name}")
        aggregated, total_cost, all_libraries = aggregate_functions_from_nested(
            data, filter_set, check_file_exists=_file_content_provider_initialized
        )
    elif isinstance(data, list):
        print(f"Detected array format with {len(data)} callstack groups")
        aggregated, total_cost, all_libraries = aggregate_functions_from_array(data, filter_set)
    else:
        print(f"Error: Unsupported JSON format. Expected dict with 'callstack' key or array.")
        return False
    
    print(f"  Found {len(aggregated)} unique functions")
    
    print(f"\nTotal cost from JSON: {total_cost}")
    print(f"Unique functions after processing: {len(aggregated)}")
    
    # Second pass: Calculate normalized costs
    print("\nSecond pass: Calculating normalized costs...")
    aggregated = calculate_normalized_costs(aggregated, total_cost)
    
    # Filter out entries with normalized_self_cost < 0.0001% (but keep if inclusive_cost is significant)
    filtered_aggregated = {k: v for k, v in aggregated.items()
                          if v.get("normalized_self_cost", v.get("normalized_cost", 0)) >= 0.0001
                          or v.get("normalized_inclusive_cost", 0) >= 0.01}
    filtered_count = len(aggregated) - len(filtered_aggregated)
    print(f"  Filtered out {filtered_count} entries with normalized_cost < 0.0001%")
    
    # Convert to output format
    output = convert_to_output_format(filtered_aggregated)
    
    # Sort by inclusive_cost (descending) for better readability
    sorted_output = dict(sorted(output.items(),
                                key=lambda x: x[1]["inclusive_cost"],
                                reverse=True))
    
    # Write to output file
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(sorted_output, f, indent=2)
        print(f"Successfully wrote aggregated data to {output_file}")
    except Exception as e:
        print(f"Error writing output file: {e}")
        return False
    
    # Print all libraries found in the input JSON (4 per line, indented 30 spaces)
    print("\n" + "=" * 60)
    print("LIBRARIES FOUND IN INPUT JSON")
    print("=" * 60)
    sorted_libs = sorted(all_libraries)
    indent = " " * 30
    for i in range(0, len(sorted_libs), 4):
        libs_chunk = sorted_libs[i:i+4]
        print(indent + "  ".join(libs_chunk))
    print(f"\nTotal libraries found: {len(all_libraries)}")
    
    # Print the dictionary (already filtered)
    print("\n" + "=" * 60)
    print("AGGREGATED FUNCTION COSTS (>= 0.0001%)")
    print("=" * 60)
    
    for key, value in sorted_output.items():
        print(f"{key}")
        print(f"  self_cost: {value['self_cost']}, inclusive_cost: {value['inclusive_cost']}")
        print(f"  normalized_self_cost: {value['normalized_self_cost']:.4f}%, normalized_inclusive_cost: {value['normalized_inclusive_cost']:.4f}%")
    
    print("=" * 60)
    print(f"Total unique functions: {len(sorted_output)}")
    
    return True, sorted_output


# Common types to ignore when extracting keywords from function names
IGNORED_KEYWORDS = {
    # Common C/C++ types
    "double", "float", "int", "long", "short", "char", "void", "bool",
    "unsigned", "signed", "size_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int8_t", "int16_t", "int32_t", "int64_t", "CFTimeInterval",
    # Qualifiers
    "const", "volatile", "mutable", "static", "inline", "virtual", "explicit",
    # Reference/pointer markers
    "const&", "bool&", "int&", "double&", "float&",
    # Common STL types
    "std", "string", "vector", "map", "set", "unordered_map", "unordered_set",
    "optional", "pair", "tuple", "shared_ptr", "unique_ptr", "weak_ptr",
    # Common Objective-C/Swift types
    "id", "NSString", "NSArray", "NSDictionary", "NSSet", "NSNumber",
    "CFStringRef", "CFArrayRef", "CFDictionaryRef",
    # Other common tokens
    "T", "Self", "self", "this", "nullptr", "NULL", "true", "false",
}


def extract_keywords_from_function_name(function_name: str, filter_libraries: Set[str]) -> List[str]:
    """
    Extract meaningful keywords from a function name, including parameter types.
    
    Handles both C++/C style and Objective-C style function names:
    
    C++ example:
    cllcf::CLLCFusion::insertLocationIntoInputBuffer(ProviderLocationsContainer,std::shared_ptr< const LCFusionProviderLocation >) const
    Returns: ["cllcf", "CLLCFusion", "insertLocationIntoInputBuffer", "ProviderLocationsContainer", "LCFusionProviderLocation"]
    
    Objective-C examples:
    [CLContextManagerAbsoluteAltimeter shouldEnableWifiAtTime:]
    -[CLContextManagerAbsoluteAltimeter shouldEnableWifiAtTime:]
    +[CLContextManagerAbsoluteAltimeter classMethod]
    Returns: ["CLContextManagerAbsoluteAltimeter", "shouldEnableWifiAtTime"]
    
    Args:
        function_name: The full function name/signature
        filter_libraries: Set of library names to exclude from keywords
        
    Returns:
        List of extracted keywords
    """
    keywords = []
    
    # Create lowercase set of filter libraries for case-insensitive comparison
    filter_libs_lower = {lib.lower() for lib in filter_libraries}
    
    # Create lowercase set of ignored keywords for comparison
    ignored_lower = {k.lower() for k in IGNORED_KEYWORDS}
    
    def add_keyword(part: str) -> None:
        """Add a keyword if it's valid and not already in the list."""
        if not part:
            return
        part = part.strip()
        if not part:
            return
        # Skip if it's in the ignored list
        if part.lower() in ignored_lower:
            return
        # Skip if it's a filter library name
        if part.lower() in filter_libs_lower:
            return
        # Skip if it looks like a destructor (~ClassName)
        if part.startswith('~'):
            part = part[1:]
        if part and part not in keywords:
            keywords.append(part)
    
    # Check if this is an Objective-C method (starts with -, +, or [ )
    stripped_name = function_name.strip()
    is_objc_method = (stripped_name.startswith('-[') or
                      stripped_name.startswith('+[') or
                      stripped_name.startswith('['))
    
    if is_objc_method:
        # Parse Objective-C method syntax: -[ClassName methodName:param1:param2:]
        # or [ClassName methodName:param1:param2:]
        
        # Remove leading -/+ and brackets
        objc_content = stripped_name.lstrip('-+').strip()
        if objc_content.startswith('['):
            objc_content = objc_content[1:]
        if objc_content.endswith(']'):
            objc_content = objc_content[:-1]
        
        # Split into class name and method selector
        # Format: "ClassName methodPart1:methodPart2:..."
        parts = objc_content.split(None, 1)  # Split on first whitespace
        if parts:
            # First part is the class name
            class_name = parts[0].strip()
            add_keyword(class_name)
            
            if len(parts) > 1:
                # Second part is the method selector (may contain colons)
                method_selector = parts[1].strip()
                # Split by colons to get method name parts
                # e.g., "shouldEnableWifiAtTime:" -> ["shouldEnableWifiAtTime", ""]
                # e.g., "initWithUniverse:delegate:withBuffer:" -> ["initWithUniverse", "delegate", "withBuffer", ""]
                selector_parts = method_selector.split(':')
                for selector_part in selector_parts:
                    selector_part = selector_part.strip()
                    if selector_part:
                        add_keyword(selector_part)
    else:
        # C/C++ style function name
        # Split function name into qualified name and parameters
        paren_idx = function_name.find('(')
        if paren_idx != -1:
            qualified_name = function_name[:paren_idx]
            # Extract parameters part (between parentheses)
            close_paren_idx = function_name.rfind(')')
            if close_paren_idx > paren_idx:
                params_str = function_name[paren_idx + 1:close_paren_idx]
            else:
                params_str = ""
        else:
            qualified_name = function_name
            params_str = ""
        
        # Process qualified name (namespace::class::method)
        parts = qualified_name.split('::')
        for part in parts:
            # Clean up the part - remove template arguments <...>
            part = re.sub(r'<[^>]*>', '', part)
            add_keyword(part)
        
        # Process parameters
        if params_str:
            # Split by comma to get individual parameters
            params = params_str.split(',')
            for param in params:
                # Extract type names from parameter
                # Remove template arguments first to get the base types
                # But also extract types from within templates
                
                # First, extract types from within angle brackets (template parameters)
                template_matches = re.findall(r'<\s*(?:const\s+)?([A-Za-z_][A-Za-z0-9_]*)', param)
                for match in template_matches:
                    add_keyword(match)
                
                # Remove template arguments for base type extraction
                param_clean = re.sub(r'<[^>]*>', '', param)
                
                # Split by common separators and extract type names
                # Handle things like "std::shared_ptr", "const Type&", "Type*", etc.
                type_parts = re.split(r'[\s\*\&]+', param_clean)
                for type_part in type_parts:
                    # Further split by ::
                    sub_parts = type_part.split('::')
                    for sub_part in sub_parts:
                        add_keyword(sub_part)
    
    # Clean up keywords - replace <, >, &, [, ] with space and filter out invalid tokens
    cleaned_keywords = []
    for kw in keywords:
        # Replace special characters with space
        cleaned = kw.replace('<', ' ').replace('>', ' ').replace('&', ' ').replace('[', ' ').replace(']', ' ')
        # Split by space and add valid tokens
        for token in cleaned.split():
            token = token.strip()
            if token and token not in cleaned_keywords:
                # Re-check against ignored keywords and filter libraries
                if token.lower() not in ignored_lower and token.lower() not in filter_libs_lower:
                    cleaned_keywords.append(token)
    
    return cleaned_keywords


def build_function_implementation_dict(
    merged_functions_path: str,
    aggregated_output: Dict[str, Dict[str, Any]],
    filter_libraries: Set[str]
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]]]:
    """
    Build a dictionary mapping function names to their implementations and keywords.
    Also builds a file-to-functions index for efficient lookup.
    
    Schema:
    {
        "function_name": {
            "implementation": [
                {"file_name": <>, "start": <>, "end": <>},
                ...
            ],
            "keywords": [...]
        }
    }
    
    Args:
        merged_functions_path: Path to merged_functions.json
        aggregated_output: The aggregated hotspot output dictionary
        filter_libraries: Set of library names for keyword extraction
        
    Returns:
        Tuple of (function_impl_dict, file_to_functions_index)
    """
    # Load merged functions
    try:
        with open(merged_functions_path, 'r', encoding='utf-8') as f:
            merged_functions = json.load(f)
    except Exception as e:
        print(f"Error loading merged functions file: {e}")
        return {}, {}
    
    result = {}
    file_to_functions: Dict[str, List[str]] = {}  # Maps filename -> list of function names
    
    # Process ALL functions in merged_functions (not just hotspot matches)
    for func_name, func_data in merged_functions.items():
        # Build implementation list
        implementations = []
        if "code" in func_data:
            for code_entry in func_data["code"]:
                impl = {
                    "file_name": code_entry.get("file_name", ""),
                    "start": code_entry.get("start", 0),
                    "end": code_entry.get("end", 0)
                }
                implementations.append(impl)
                
                # Build file-to-functions index
                file_path = code_entry.get("file_name", "")
                if file_path:
                    # Use just the filename for the index
                    file_basename = Path(file_path).name
                    if file_basename not in file_to_functions:
                        file_to_functions[file_basename] = []
                    if func_name not in file_to_functions[file_basename]:
                        file_to_functions[file_basename].append(func_name)
        
        # Extract keywords
        keywords = extract_keywords_from_function_name(func_name, filter_libraries)
        
        result[func_name] = {
            "implementation": implementations,
            "keywords": keywords
        }
    
    return result, file_to_functions


def build_keyword_to_functions_index(func_impl_dict: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Build a reverse index mapping keywords to list of function names.
    
    Args:
        func_impl_dict: Dictionary mapping function names to their implementations and keywords
        
    Returns:
        Dictionary mapping each keyword to a list of function names that contain it
    """
    keyword_index: Dict[str, List[str]] = {}
    
    for func_name, data in func_impl_dict.items():
        keywords = data.get("keywords", [])
        for keyword in keywords:
            if keyword not in keyword_index:
                keyword_index[keyword] = []
            keyword_index[keyword].append(func_name)
    
    return keyword_index


def print_function_keywords_dict(func_impl_dict: Dict[str, Dict[str, Any]], keyword_index: Dict[str, List[str]]) -> None:
    """
    Print summary of the function implementation dictionary and keyword index.
    """
    print(f"\nFunction implementation dictionary built with {len(func_impl_dict)} functions")
    print(f"Keyword to function dictionary built with {len(keyword_index)} keys")


def find_best_matching_function(
    hotspot_function_name: str,
    hotspot_file_name: str,
    func_impl_dict: Dict[str, Dict[str, Any]],
    file_to_functions: Dict[str, List[str]],
    filter_libraries: Set[str]
) -> Tuple[Optional[str], Optional[str], int]:
    """
    Find the best matching function from merged_functions.json for a hotspot function.
    
    The matching is done by:
    1. First filter by file name - get all functions implemented in the same file
    2. Extract keywords from the hotspot function name
    3. For each candidate function, count how many keywords match
    4. Return the function with the most keyword matches
    
    Args:
        hotspot_function_name: The function name from hotspot data
        hotspot_file_name: The file name from hotspot data
        func_impl_dict: Dictionary mapping function names to implementations and keywords
        file_to_functions: Index mapping file names to list of function names
        filter_libraries: Set of library names for keyword extraction
        
    Returns:
        Tuple of (best_matching_function_name, best_matching_file, match_count)
        Returns (None, None, 0) if no match found
    """
    # Step 1: Get all functions implemented in the same file
    candidate_functions = file_to_functions.get(hotspot_file_name, [])
    
    if not candidate_functions:
        return None, None, 0
    
    # Step 2: Extract keywords from the hotspot function name
    hotspot_keywords = set(extract_keywords_from_function_name(hotspot_function_name, filter_libraries))
    
    if not hotspot_keywords:
        return None, None, 0
    
    # Step 3: For each candidate function, count keyword matches
    candidate_matches: Dict[str, int] = {}
    
    for func_name in candidate_functions:
        func_data = func_impl_dict.get(func_name, {})
        func_keywords = set(func_data.get("keywords", []))
        
        # Count how many hotspot keywords match the function's keywords
        match_count = len(hotspot_keywords & func_keywords)
        
        if match_count > 0:
            candidate_matches[func_name] = match_count
    
    if not candidate_matches:
        return None, None, 0
    
    # Step 4: Find the function with the most matches
    best_func = max(candidate_matches, key=candidate_matches.get)
    best_count = candidate_matches[best_func]
    
    # Get the file path for the best match
    best_file = None
    func_data = func_impl_dict.get(best_func, {})
    implementations = func_data.get("implementation", [])
    for impl in implementations:
        impl_file = impl.get("file_name", "")
        impl_file_name = Path(impl_file).name if impl_file else ""
        if impl_file_name == hotspot_file_name:
            best_file = impl_file
            break
    
    return best_func, best_file, best_count


def match_and_print_hotspot_functions(
    aggregated_output: Dict[str, Dict[str, Any]],
    func_impl_dict: Dict[str, Dict[str, Any]],
    file_to_functions: Dict[str, List[str]],
    filter_libraries: Set[str],
    out_dir: Optional[str] = None,
    hotspot_text_file: Optional[str] = None,
    hotspot_json_data: Optional[Dict[str, Any]] = None,
    skip_existing: bool = True,
    filtered_traces_dir: Optional[str] = None,
    num_slices: int = 30
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    For each aggregated hotspot function, find the best matching function from merged_functions.json
    and print both matched and unmatched functions.
    
    Args:
        aggregated_output: Dictionary of aggregated hotspot function costs
        func_impl_dict: Dictionary mapping function names to their implementations
        file_to_functions: Index mapping file names to list of function names
        filter_libraries: Set of library names to filter by
        out_dir: Output directory path (optional)
        hotspot_text_file: Path to processed hotspots text file (optional)
        hotspot_json_data: Loaded hotspot JSON data (optional)
        skip_existing: If True, skip if file already exists
        filtered_traces_dir: Path to filtered_traces directory with slice files (optional)
        num_slices: Number of slices in filtered_traces_dir (default: 30)
    
    Returns:
        Tuple of (matched_functions, unmatched_functions)
    """
    unmatched_functions = []
    matched_functions = []
    
    for key, value in aggregated_output.items():
        # Parse the key to extract file_name, function_name, library_name
        # Format: "(file_name, function_name, library_name)"
        match = re.match(r'\(([^,]*), ([^,]+), ([^)]*)\)', key)
        if not match:
            continue
        
        file_name = match.group(1).strip()
        function_name = match.group(2).strip()
        library_name = match.group(3).strip()
        self_cost = value.get("self_cost", value.get("cost", 0))
        inclusive_cost = value.get("inclusive_cost", self_cost)
        normalized_self_cost = value.get("normalized_self_cost", value.get("normalized_cost", 0.0))
        normalized_inclusive_cost = value.get("normalized_inclusive_cost", normalized_self_cost)
        
        # Find best matching function
        best_func, best_file, match_count = find_best_matching_function(
            function_name,
            file_name,
            func_impl_dict,
            file_to_functions,
            filter_libraries
        )
        
        if best_func:
            matched_functions.append({
                "file_name": file_name,
                "function_name": function_name,
                "library_name": library_name,
                "self_cost": self_cost,
                "inclusive_cost": inclusive_cost,
                "normalized_self_cost": normalized_self_cost,
                "normalized_inclusive_cost": normalized_inclusive_cost,
                "best_match": best_func
            })
        else:
            unmatched_functions.append({
                "file_name": file_name,
                "function_name": function_name,
                "library_name": library_name,
                "self_cost": self_cost,
                "inclusive_cost": inclusive_cost,
                "normalized_self_cost": normalized_self_cost,
                "normalized_inclusive_cost": normalized_inclusive_cost
            })
    
    # Print summary
    total_count = len(aggregated_output)
    print(f"\n" + "=" * 100)
    print(f"FUNCTION MATCHING SUMMARY")
    print(f"=" * 100)
    print(f"Total hotspot functions: {total_count}")
    print(f"Matched functions: {len(matched_functions)}")
    print(f"Unmatched functions: {len(unmatched_functions)}")
    
    # Print matched functions (just the function signature from merged_functions.json)
    if matched_functions:
        print(f"\n" + "-" * 100)
        print(f"MATCHED TRACE FUNCTIONS (function signature from merged_functions.json)")
        print(f"-" * 100)
        for func in matched_functions:
            print(f"  {func['best_match']}")
    
    # Print unmatched functions
    if unmatched_functions:
        print(f"\n" + "-" * 100)
        print(f"UNMATCHED TRACE FUNCTIONS (no match found in merged_functions.json)")
        print(f"-" * 100)
        for func in unmatched_functions:
            print(f"  ({func['file_name']}, {func['function_name']}, {func['library_name']})")
            print(f"    Self Cost: {func['self_cost']}, Inclusive Cost: {func['inclusive_cost']}")
            print(f"    Normalized Self: {func['normalized_self_cost']:.4f}%, Normalized Inclusive: {func['normalized_inclusive_cost']:.4f}%")
    else:
        print("\nAll functions matched successfully!")
    
    print("\n" + "=" * 100)
    
    # Write text files if out_dir is provided
    if out_dir:
        write_text_output_files(out_dir, aggregated_output, matched_functions, unmatched_functions, skip_existing=skip_existing)
    
    return matched_functions, unmatched_functions


def write_text_output_files(
    out_dir: str,
    aggregated_output: Dict[str, Dict[str, Any]],
    matched_functions: List[Dict[str, Any]],
    unmatched_functions: List[Dict[str, Any]],
    skip_existing: bool = True
) -> None:
    """
    Write text files for aggregated function costs, matched functions, and unmatched functions.
    
    Creates:
    - aggregated_function_costs.txt
    - matched_hotspot_functions_in_ast.csv
    - missing_hotspot_functions_in_ast.txt
    
    If skip_existing is True, skips files that already exist.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Write aggregated function costs
    aggregated_file = out_path / "aggregated_function_costs.txt"
    if skip_existing and aggregated_file.exists():
        print(f"SKIPPING: {aggregated_file} already exists")
    else:
        with open(aggregated_file, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("AGGREGATED FUNCTION COSTS (>= 0.0001%)\n")
            f.write("=" * 60 + "\n\n")
            
            for key, value in aggregated_output.items():
                f.write(f"{key}\n")
                self_cost = value.get('self_cost', value.get('cost', 0))
                inclusive_cost = value.get('inclusive_cost', self_cost)
                norm_self = value.get('normalized_self_cost', value.get('normalized_cost', 0.0))
                norm_incl = value.get('normalized_inclusive_cost', norm_self)
                f.write(f"  self_cost: {self_cost}, inclusive_cost: {inclusive_cost}\n")
                f.write(f"  normalized_self_cost: {norm_self:.4f}%, normalized_inclusive_cost: {norm_incl:.4f}%\n")
            
            f.write("\n" + "=" * 60 + "\n")
            f.write(f"Total unique functions: {len(aggregated_output)}\n")
        
        print(f"Wrote aggregated function costs to: {aggregated_file}")
    
    # Write matched hotspot functions in AST as CSV (de-duplicated by best_match + function_name combo)
    # Each line: <function_in_merged_functions_in_ast>, <matched_hotspot_function>
    matched_file = out_path / "matched_hotspot_functions_in_ast.csv"
    if skip_existing and matched_file.exists():
        print(f"SKIPPING: {matched_file} already exists")
    else:
        seen_matched = set()
        unique_matched = []
        for func in matched_functions:
            best_match = func['best_match']
            hotspot_func = func['function_name']
            combo_key = (best_match, hotspot_func)
            if combo_key not in seen_matched:
                seen_matched.add(combo_key)
                unique_matched.append(func)
        
        matched_count = len(unique_matched)
        with open(matched_file, 'w', encoding='utf-8') as f:
            # Write CSV header
            f.write("function_in_merged_functions_in_ast,matched_hotspot_function\n")
            
            for func in unique_matched:
                # Escape commas and quotes in function names for CSV format
                ast_func = func['best_match'].replace('"', '""')
                hotspot_func = func['function_name'].replace('"', '""')
                # Wrap in quotes if contains comma, quote, or newline
                if ',' in ast_func or '"' in ast_func or '\n' in ast_func:
                    ast_func = f'"{ast_func}"'
                if ',' in hotspot_func or '"' in hotspot_func or '\n' in hotspot_func:
                    hotspot_func = f'"{hotspot_func}"'
                f.write(f"{ast_func},{hotspot_func}\n")
        
        print(f"Wrote matched hotspot functions to: {matched_file} (matched: {matched_count})")
    
    # Write matched hotspot functions in AST as TXT (just function names, de-duplicated by best_match)
    matched_txt_file = out_path / "matched_hotspot_functions_in_ast.txt"
    if skip_existing and matched_txt_file.exists():
        print(f"SKIPPING: {matched_txt_file} already exists")
    else:
        seen_matched_txt = set()
        unique_matched_txt = []
        for func in matched_functions:
            best_match = func['best_match']
            if best_match not in seen_matched_txt:
                seen_matched_txt.add(best_match)
                unique_matched_txt.append(func)
        
        matched_txt_count = len(unique_matched_txt)
        with open(matched_txt_file, 'w', encoding='utf-8') as f:
            f.write("-" * 100 + "\n")
            f.write("MATCHED HOTSPOT FUNCTIONS IN AST (found in merged_functions.json)\n")
            f.write("-" * 100 + "\n\n")
            
            for func in unique_matched_txt:
                f.write(f"  {func['best_match']}\n")
            
            f.write("\n" + "-" * 100 + "\n")
            f.write(f"Total matched functions: {matched_txt_count}\n")
        
        print(f"Wrote matched hotspot functions to: {matched_txt_file} (matched: {matched_txt_count})")
    
    # Write matched hotspot functions in AST with costs (de-duplicated by file_name + function_name + library_name)
    # Similar format to missing_hotspot_functions_in_ast.txt
    matched_costs_file = out_path / "matched_hotspot_functions_in_ast_with_costs.txt"
    if skip_existing and matched_costs_file.exists():
        print(f"SKIPPING: {matched_costs_file} already exists")
    else:
        seen_matched_costs = set()
        unique_matched_costs = []
        for func in matched_functions:
            key = (func['file_name'], func['function_name'], func['library_name'])
            if key not in seen_matched_costs:
                seen_matched_costs.add(key)
                unique_matched_costs.append(func)
        
        matched_costs_count = len(unique_matched_costs)
        with open(matched_costs_file, 'w', encoding='utf-8') as f:
            f.write("-" * 100 + "\n")
            f.write("MATCHED HOTSPOT FUNCTIONS IN AST (found in merged_functions.json)\n")
            f.write("-" * 100 + "\n\n")
            
            for func in unique_matched_costs:
                f.write(f"  ({func['file_name']}, {func['function_name']}, {func['library_name']})\n")
                self_cost = func.get('self_cost', func.get('cost', 0))
                inclusive_cost = func.get('inclusive_cost', self_cost)
                norm_self = func.get('normalized_self_cost', func.get('normalized_cost', 0.0))
                norm_incl = func.get('normalized_inclusive_cost', norm_self)
                f.write(f"    Self Cost: {self_cost}, Inclusive Cost: {inclusive_cost}\n")
                f.write(f"    Normalized Self: {norm_self:.4f}%, Normalized Inclusive: {norm_incl:.4f}%\n")
            
            f.write("\n" + "-" * 100 + "\n")
            f.write(f"Total matched functions: {matched_costs_count}\n")
        
        print(f"Wrote matched hotspot functions with costs to: {matched_costs_file} (matched: {matched_costs_count})")
    
    # Write missing hotspot functions in AST (de-duplicated by file_name + function_name + library_name)
    unmatched_file = out_path / "missing_hotspot_functions_in_ast.txt"
    if skip_existing and unmatched_file.exists():
        print(f"SKIPPING: {unmatched_file} already exists")
    else:
        seen_unmatched = set()
        unique_unmatched = []
        for func in unmatched_functions:
            key = (func['file_name'], func['function_name'], func['library_name'])
            if key not in seen_unmatched:
                seen_unmatched.add(key)
                unique_unmatched.append(func)
        
        missing_count = len(unique_unmatched)
        with open(unmatched_file, 'w', encoding='utf-8') as f:
            f.write("-" * 100 + "\n")
            f.write("MISSING HOTSPOT FUNCTIONS IN AST (no match found in merged_functions.json)\n")
            f.write("-" * 100 + "\n\n")
            
            for func in unique_unmatched:
                f.write(f"  ({func['file_name']}, {func['function_name']}, {func['library_name']})\n")
                self_cost = func.get('self_cost', func.get('cost', 0))
                inclusive_cost = func.get('inclusive_cost', self_cost)
                norm_self = func.get('normalized_self_cost', func.get('normalized_cost', 0.0))
                norm_incl = func.get('normalized_inclusive_cost', norm_self)
                f.write(f"    Self Cost: {self_cost}, Inclusive Cost: {inclusive_cost}\n")
                f.write(f"    Normalized Self: {norm_self:.4f}%, Normalized Inclusive: {norm_incl:.4f}%\n")
            
            f.write("\n" + "-" * 100 + "\n")
            f.write(f"Total missing functions: {missing_count}\n")
        
        print(f"Wrote missing hotspot functions to: {unmatched_file} (missing: {missing_count})")


def write_function_callstack_csv(
    output_file: Path,
    results: List[Dict[str, Any]]
) -> None:
    """
    Write a CSV file with function_index and callstack indices.
    
    Output format:
    function_index,callstack_index1,callstack_index2,callstack_index3
    
    Args:
        output_file: Path to output CSV file
        results: List of result dictionaries with 'indices' key
    """
    with open(output_file, 'w', encoding='utf-8') as f:
        # Write header
        f.write("function_index,callstack_index1,callstack_index2,callstack_index3\n")
        
        for func_idx, result in enumerate(results):
            indices = result.get('indices', [])
            # Pad with empty strings if fewer than 3 indices
            idx1 = str(indices[0]) if len(indices) > 0 else ""
            idx2 = str(indices[1]) if len(indices) > 1 else ""
            idx3 = str(indices[2]) if len(indices) > 2 else ""
            f.write(f"{func_idx},{idx1},{idx2},{idx3}\n")
    
    print(f"Wrote function-callstack mapping CSV to: {output_file}")


def write_picked_traces_file(
    output_file: Path,
    results: List[Dict[str, Any]]
) -> None:
    """
    Write picked traces to a text file in the same format as processed.txt.
    
    For each function that has a matched callstack, writes the callstack content
    to the output file. Each callstack is separated by '====='.
    
    Output format (same as processed.txt):
    percentage% (cost) library function_name (source_file)
    ...
    =====
    
    Args:
        output_file: Path to output text file (picked_traces.txt)
        results: List of result dictionaries with 'callstack_lines' key
    """
    traces_written = 0
    
    with open(output_file, 'w', encoding='utf-8') as f:
        first_trace = True
        
        for result in results:
            callstack_lines = result.get('callstack_lines', [])
            
            if not callstack_lines:
                continue
            
            # Add separator before each trace (except the first one)
            if not first_trace:
                f.write("\n=====\n\n")
            first_trace = False
            
            # Write the callstack lines
            for line in callstack_lines:
                f.write(line + "\n")
            
            traces_written += 1
    
    print(f"Wrote picked traces to: {output_file} ({traces_written} traces)")


def reservoir_sample(stream: Iterator, k: int, seed: Optional[int] = None) -> List:
    """
    Reservoir sampling algorithm to randomly select k items from a stream.
    
    This algorithm ensures uniform random sampling without needing to know
    the total size of the stream in advance.
    
    Args:
        stream: An iterator of items to sample from
        k: Number of items to select
        seed: Optional random seed for reproducibility
        
    Returns:
        List of k randomly selected items (or fewer if stream has less than k items)
    """
    rng = random.Random(seed)
    res = []
    for i, item in enumerate(stream):
        if i < k:
            res.append(item)
        else:
            j = rng.randrange(i + 1)
            if j < k:
                res[j] = item
    return res


def load_callstacks_into_memory(
    processed_text_file: str,
    progress_interval: int = 10000
) -> Tuple[List[Tuple[int, List[str]]], Dict[str, List[int]]]:
    """
    Load all callstacks from processed.txt into memory and build a function-to-callstack index.
    
    This is much faster than calling grep for each function since we only read the file once.
    
    Args:
        processed_text_file: Path to processed.txt file
        progress_interval: Print progress every N callstacks (default: 10000)
        
    Returns:
        Tuple of:
        - List of (callstack_index, lines) tuples
        - Dict mapping function names to list of callstack indices containing them
    """
    callstacks: List[Tuple[int, List[str]]] = []
    function_to_callstacks: Dict[str, List[int]] = {}
    
    current_index = 0
    current_lines: List[str] = []
    last_progress_report = 0
    
    print(f"  Loading callstacks from {processed_text_file}...", flush=True)
    
    try:
        with open(processed_text_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.strip() == '=====':
                    if current_lines:
                        # Save the current callstack
                        callstacks.append((current_index, current_lines))
                        
                        # Print progress
                        if current_index - last_progress_report >= progress_interval:
                            print(f"    Loaded {current_index} callstacks...", flush=True)
                            last_progress_report = current_index
                        
                        # Index all functions in this callstack
                        for cs_line in current_lines:
                            # Extract function name from line
                            # Format: "percentage% (cost) library function_name (source_file)"
                            cost_end = cs_line.find(')')
                            if cost_end != -1:
                                rest = cs_line[cost_end + 1:].strip()
                                if rest:
                                    parts = rest.split(None, 1)
                                    if len(parts) >= 2:
                                        func_and_source = parts[1]
                                        # Remove source file if present
                                        last_paren = func_and_source.rfind(' (')
                                        if last_paren != -1:
                                            potential_source = func_and_source[last_paren + 2:-1] if func_and_source.endswith(')') else ""
                                            if '.' in potential_source and not potential_source.startswith('['):
                                                func_name = func_and_source[:last_paren]
                                            else:
                                                func_name = func_and_source
                                        else:
                                            func_name = func_and_source
                                        
                                        if func_name:
                                            if func_name not in function_to_callstacks:
                                                function_to_callstacks[func_name] = []
                                            if current_index not in function_to_callstacks[func_name]:
                                                function_to_callstacks[func_name].append(current_index)
                        
                        # Only increment index after saving a non-empty callstack
                        current_index += 1
                    
                    # Reset lines for next callstack (whether we saved or not)
                    current_lines = []
                else:
                    if line.strip():
                        current_lines.append(line.rstrip())
            
            # Handle last callstack (no trailing separator)
            if current_lines:
                callstacks.append((current_index, current_lines))
                
                # Index functions in last callstack
                for cs_line in current_lines:
                    cost_end = cs_line.find(')')
                    if cost_end != -1:
                        rest = cs_line[cost_end + 1:].strip()
                        if rest:
                            parts = rest.split(None, 1)
                            if len(parts) >= 2:
                                func_and_source = parts[1]
                                last_paren = func_and_source.rfind(' (')
                                if last_paren != -1:
                                    potential_source = func_and_source[last_paren + 2:-1] if func_and_source.endswith(')') else ""
                                    if '.' in potential_source and not potential_source.startswith('['):
                                        func_name = func_and_source[:last_paren]
                                    else:
                                        func_name = func_and_source
                                else:
                                    func_name = func_and_source
                                
                                if func_name:
                                    if func_name not in function_to_callstacks:
                                        function_to_callstacks[func_name] = []
                                    if current_index not in function_to_callstacks[func_name]:
                                        function_to_callstacks[func_name].append(current_index)
    
    except Exception as e:
        print(f"Error loading callstacks: {e}")
        return [], {}
    
    print(f"  Loaded {len(callstacks)} callstacks, indexed {len(function_to_callstacks)} unique functions")
    
    return callstacks, function_to_callstacks


def select_callstacks_by_function_coverage(
    matched_csv_path: Path,
    processed_text_file: str
) -> Tuple[List[Tuple[int, List[str]]], Set[str], Set[str]]:
    """
    Select callstacks by iterating through matched functions and finding first match for each.
    
    Algorithm:
    1. Load all callstacks into memory and build function-to-callstack index
    2. Read matched_hotspot_functions_in_ast.csv
    3. For each hotspot function, look up in the index (O(1))
    4. De-duplicate callstacks by index
    5. Return unique callstacks with their full content
    
    Args:
        matched_csv_path: Path to matched_hotspot_functions_in_ast.csv
        processed_text_file: Path to processed.txt
        
    Returns:
        Tuple of (list of (index, lines) tuples, set of covered function names, set of not found functions)
    """
    # Load all callstacks into memory and build index
    all_callstacks, function_index = load_callstacks_into_memory(processed_text_file)
    
    if not all_callstacks:
        return [], set(), set()
    
    # Create a dict for O(1) callstack lookup by index
    callstack_by_index = {idx: lines for idx, lines in all_callstacks}
    
    # Read matched functions from CSV
    hotspot_functions = []
    try:
        with open(matched_csv_path, 'r', encoding='utf-8') as f:
            # Skip header
            next(f, None)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # CSV format: function_in_merged_functions_in_ast,matched_hotspot_function
                # Parse CSV properly handling quotes
                parts = []
                current = []
                in_quotes = False
                for char in line:
                    if char == '"':
                        in_quotes = not in_quotes
                    elif char == ',' and not in_quotes:
                        parts.append(''.join(current))
                        current = []
                    else:
                        current.append(char)
                parts.append(''.join(current))
                
                if len(parts) >= 2:
                    hotspot_func = parts[1].strip().strip('"')
                    if hotspot_func:
                        hotspot_functions.append(hotspot_func)
    except Exception as e:
        print(f"Error reading matched functions CSV: {e}")
        return [], set(), set()
    
    print(f"  Found {len(hotspot_functions)} hotspot functions to search for")
    
    # Find callstacks for each function using the in-memory index
    selected_indices: Set[int] = set()
    covered_functions: Set[str] = set()
    not_found_functions: Set[str] = set()
    
    total_funcs = len(hotspot_functions)
    progress_interval = max(1, total_funcs // 20)  # Report every 5%
    
    for i, func_name in enumerate(hotspot_functions):
        # Progress update
        if (i + 1) % progress_interval == 0:
            pct = (i + 1) * 100 // total_funcs
            print(f"    Processed {i + 1}/{total_funcs} functions ({pct}%), found {len(selected_indices)} unique callstacks", flush=True)
        
        # Look up in the index (O(1))
        if func_name in function_index:
            # Get the first callstack index for this function
            cs_indices = function_index[func_name]
            if cs_indices:
                selected_indices.add(cs_indices[0])  # Add first match
                covered_functions.add(func_name)
        else:
            not_found_functions.add(func_name)
    
    print(f"  Found {len(selected_indices)} unique callstacks covering {len(covered_functions)} functions")
    print(f"  {len(not_found_functions)} functions not found in processed.txt")
    
    # Build result list with full callstack content
    result = [(idx, callstack_by_index[idx]) for idx in sorted(selected_indices) if idx in callstack_by_index]
    
    return result, covered_functions, not_found_functions


def iter_callstacks_from_text_file(hotspot_text_file: str) -> Iterator[Tuple[int, List[str]]]:
    """
    Iterate over callstacks from a text file, yielding (index, lines) tuples.
    
    Args:
        hotspot_text_file: Path to the processed hotspots text file
        
    Yields:
        Tuples of (callstack_index, list_of_lines)
    """
    current_index = 0
    current_lines = []
    
    try:
        with open(hotspot_text_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.strip() == '=====':
                    if current_lines:
                        yield (current_index, current_lines)
                    current_index += 1
                    current_lines = []
                else:
                    if line.strip():  # Skip empty lines
                        current_lines.append(line.rstrip())
            
            # Yield the last callstack (no trailing separator)
            if current_lines:
                yield (current_index, current_lines)
    except Exception as e:
        print(f"Error reading callstacks from text file: {e}")


def iter_callstacks_from_json(hotspot_json_data: Dict[str, Any]) -> Iterator[Tuple[int, List[str]]]:
    """
    Iterate over callstacks from JSON data, yielding (index, lines) tuples.
    
    Args:
        hotspot_json_data: The loaded hotspot JSON data
        
    Yields:
        Tuples of (callstack_index, list_of_lines)
    """
    if not isinstance(hotspot_json_data, dict) or 'callstack' not in hotspot_json_data:
        return
    
    callstack = hotspot_json_data.get("callstack", {})
    
    # Get all leaf paths
    leaf_paths = []
    stack = deque([(callstack,
                   [callstack.get("frameName", "<unnamed>")],
                   [callstack.get("value")],
                   [callstack.get("ownerName", "")],
                   [callstack.get("sourcePath", "")])])
    
    while stack:
        node, frames, vals, owners, sources = stack.pop()
        children = node.get("children")
        
        if not children:  # leaf node
            leaf_paths.append({
                "path": list(reversed(frames)),
                "cost": [int(c or 0) for c in reversed(vals)],
                "ownerName": list(reversed(owners)),
                "sourcePath": list(reversed(sources))
            })
        else:
            for ch in reversed(children):
                stack.append((
                    ch,
                    frames + [ch.get("frameName", "<unnamed>")],
                    vals + [ch.get("value")],
                    owners + [ch.get("ownerName", "")],
                    sources + [ch.get("sourcePath", "")]
                ))
    
    for idx, entry in enumerate(leaf_paths):
        lines = []
        
        # Get total cost for percentage calculation
        total_cost = entry["cost"][0] if entry["cost"] else 1
        
        for path, cost, owner, source in zip(
                entry["path"], entry["cost"], entry["ownerName"], entry["sourcePath"]):
            if path and path.strip() and path.strip() != "???" and path.strip() != "<root>":
                # Calculate percentage
                percentage = int(round((cost / total_cost) * 100)) if total_cost > 0 else 0
                
                # Build line similar to convert_entry_to_text_line
                line = f"{percentage}% ({cost})"
                if owner:
                    line += f" {owner}"
                line += f" {path}"
                if source:
                    source_filename = Path(source).name
                    line += f" ({source_filename})"
                
                lines.append(line)
        
        if lines:
            yield (idx, lines)


def get_callstack_text_by_index(
    callstack_index: int,
    hotspot_text_file: Optional[str] = None,
    hotspot_json_data: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Get the text representation of a callstack by its index.
    
    Args:
        callstack_index: The 0-based index of the callstack
        hotspot_text_file: Path to processed hotspots text file (optional)
        hotspot_json_data: Loaded hotspot JSON data (optional)
        
    Returns:
        List of lines representing the callstack
    """
    if hotspot_text_file and Path(hotspot_text_file).exists():
        return get_callstack_from_text_file(callstack_index, hotspot_text_file)
    elif hotspot_json_data:
        return get_callstack_from_json(callstack_index, hotspot_json_data)
    return []


def get_callstack_from_text_file(callstack_index: int, hotspot_text_file: str) -> List[str]:
    """
    Extract a specific callstack from the text file by index.
    
    Args:
        callstack_index: The 0-based index of the callstack
        hotspot_text_file: Path to the text file
        
    Returns:
        List of lines for that callstack
    """
    current_index = 0
    current_lines = []
    
    try:
        with open(hotspot_text_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.strip() == '=====':
                    if current_index == callstack_index:
                        return current_lines
                    current_index += 1
                    current_lines = []
                else:
                    if line.strip():  # Skip empty lines
                        current_lines.append(line.rstrip())
            
            # Handle last callstack (no trailing separator)
            if current_index == callstack_index:
                return current_lines
    except Exception as e:
        print(f"Error reading callstack from text file: {e}")
    
    return []


def get_callstack_from_json(callstack_index: int, hotspot_json_data: Dict[str, Any]) -> List[str]:
    """
    Extract a specific callstack from the JSON data by index.
    
    Converts the JSON callstack to text format similar to processed.txt.
    
    Args:
        callstack_index: The 0-based index of the callstack (leaf path index)
        hotspot_json_data: The loaded hotspot JSON data
        
    Returns:
        List of lines for that callstack in text format
    """
    if not isinstance(hotspot_json_data, dict) or 'callstack' not in hotspot_json_data:
        return []
    
    callstack = hotspot_json_data.get("callstack", {})
    
    # Get all leaf paths
    leaf_paths = []
    stack = deque([(callstack,
                   [callstack.get("frameName", "<unnamed>")],
                   [callstack.get("value")],
                   [callstack.get("ownerName", "")],
                   [callstack.get("sourcePath", "")])])
    
    while stack:
        node, frames, vals, owners, sources = stack.pop()
        children = node.get("children")
        
        if not children:  # leaf node
            leaf_paths.append({
                "path": list(reversed(frames)),
                "cost": [int(c or 0) for c in reversed(vals)],
                "ownerName": list(reversed(owners)),
                "sourcePath": list(reversed(sources))
            })
        else:
            for ch in reversed(children):
                stack.append((
                    ch,
                    frames + [ch.get("frameName", "<unnamed>")],
                    vals + [ch.get("value")],
                    owners + [ch.get("ownerName", "")],
                    sources + [ch.get("sourcePath", "")]
                ))
    
    if callstack_index >= len(leaf_paths):
        return []
    
    entry = leaf_paths[callstack_index]
    lines = []
    
    # Get total cost for percentage calculation
    total_cost = entry["cost"][0] if entry["cost"] else 1
    
    for i, (path, cost, owner, source) in enumerate(zip(
            entry["path"], entry["cost"], entry["ownerName"], entry["sourcePath"])):
        if path and path.strip() and path.strip() != "???" and path.strip() != "<root>":
            # Calculate percentage
            percentage = int(round((cost / total_cost) * 100)) if total_cost > 0 else 0
            
            # Build line similar to convert_entry_to_text_line
            line = f"{percentage}% ({cost})"
            if owner:
                line += f" {owner}"
            line += f" {path}"
            if source:
                source_filename = Path(source).name
                line += f" ({source_filename})"
            
            lines.append(line)
    
    return lines


def extract_function_names_from_callstack_lines(
    lines: List[str],
    filter_libraries: Set[str]
) -> Set[str]:
    """
    Extract function names from callstack lines, filtering by library.
    
    Each line has format: "percentage% (cost) library function_name (source_file)"
    Example: "2% (35012) CoreLocation -[CLLocationManager requestLocation] (CLLocationManager.m)"
    
    Args:
        lines: List of callstack lines
        filter_libraries: Set of library names to filter by
        
    Returns:
        Set of function names from the specified libraries
    """
    function_names = set()
    
    for line in lines:
        # Parse line format: "percentage% (cost) library function_name (source_file)"
        # The library and function are after the (cost) part
        
        # Find the closing parenthesis of the cost
        cost_end = line.find(')')
        if cost_end == -1:
            continue
        
        # Everything after the cost
        rest = line[cost_end + 1:].strip()
        if not rest:
            continue
        
        # Split into parts - first part is library, rest is function name (possibly with source file)
        parts = rest.split(None, 1)
        if len(parts) < 2:
            continue
        
        library = parts[0]
        func_and_source = parts[1]
        
        # Check if library matches filter
        if filter_libraries and library not in filter_libraries:
            continue
        
        # Remove source file if present (it's in parentheses at the end)
        # But be careful - Objective-C methods have brackets like -[Class method]
        # Source file is always at the very end like (filename.m)
        func_name = func_and_source
        
        # Find the last occurrence of " (" which indicates source file
        last_paren = func_and_source.rfind(' (')
        if last_paren != -1:
            # Check if this looks like a source file (ends with .something)
            potential_source = func_and_source[last_paren + 2:-1] if func_and_source.endswith(')') else ""
            if '.' in potential_source and not potential_source.startswith('['):
                func_name = func_and_source[:last_paren]
        
        if func_name:
            function_names.add(func_name)
    
    return function_names


def compute_coverage(
    selected_callstacks: List[Tuple[int, List[str]]],
    matched_functions_csv_path: Path,
    filter_libraries: Set[str]
) -> Tuple[int, int, float]:
    """
    Compute coverage of matched functions by the selected callstacks.
    
    Args:
        selected_callstacks: List of (index, lines) tuples for selected callstacks
        matched_functions_csv_path: Path to matched_hotspot_functions_in_ast.csv
        filter_libraries: Set of library names to filter by
        
    Returns:
        Tuple of (covered_count, total_count, coverage_percentage)
    """
    # Load matched hotspot functions from CSV
    matched_hotspot_functions = set()
    try:
        with open(matched_functions_csv_path, 'r', encoding='utf-8') as f:
            # Skip header
            next(f, None)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # CSV format: function_in_merged_functions_in_ast,matched_hotspot_function
                # We need the matched_hotspot_function (second column)
                # Handle CSV escaping
                if ',' in line:
                    # Simple CSV parsing - find the last comma that's not inside quotes
                    parts = []
                    current = []
                    in_quotes = False
                    for char in line:
                        if char == '"':
                            in_quotes = not in_quotes
                        elif char == ',' and not in_quotes:
                            parts.append(''.join(current))
                            current = []
                        else:
                            current.append(char)
                    parts.append(''.join(current))
                    
                    if len(parts) >= 2:
                        hotspot_func = parts[1].strip().strip('"')
                        if hotspot_func:
                            matched_hotspot_functions.add(hotspot_func)
    except Exception as e:
        print(f"Error reading matched functions CSV: {e}")
        return 0, 0, 0.0
    
    if not matched_hotspot_functions:
        print("No matched functions found in CSV")
        return 0, 0, 0.0
    
    # Extract function names from selected callstacks
    functions_in_selected = set()
    for _, lines in selected_callstacks:
        funcs = extract_function_names_from_callstack_lines(lines, filter_libraries)
        functions_in_selected.update(funcs)
    
    # Compute coverage
    covered_functions = matched_hotspot_functions & functions_in_selected
    total_functions = len(matched_hotspot_functions)
    covered_count = len(covered_functions)
    
    coverage_pct = (covered_count / total_functions * 100.0) if total_functions > 0 else 0.0
    
    return covered_count, total_functions, coverage_pct


def write_selected_traces_file(
    output_file: Path,
    selected_callstacks: List[Tuple[int, List[str]]],
    matched_functions_csv_path: Optional[Path] = None,
    filter_libraries: Optional[Set[str]] = None
) -> None:
    """
    Write randomly selected callstacks to a text file.
    
    Format is similar to processed.txt - each callstack separated by '====='.
    
    Args:
        output_file: Path to output file
        selected_callstacks: List of (index, lines) tuples for selected callstacks
        matched_functions_csv_path: Optional path to matched_hotspot_functions_in_ast.csv for coverage calculation
        filter_libraries: Optional set of library names to filter by for coverage calculation
    """
    with open(output_file, 'w', encoding='utf-8') as f:
        for i, (cs_idx, lines) in enumerate(selected_callstacks):
            # Write callstack lines
            if lines:
                for line in lines:
                    f.write(line + "\n")
            
            # Add separator between callstacks (except for the last one)
            if i < len(selected_callstacks) - 1:
                f.write("\n=====\n\n")
    
    print(f"Wrote selected traces to: {output_file} ({len(selected_callstacks)} callstacks)")
    
    # Compute and print coverage if CSV path is provided
    if matched_functions_csv_path and matched_functions_csv_path.exists() and filter_libraries:
        covered, total, coverage_pct = compute_coverage(
            selected_callstacks,
            matched_functions_csv_path,
            filter_libraries
        )
        print(f"\n" + "=" * 60)
        print(f"COVERAGE ANALYSIS")
        print(f"=" * 60)
        print(f"  Total matched functions (from CSV): {total}")
        print(f"  Functions covered by selected traces: {covered}")
        print(f"  Coverage: {coverage_pct:.2f}%")
        print(f"=" * 60)


def find_callstack_indices_for_function(
    function_name: str,
    hotspot_text_file: str,
    max_matches: int = 3
) -> List[int]:
    """
    Find callstack indices that contain the given function name.
    
    Uses logic similar to ctx_grep.py - searches through the processed text file
    and finds callstack groups (separated by '=====') that contain the function.
    
    Args:
        function_name: The function name to search for
        hotspot_text_file: Path to the processed hotspots text file
        max_matches: Maximum number of matches to return (default: 3)
        
    Returns:
        List of callstack indices (0-based) that contain the function
    """
    matching_indices = []
    current_callstack_index = 0
    current_callstack_has_match = False
    
    try:
        with open(hotspot_text_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                # Check for callstack separator
                if line.strip() == '=====':
                    # If current callstack had a match, record it
                    if current_callstack_has_match:
                        matching_indices.append(current_callstack_index)
                        if len(matching_indices) >= max_matches:
                            break
                    # Move to next callstack
                    current_callstack_index += 1
                    current_callstack_has_match = False
                    continue
                
                # Check if this line contains the function name
                if not current_callstack_has_match and function_name in line:
                    current_callstack_has_match = True
            
            # Handle the last callstack (no trailing separator)
            if current_callstack_has_match and len(matching_indices) < max_matches:
                matching_indices.append(current_callstack_index)
    
    except Exception as e:
        print(f"Error reading hotspot text file: {e}")
        return []
    
    return matching_indices


def find_callstack_in_slices(
    function_name: str,
    filtered_traces_dir: str,
    num_slices: int = 30,
    max_matches: int = 1
) -> Tuple[List[Tuple[int, int, List[str]]], int]:
    """
    Search for callstacks containing a function across all slices, starting from a random slice index.
    
    This function starts searching from a random slice index (0 to num_slices-1) and wraps around
    to slice 0 if the function is not found in higher-indexed slices.
    
    Args:
        function_name: The function name to search for
        filtered_traces_dir: Path to the filtered_traces directory containing slice_XXX.txt files
        num_slices: Number of slices (default: 30)
        max_matches: Maximum number of matches to return (default: 1)
        
    Returns:
        Tuple of:
        - List of (slice_index, callstack_index_within_slice, callstack_lines) tuples
        - The starting slice index that was randomly chosen
    """
    filtered_path = Path(filtered_traces_dir)
    
    # Generate a random starting slice index
    start_slice = random.randint(0, num_slices - 1)
    
    matches: List[Tuple[int, int, List[str]]] = []
    
    # Search through slices starting from random index, wrapping around
    for offset in range(num_slices):
        slice_idx = (start_slice + offset) % num_slices
        slice_file = filtered_path / f"slice_{slice_idx:03d}.txt"
        
        if not slice_file.exists():
            continue
        
        # Search this slice for the function
        try:
            current_callstack_index = 0
            current_lines: List[str] = []
            current_has_match = False
            
            with open(slice_file, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if line.strip() == '=====':
                        # End of current callstack
                        if current_has_match and current_lines:
                            matches.append((slice_idx, current_callstack_index, current_lines.copy()))
                            if len(matches) >= max_matches:
                                return matches, start_slice
                        
                        current_callstack_index += 1
                        current_lines = []
                        current_has_match = False
                    else:
                        stripped = line.rstrip()
                        if stripped:
                            current_lines.append(stripped)
                            if not current_has_match and function_name in line:
                                current_has_match = True
                
                # Handle last callstack in file (no trailing separator)
                if current_has_match and current_lines:
                    matches.append((slice_idx, current_callstack_index, current_lines.copy()))
                    if len(matches) >= max_matches:
                        return matches, start_slice
        
        except Exception as e:
            # Continue to next slice on error
            continue
    
    return matches, start_slice


def find_callstack_indices_from_json(
    function_name: str,
    hotspot_json_data: Dict[str, Any],
    max_matches: int = 3
) -> List[int]:
    """
    Find callstack indices that contain the given function name by traversing the JSON.
    
    This is an alternative to text file search - directly parses the JSON structure.
    
    Args:
        function_name: The function name to search for
        hotspot_json_data: The loaded hotspot JSON data (dict with 'callstack' key)
        max_matches: Maximum number of matches to return (default: 3)
        
    Returns:
        List of callstack indices (0-based) that contain the function
    """
    from collections import deque
    
    if not isinstance(hotspot_json_data, dict) or 'callstack' not in hotspot_json_data:
        return []
    
    callstack = hotspot_json_data.get("callstack", {})
    matching_indices = []
    callstack_index = 0
    
    # Use iter_flat_paths logic to get all leaf paths
    stack = deque([(callstack,
                   [callstack.get("frameName", "<unnamed>")])])
    
    # Track which callstack index each leaf belongs to
    leaf_paths = []
    
    while stack:
        node, frames = stack.pop()
        children = node.get("children")
        
        if not children:  # leaf node
            leaf_paths.append(frames)
        else:
            for ch in reversed(children):
                stack.append((
                    ch,
                    frames + [ch.get("frameName", "<unnamed>")]
                ))
    
    # Search through leaf paths for function name matches
    for idx, path in enumerate(leaf_paths):
        # Check if any frame in this path contains the function name
        for frame in path:
            if function_name in frame:
                matching_indices.append(idx)
                break
        
        if len(matching_indices) >= max_matches:
            break
    
    return matching_indices


def _process_callstack_chunk(
    chunk: List[Dict[str, Any]],
    hotspot_text_file: Optional[str],
    hotspot_json_data: Optional[Dict[str, Any]],
    max_matches: int,
    chunk_id: int,
    total_chunks: int,
    filtered_traces_dir: Optional[str] = None,
    num_slices: int = 30
) -> List[Dict[str, Any]]:
    """
    Process a chunk of functions to find their callstack indices.
    
    This function is designed to be run in parallel threads.
    
    When filtered_traces_dir is provided, searches through all slices starting from a random
    slice index and wrapping around if not found.
    
    Args:
        chunk: List of function dictionaries to process
        hotspot_text_file: Path to processed hotspots text file (optional)
        hotspot_json_data: Loaded hotspot JSON data (optional)
        max_matches: Maximum number of callstack matches per function
        chunk_id: Identifier for this chunk (for logging)
        total_chunks: Total number of chunks (for logging)
        filtered_traces_dir: Path to filtered_traces directory with slice files (optional)
        num_slices: Number of slices in filtered_traces_dir (default: 30)
        
    Returns:
        List of result dictionaries with function info, callstack indices, and callstack content
    """
    chunk_results = []
    chunk_size = len(chunk)
    
    # Calculate progress intervals (report at 2%, 4%, 6%, etc.)
    progress_interval = max(1, chunk_size // 50)
    
    for idx, func in enumerate(chunk, 1):
        hotspot_function_name = func.get('function_name', '')
        ast_function_name = func.get('best_match', '')
        
        if not hotspot_function_name:
            continue
        
        indices = []
        callstack_lines: List[str] = []
        start_slice = -1
        
        # Find callstack using slice-based search if filtered_traces_dir is provided
        if filtered_traces_dir and Path(filtered_traces_dir).exists():
            matches, start_slice = find_callstack_in_slices(
                hotspot_function_name, filtered_traces_dir, num_slices, max_matches
            )
            if matches:
                # Extract indices and callstack content from matches
                for slice_idx, cs_idx, lines in matches:
                    # Create a composite index: slice_idx * 1000000 + cs_idx
                    # This allows us to identify both the slice and the callstack within it
                    composite_idx = slice_idx * 1000000 + cs_idx
                    indices.append(composite_idx)
                    # Store the first match's callstack lines
                    if not callstack_lines and lines:
                        callstack_lines = lines
        elif hotspot_text_file and Path(hotspot_text_file).exists():
            indices = find_callstack_indices_for_function(
                hotspot_function_name, hotspot_text_file, max_matches
            )
        elif hotspot_json_data:
            indices = find_callstack_indices_from_json(
                hotspot_function_name, hotspot_json_data, max_matches
            )
        
        chunk_results.append({
            'hotspot_function_name': hotspot_function_name,
            'ast_function_name': ast_function_name,
            'indices': indices,
            'callstack_lines': callstack_lines,
            'start_slice': start_slice
        })
        
        # Print progress within chunk at 2% intervals
        if idx % progress_interval == 0 or idx == chunk_size:
            percent = (idx * 100) // chunk_size
            print(f"  [Thread {chunk_id + 1}/{total_chunks}] {percent}% ({idx}/{chunk_size})", flush=True)
    
    return chunk_results


def write_callstack_matches_file(
    out_dir: str,
    matched_functions: List[Dict[str, Any]],
    hotspot_text_file: Optional[str] = None,
    hotspot_json_data: Optional[Dict[str, Any]] = None,
    max_matches: int = 3,
    skip_existing: bool = True,
    num_threads: int = 4,
    filtered_traces_dir: Optional[str] = None,
    num_slices: int = 30
) -> None:
    """
    Write a text file containing each matched function and its callstack indices.
    
    Uses parallel processing with 4 threads, each processing 25% of functions.
    
    For each function in matched_functions, finds up to max_matches callstack indices
    that contain that function. When filtered_traces_dir is provided, searches through
    all slices starting from a random slice index.
    
    Output format:
    Function: <function_name>
    Callstack indices: [idx1, idx2, idx3]
    
    Also writes picked_traces.txt containing the callstack content for each matched function.
    
    Args:
        out_dir: Output directory path
        matched_functions: List of matched function dictionaries
        hotspot_text_file: Path to processed hotspots text file (optional)
        hotspot_json_data: Loaded hotspot JSON data (optional, used if text file not provided)
        max_matches: Maximum number of callstack matches per function (default: 3)
        skip_existing: If True, skip if file already exists
        num_threads: Number of threads to use for parallel processing (default: 4)
        filtered_traces_dir: Path to filtered_traces directory with slice files (optional)
        num_slices: Number of slices in filtered_traces_dir (default: 30)
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Define all output files
    output_file = out_path / "function_callstack_matches.txt"
    csv_output_file = out_path / "function_callstack_mapping.csv"
    selected_traces_file = out_path / "selected.txt"
    matched_csv_path = out_path / "matched_hotspot_functions_in_ast.csv"
    picked_traces_file = out_path / "picked_traces.txt"
    
    # Check which files need to be generated
    need_matches_file = not (skip_existing and output_file.exists())
    need_csv_file = not (skip_existing and csv_output_file.exists())
    need_selected_file = not (skip_existing and selected_traces_file.exists())
    need_picked_traces_file = not (skip_existing and picked_traces_file.exists())
    
    # Print skip messages for files that already exist and exit
    if skip_existing and csv_output_file.exists():
        print(f"SKIPPING: {csv_output_file} already exists")
        sys.exit(0)
    
    if skip_existing and output_file.exists():
        print(f"SKIPPING: {output_file} already exists")
    if skip_existing and selected_traces_file.exists():
        print(f"SKIPPING: {selected_traces_file} already exists")
    if skip_existing and picked_traces_file.exists():
        print(f"SKIPPING: {picked_traces_file} already exists")
    
    # If all files exist and skip_existing is True, we're done
    if not need_matches_file and not need_csv_file and not need_selected_file and not need_picked_traces_file:
        return
    
    # We need to process callstack matching if we need the matches file or CSV file
    ordered_results = None
    if need_matches_file or need_csv_file or need_picked_traces_file:
        # De-duplicate by hotspot function_name (the original hotspot function, not the AST match)
        # We search for the hotspot function name in callstacks since that's what appears in the trace
        seen_functions = set()
        unique_functions = []
        for func in matched_functions:
            hotspot_func = func.get('function_name', '')
            if hotspot_func and hotspot_func not in seen_functions:
                seen_functions.add(hotspot_func)
                unique_functions.append(func)
        
        total_functions = len(unique_functions)
        print(f"\nSearching for callstack matches for {total_functions} functions using {num_threads} threads...")
        if filtered_traces_dir:
            print(f"  Using slice-based search with random starting slice (searching {num_slices} slices)")
        
        # Split unique_functions into chunks for parallel processing
        chunk_size = (total_functions + num_threads - 1) // num_threads  # Ceiling division
        chunks = []
        for i in range(num_threads):
            start_idx = i * chunk_size
            end_idx = min(start_idx + chunk_size, total_functions)
            if start_idx < total_functions:
                chunks.append(unique_functions[start_idx:end_idx])
        
        print(f"  Split into {len(chunks)} chunks of ~{chunk_size} functions each")
        
        # Process chunks in parallel using ThreadPoolExecutor
        all_results = []
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            # Submit all chunks for processing
            future_to_chunk_id = {
                executor.submit(
                    _process_callstack_chunk,
                    chunk,
                    hotspot_text_file,
                    hotspot_json_data,
                    max_matches,
                    i,
                    len(chunks),
                    filtered_traces_dir,
                    num_slices
                ): i
                for i, chunk in enumerate(chunks)
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_chunk_id):
                chunk_id = future_to_chunk_id[future]
                try:
                    chunk_results = future.result()
                    all_results.append((chunk_id, chunk_results))
                    print(f"  Thread {chunk_id + 1}/{len(chunks)} completed: processed {len(chunk_results)} functions")
                except Exception as e:
                    print(f"  Thread {chunk_id + 1}/{len(chunks)} failed with error: {e}")
        
        # Sort results by chunk_id to maintain original order
        all_results.sort(key=lambda x: x[0])
        
        # Flatten results
        ordered_results = []
        for _, chunk_results in all_results:
            ordered_results.extend(chunk_results)
        
        # Count matches
        total_with_matches = sum(1 for r in ordered_results if r['indices'])
        total_without_matches = sum(1 for r in ordered_results if not r['indices'])
        
        print(f"\nCallstack matching complete:")
        print(f"  Functions with matches: {total_with_matches}")
        print(f"  Functions without matches: {total_without_matches}")
        
        # Write matches file if needed
        if need_matches_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write("=" * 80 + "\n")
                f.write("FUNCTION CALLSTACK MATCHES\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"Total functions scanned: {len(ordered_results)}\n")
                f.write(f"Max callstack matches per function: {max_matches}\n")
                f.write(f"Threads used: {num_threads}\n")
                if filtered_traces_dir:
                    f.write(f"Search method: Slice-based with random starting slice ({num_slices} slices)\n")
                f.write("\n")
                f.write("-" * 80 + "\n\n")
                
                for result in ordered_results:
                    f.write(f"AST Function: {result['ast_function_name']}\n")
                    f.write(f"Hotspot Function: {result['hotspot_function_name']}\n")
                    if result['indices']:
                        f.write(f"Callstack indices: {result['indices']}\n")
                        if result.get('start_slice', -1) >= 0:
                            f.write(f"Started search at slice: {result['start_slice']}\n")
                    else:
                        f.write("Callstack indices: [] (no matches found)\n")
                    f.write("\n")
                
                f.write("-" * 80 + "\n")
                f.write(f"\nSummary:\n")
                f.write(f"  Functions with callstack matches: {total_with_matches}\n")
                f.write(f"  Functions without matches: {total_without_matches}\n")
            
            print(f"Wrote function callstack matches to: {output_file}")
        
        # Write CSV file if needed
        if need_csv_file:
            write_function_callstack_csv(csv_output_file, ordered_results)
        
        # Write picked_traces.txt if needed - contains the callstack content for each matched function
        if need_picked_traces_file and ordered_results:
            write_picked_traces_file(picked_traces_file, ordered_results)
    
    # Generate selected.txt if needed (independent of matches file)
    if need_selected_file:
        # Get filter libraries for coverage calculation
        filter_libs = set()
        for func in matched_functions:
            lib = func.get('library_name', '')
            if lib:
                filter_libs.add(lib)
        
        # Use function-driven selection: iterate through matched functions CSV
        # and find first callstack for each function using in-memory index
        if hotspot_text_file and Path(hotspot_text_file).exists() and matched_csv_path.exists():
            print(f"\nSelecting callstacks by function coverage (in-memory index)...")
            
            selected_callstacks, covered_funcs, not_found_funcs = select_callstacks_by_function_coverage(
                matched_csv_path,
                hotspot_text_file
            )
            
            if selected_callstacks:
                # Write selected traces to file and compute coverage
                write_selected_traces_file(
                    selected_traces_file,
                    selected_callstacks,
                    matched_functions_csv_path=matched_csv_path,
                    filter_libraries=filter_libs if filter_libs else None
                )
            else:
                print("\nNo callstacks found - skipping selection")
        else:
            # Fallback to reservoir sampling if no text file available
            print(f"\nSelecting random traces using reservoir sampling (fallback)...")
            
            # Create iterator over callstacks
            if hotspot_text_file and Path(hotspot_text_file).exists():
                callstack_iter = iter_callstacks_from_text_file(hotspot_text_file)
            elif hotspot_json_data:
                callstack_iter = iter_callstacks_from_json(hotspot_json_data)
            else:
                callstack_iter = iter([])
            
            # Use reservoir sampling to select 5000 random callstacks
            selected_callstacks = reservoir_sample(callstack_iter, k=5000, seed=42)
            
            if selected_callstacks:
                print(f"  Selected {len(selected_callstacks)} callstacks using reservoir sampling")
                
                # Write selected traces to file and compute coverage
                write_selected_traces_file(
                    selected_traces_file,
                    selected_callstacks,
                    matched_functions_csv_path=matched_csv_path,
                    filter_libraries=filter_libs if filter_libs else None
                )
            else:
                print("\nNo callstacks found - skipping random selection")


def _process_function_chunk(
    chunk: List[Dict[str, Any]],
    func_impl_dict: Dict[str, Dict[str, Any]],
    chunk_id: int
) -> List[Dict[str, Any]]:
    """
    Process a chunk of matched functions to build output entries.
    
    This function is designed to be run in parallel threads.
    
    Args:
        chunk: List of matched function dictionaries to process
        func_impl_dict: Dictionary mapping function names to their implementations
        chunk_id: Identifier for this chunk (for logging)
        
    Returns:
        List of output dictionaries for this chunk
    """
    chunk_results = []
    
    for func in chunk:
        best_match = func.get("best_match", "")
        self_cost = func.get("self_cost", func.get("cost", 0))
        inclusive_cost = func.get("inclusive_cost", self_cost)
        
        if not best_match:
            continue
        
        # Get implementation from func_impl_dict
        func_data = func_impl_dict.get(best_match, {})
        implementation = func_data.get("implementation", [])
        
        chunk_results.append({
            "function_name": best_match,
            "implementation": implementation,
            "self_cost": self_cost,
            "inclusive_cost": inclusive_cost
        })
    
    return chunk_results


def write_functions_with_costs_json(
    out_dir: str,
    matched_functions: List[Dict[str, Any]],
    func_impl_dict: Dict[str, Dict[str, Any]],
    skip_existing: bool = True,
    num_threads: int = 4
) -> None:
    """
    Write a JSON file containing matched functions with their implementations and costs.
    
    Uses parallel processing with 4 threads, each processing 25% of functions.
    
    Output format (JSON array sorted by inclusive_cost in descending order):
    [
        {
            "function_name": "<function_name>",
            "implementation": [
                {"file_name": "<path>", "start": <line>, "end": <line>},
                ...
            ],
            "self_cost": <self_cost>,
            "inclusive_cost": <inclusive_cost>
        },
        ...
    ]
    
    Args:
        out_dir: Output directory path
        matched_functions: List of matched function dictionaries from match_and_print_hotspot_functions
        func_impl_dict: Dictionary mapping function names to their implementations
        skip_existing: If True, skip if file already exists
        num_threads: Number of threads to use for parallel processing (default: 4)
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Check if file exists and skip if needed
    output_file = out_path / "functions_with_costs.json"
    if skip_existing and output_file.exists():
        print(f"SKIPPING: {output_file} already exists")
        return
    
    total_functions = len(matched_functions)
    print(f"Processing {total_functions} functions with {num_threads} threads...")
    
    # Split matched_functions into chunks for parallel processing
    chunk_size = (total_functions + num_threads - 1) // num_threads  # Ceiling division
    chunks = []
    for i in range(num_threads):
        start_idx = i * chunk_size
        end_idx = min(start_idx + chunk_size, total_functions)
        if start_idx < total_functions:
            chunks.append(matched_functions[start_idx:end_idx])
    
    # Process chunks in parallel using ThreadPoolExecutor
    output_array = []
    
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        # Submit all chunks for processing
        future_to_chunk_id = {
            executor.submit(_process_function_chunk, chunk, func_impl_dict, i): i
            for i, chunk in enumerate(chunks)
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_chunk_id):
            chunk_id = future_to_chunk_id[future]
            try:
                chunk_results = future.result()
                output_array.extend(chunk_results)
                print(f"  Thread {chunk_id + 1}/{num_threads} completed: processed {len(chunk_results)} functions")
            except Exception as e:
                print(f"  Thread {chunk_id + 1}/{num_threads} failed with error: {e}")
    
    # Sort by inclusive_cost in descending order (reverse order)
    output_array.sort(key=lambda x: x["inclusive_cost"], reverse=True)
    
    # Write to JSON file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_array, f, indent=2)
    
    print(f"Wrote functions with costs to: {output_file} ({len(output_array)} functions)")


def write_aggregated_costs(
    out_dir: str,
    aggregated_output: Dict[str, Dict[str, Any]],
    skip_existing: bool = True
) -> None:
    """
    Write only the aggregated function costs text file when no merged functions file is provided.
    
    Creates:
    - aggregated_function_costs.txt
    
    If skip_existing is True, skips if file already exists.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Write aggregated function costs
    aggregated_file = out_path / "aggregated_function_costs.txt"
    if skip_existing and aggregated_file.exists():
        print(f"SKIPPING: {aggregated_file} already exists")
        return
    
    with open(aggregated_file, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("AGGREGATED FUNCTION COSTS (>= 0.0001%)\n")
        f.write("=" * 60 + "\n\n")
        
        for key, value in aggregated_output.items():
            f.write(f"{key}\n")
            self_cost = value.get('self_cost', value.get('cost', 0))
            inclusive_cost = value.get('inclusive_cost', self_cost)
            norm_self = value.get('normalized_self_cost', value.get('normalized_cost', 0.0))
            norm_incl = value.get('normalized_inclusive_cost', norm_self)
            f.write(f"  self_cost: {self_cost}, inclusive_cost: {inclusive_cost}\n")
            f.write(f"  normalized_self_cost: {norm_self:.4f}%, normalized_inclusive_cost: {norm_incl:.4f}%\n")
        
        f.write("\n" + "=" * 60 + "\n")
        f.write(f"Total unique functions: {len(aggregated_output)}\n")
    
    print(f"Wrote aggregated function costs to: {aggregated_file}")


def count_traces_in_json(input_file: str) -> int:
    """
    Count the number of traces (leaf nodes) in the original JSON file.
    
    For nested callstack format: counts leaf nodes in the callstack tree.
    For array format: counts the number of callstack groups.
    
    Args:
        input_file: Path to the input JSON file
        
    Returns:
        Number of traces in the JSON file
    """
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if isinstance(data, dict) and 'callstack' in data:
            # Nested callstack format - count leaf nodes
            trace_count = 0
            stack = deque([data.get("callstack", {})])
            while stack:
                node = stack.pop()
                children = node.get("children", [])
                if not children:
                    trace_count += 1
                else:
                    stack.extend(children)
            return trace_count
        elif isinstance(data, list):
            # Array format - count callstack groups
            return len(data)
        else:
            return 0
    except Exception as e:
        print(f"Warning: Could not count traces in JSON: {e}")
        return 0


def count_traces_with_filter_libraries(input_file: str, filter_libraries: Set[str]) -> Tuple[int, int]:
    """
    Count the number of traces that contain at least one frame from the filter libraries.
    
    For nested callstack format: traverses the tree and counts leaf paths that have
    at least one frame with ownerName in filter_libraries.
    
    Args:
        input_file: Path to the input JSON file
        filter_libraries: Set of library names to filter by
        
    Returns:
        Tuple of (filtered_trace_count, total_trace_count)
    """
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if not isinstance(data, dict) or 'callstack' not in data:
            return 0, 0
        
        callstack = data.get("callstack", {})
        
        total_traces = 0
        filtered_traces = 0
        
        # Stack for DFS traversal: (node, has_filter_lib_in_path)
        # has_filter_lib_in_path tracks if any ancestor had a filter library
        stack = deque([(callstack, callstack.get("ownerName", "") in filter_libraries)])
        
        while stack:
            node, has_filter_lib = stack.pop()
            
            # Check if current node has a filter library
            current_owner = node.get("ownerName", "")
            current_has_filter = has_filter_lib or (current_owner in filter_libraries)
            
            children = node.get("children", [])
            
            if not children:
                # This is a leaf node - count it
                total_traces += 1
                if current_has_filter:
                    filtered_traces += 1
            else:
                # Add children to stack
                for child in children:
                    stack.append((child, current_has_filter))
        
        return filtered_traces, total_traces
        
    except Exception as e:
        print(f"Warning: Could not count filtered traces in JSON: {e}")
        return 0, 0


def filter_traces_to_directory(
    input_file: str,
    output_dir: str,
    filter_libraries: Set[str],
    num_slices: int = 30,
    matched_functions: Optional[List[Dict[str, Any]]] = None,
    progress_interval: int = 10000,
    skip_existing: bool = True
) -> int:
    """
    Filter traces to only those containing frames from filter libraries and write to sliced text files.
    
    This function linearly scans through all traces in the original JSON and
    includes traces where one or more frames belong to the filter libraries.
    The filtered traces are split into multiple text files (slices) in the output directory.
    
    Memory-efficient implementation:
    - Uses streaming JSON parsing for large files
    - Writes output incrementally to avoid holding all traces in memory
    - Uses a deque-based tree traversal to minimize memory footprint
    
    Output format (text, similar to processed.txt):
    - Each callstack is separated by '====='
    - Each line has format: "percentage% (cost) library function_name (source_file)"
    
    Args:
        input_file: Path to the input hotspot JSON file
        output_dir: Path to output directory (filtered_traces/)
        filter_libraries: Set of library names to filter by
        num_slices: Number of slices to split traces into (default: 30)
        matched_functions: Optional list of matched functions (for additional filtering)
        progress_interval: Print progress every N traces (default: 10000)
        skip_existing: If True, skip if directory already exists with all slices
        
    Returns:
        Number of traces written to the output files
    """
    output_path = Path(output_dir)
    
    # Check if directory exists and has all slices
    if skip_existing and output_path.exists():
        existing_slices = list(output_path.glob("slice_*.txt"))
        if len(existing_slices) >= num_slices:
            print(f"SKIPPING: {output_dir} already exists with {len(existing_slices)} slices")
            # Count total traces from existing files
            total_traces = 0
            for slice_file in existing_slices:
                try:
                    with open(slice_file, 'r', encoding='utf-8') as f:
                        # Count separators + 1 for last callstack
                        separator_count = sum(1 for line in f if line.strip() == '=====')
                        total_traces += separator_count + 1 if separator_count > 0 else 0
                except Exception:
                    pass
            print(f"  Loaded approximately {total_traces} filtered traces from existing files")
            return total_traces
    
    print(f"\nFiltering traces containing frames from: {filter_libraries}")
    print(f"Output directory: {output_dir}")
    print(f"Number of slices: {num_slices}")
    
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON file for filtering: {e}")
        return 0
    
    if not isinstance(data, dict) or 'callstack' not in data:
        print("Error: Input file is not in nested callstack format")
        return 0
    
    callstack = data.get("callstack", {})
    
    # Build set of matched function names for faster lookup (if provided)
    matched_func_names: Set[str] = set()
    if matched_functions:
        for func in matched_functions:
            func_name = func.get('function_name', '')
            if func_name:
                matched_func_names.add(func_name)
        print(f"  Using {len(matched_func_names)} matched function names for filtering")
    
    # First pass: collect all filtered traces
    filtered_traces: List[List[Dict[str, Any]]] = []
    total_traces = 0
    filtered_count = 0
    
    # Stack for DFS traversal: (node, path_so_far)
    # path_so_far is a list of frame dictionaries
    stack = deque([(callstack, [])])
    
    while stack:
        node, path = stack.pop()
        
        # Build current frame info
        frame_info = {
            "frameName": node.get("frameName", "<unnamed>"),
            "value": node.get("value", 0),
            "ownerName": node.get("ownerName", ""),
            "sourcePath": node.get("sourcePath", "")
        }
        
        # Extend path with current frame
        current_path = path + [frame_info]
        
        children = node.get("children", [])
        
        if not children:
            # This is a leaf node - we have a complete trace
            total_traces += 1
            
            # Check if any frame in this trace belongs to filter libraries
            trace_has_filter_lib = False
            for frame in current_path:
                owner = frame.get("ownerName", "")
                if owner in filter_libraries:
                    trace_has_filter_lib = True
                    break
            
            if trace_has_filter_lib:
                # Store the trace frames
                filtered_traces.append(current_path)
                filtered_count += 1
            
            # Progress reporting
            if total_traces % progress_interval == 0:
                print(f"    Processed {total_traces} traces, filtered {filtered_count}...", flush=True)
        else:
            # Add children to stack for further traversal
            for child in reversed(children):
                stack.append((child, current_path))
    
    print(f"  Total traces scanned: {total_traces}")
    print(f"  Traces matching filter: {filtered_count}")
    
    if not filtered_traces:
        print("  No traces matched the filter criteria")
        return 0
    
    # Create output directory
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Calculate traces per slice
    traces_per_slice = (filtered_count + num_slices - 1) // num_slices  # Ceiling division
    
    print(f"  Writing {filtered_count} traces to {num_slices} slices (~{traces_per_slice} traces per slice)")
    
    # Write traces to sliced text files
    for slice_idx in range(num_slices):
        start_idx = slice_idx * traces_per_slice
        end_idx = min(start_idx + traces_per_slice, filtered_count)
        
        if start_idx >= filtered_count:
            break
        
        slice_file = output_path / f"slice_{slice_idx:03d}.txt"
        slice_traces = filtered_traces[start_idx:end_idx]
        
        try:
            with open(slice_file, 'w', encoding='utf-8') as f:
                for trace_idx, frames in enumerate(slice_traces):
                    if not frames:
                        continue
                    
                    # Get total cost for percentage calculation (first frame's value)
                    total_cost = frames[0].get("value", 1) if frames else 1
                    if total_cost == 0:
                        total_cost = 1
                    
                    # Write each frame as a line
                    for frame in frames:
                        frame_name = frame.get("frameName", "<unnamed>")
                        cost = frame.get("value", 0)
                        owner = frame.get("ownerName", "")
                        source = frame.get("sourcePath", "")
                        
                        # Skip invalid frames
                        if not frame_name or frame_name in ("???", "<root>", "<unnamed>"):
                            continue
                        
                        # Calculate percentage
                        percentage = int(round((cost / total_cost) * 100)) if total_cost > 0 else 0
                        
                        # Build line in processed.txt format
                        line = f"{percentage}% ({cost})"
                        if owner:
                            line += f" {owner}"
                        line += f" {frame_name}"
                        if source:
                            source_filename = Path(source).name
                            line += f" ({source_filename})"
                        
                        f.write(line + "\n")
                    
                    # Add separator between callstacks (except for the last one in this slice)
                    if trace_idx < len(slice_traces) - 1:
                        f.write("\n=====\n\n")
            
            print(f"    Wrote slice {slice_idx}: {len(slice_traces)} traces to {slice_file.name}")
        except Exception as e:
            print(f"Error writing slice {slice_idx}: {e}")
    
    # Write metadata file
    metadata_file = output_path / "metadata.json"
    try:
        metadata = {
            "processName": data.get("processName", "unknown"),
            "filter_libraries": list(filter_libraries),
            "total_traces_scanned": total_traces,
            "filtered_trace_count": filtered_count,
            "num_slices": num_slices,
            "traces_per_slice": traces_per_slice,
            "source_file": str(input_file)
        }
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        print(f"  Wrote metadata to: {metadata_file}")
    except Exception as e:
        print(f"Warning: Could not write metadata file: {e}")
    
    print(f"  Successfully wrote {filtered_count} filtered traces to {output_dir}")
    
    return filtered_count


def filter_traces_with_hotspot_functions(
    input_file: str,
    output_file: str,
    filter_libraries: Set[str],
    matched_functions: Optional[List[Dict[str, Any]]] = None,
    progress_interval: int = 10000
) -> int:
    """
    Filter traces to only those containing frames from filter libraries.
    
    This function linearly scans through all traces in the original JSON and
    includes traces where one or more frames belong to the filter libraries.
    
    Memory-efficient implementation:
    - Uses streaming JSON parsing for large files
    - Writes output incrementally to avoid holding all traces in memory
    - Uses a deque-based tree traversal to minimize memory footprint
    
    Args:
        input_file: Path to the input hotspot JSON file
        output_file: Path to output JSON file (process_filtered_traces.json)
        filter_libraries: Set of library names to filter by
        matched_functions: Optional list of matched functions (for additional filtering)
        progress_interval: Print progress every N traces (default: 10000)
        
    Returns:
        Number of traces written to the output file
    """
    print(f"\nFiltering traces containing frames from: {filter_libraries}")
    
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON file for filtering: {e}")
        return 0
    
    if not isinstance(data, dict) or 'callstack' not in data:
        print("Error: Input file is not in nested callstack format")
        return 0
    
    callstack = data.get("callstack", {})
    
    # Build set of matched function names for faster lookup (if provided)
    matched_func_names: Set[str] = set()
    if matched_functions:
        for func in matched_functions:
            func_name = func.get('function_name', '')
            if func_name:
                matched_func_names.add(func_name)
        print(f"  Using {len(matched_func_names)} matched function names for filtering")
    
    # Traverse the tree and collect leaf paths (traces) that match the filter
    # Use a generator-based approach to minimize memory usage
    filtered_traces: List[Dict[str, Any]] = []
    total_traces = 0
    filtered_count = 0
    
    # Stack for DFS traversal: (node, path_so_far)
    # path_so_far is a list of frame dictionaries
    stack = deque([(callstack, [])])
    
    while stack:
        node, path = stack.pop()
        
        # Build current frame info
        frame_info = {
            "frameName": node.get("frameName", "<unnamed>"),
            "value": node.get("value", 0),
            "ownerName": node.get("ownerName", ""),
            "sourcePath": node.get("sourcePath", "")
        }
        
        # Extend path with current frame
        current_path = path + [frame_info]
        
        children = node.get("children", [])
        
        if not children:
            # This is a leaf node - we have a complete trace
            total_traces += 1
            
            # Check if any frame in this trace belongs to filter libraries
            trace_has_filter_lib = False
            for frame in current_path:
                owner = frame.get("ownerName", "")
                if owner in filter_libraries:
                    trace_has_filter_lib = True
                    break
            
            if trace_has_filter_lib:
                # Store the trace in a compact format
                filtered_traces.append({
                    "frames": current_path,
                    "leaf_value": frame_info.get("value", 0)
                })
                filtered_count += 1
            
            # Progress reporting
            if total_traces % progress_interval == 0:
                print(f"    Processed {total_traces} traces, filtered {filtered_count}...", flush=True)
        else:
            # Add children to stack for further traversal
            for child in reversed(children):
                stack.append((child, current_path))
    
    print(f"  Total traces scanned: {total_traces}")
    print(f"  Traces matching filter: {filtered_count}")
    
    # Write filtered traces to output file
    if filtered_traces:
        try:
            # Create output structure
            output_data = {
                "processName": data.get("processName", "unknown"),
                "filter_libraries": list(filter_libraries),
                "total_traces_scanned": total_traces,
                "filtered_trace_count": filtered_count,
                "traces": filtered_traces
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2)
            
            print(f"  Wrote {filtered_count} filtered traces to: {output_file}")
        except Exception as e:
            print(f"Error writing filtered traces: {e}")
            return 0
    else:
        print("  No traces matched the filter criteria")
    
    return filtered_count


def generate_filtered_processed_text(
    filtered_traces_json_file: str,
    output_text_file: str,
    skip_existing: bool = True,
    progress_interval: int = 10000
) -> int:
    """
    Generate filtered_processed.txt from process_filtered_traces.json.
    
    The output format matches processed.txt:
    - Each callstack is separated by '====='
    - Each line in a callstack has format: "percentage% (cost) library function_name (source_file)"
    
    Args:
        filtered_traces_json_file: Path to process_filtered_traces.json
        output_text_file: Path to output filtered_processed.txt
        skip_existing: If True, skip if file already exists
        progress_interval: Print progress every N traces
        
    Returns:
        Number of callstacks written
    """
    output_path = Path(output_text_file)
    
    # Check if file exists and skip if needed
    if skip_existing and output_path.exists():
        print(f"SKIPPING generation: {output_text_file} already exists - reusing existing file")
        # Count lines to estimate callstack count
        try:
            with open(output_text_file, 'r', encoding='utf-8') as f:
                separator_count = sum(1 for line in f if line.strip() == '=====')
            # Number of callstacks is separator_count + 1 (last callstack has no trailing separator)
            callstack_count = separator_count + 1 if separator_count > 0 else 0
            print(f"  Existing file has approximately {callstack_count} callstacks")
            return callstack_count
        except Exception as e:
            print(f"  Warning: Could not count callstacks in existing file: {e}")
            return 0
    
    print(f"\nGenerating filtered_processed.txt from {filtered_traces_json_file}...")
    
    # Load filtered traces JSON
    try:
        with open(filtered_traces_json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading filtered traces JSON: {e}")
        return 0
    
    traces = data.get("traces", [])
    total_traces = len(traces)
    
    if total_traces == 0:
        print("  No traces found in filtered JSON")
        return 0
    
    print(f"  Processing {total_traces} filtered traces...")
    
    # Write to output file
    try:
        with open(output_text_file, 'w', encoding='utf-8') as f:
            for idx, trace in enumerate(traces):
                frames = trace.get("frames", [])
                
                if not frames:
                    continue
                
                # Get total cost for percentage calculation (first frame's value)
                total_cost = frames[0].get("value", 1) if frames else 1
                if total_cost == 0:
                    total_cost = 1
                
                # Write each frame as a line
                for frame in frames:
                    frame_name = frame.get("frameName", "<unnamed>")
                    cost = frame.get("value", 0)
                    owner = frame.get("ownerName", "")
                    source = frame.get("sourcePath", "")
                    
                    # Skip invalid frames
                    if not frame_name or frame_name in ("???", "<root>", "<unnamed>"):
                        continue
                    
                    # Calculate percentage
                    percentage = int(round((cost / total_cost) * 100)) if total_cost > 0 else 0
                    
                    # Build line in processed.txt format
                    line = f"{percentage}% ({cost})"
                    if owner:
                        line += f" {owner}"
                    line += f" {frame_name}"
                    if source:
                        source_filename = Path(source).name
                        line += f" ({source_filename})"
                    
                    f.write(line + "\n")
                
                # Add separator between callstacks (except for the last one)
                if idx < total_traces - 1:
                    f.write("\n=====\n\n")
                
                # Progress reporting
                if (idx + 1) % progress_interval == 0:
                    print(f"    Processed {idx + 1}/{total_traces} traces...", flush=True)
        
        print(f"  Wrote {total_traces} callstacks to: {output_text_file}")
        return total_traces
        
    except Exception as e:
        print(f"Error writing filtered_processed.txt: {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate hotspot function costs from JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Aggregate with default filter (locationd, CoreLocation, etc.)
  python dev/hotspot_function_aggregator.py -i input.json output.json
  
  # Aggregate only specific libraries
  python dev/hotspot_function_aggregator.py -i input.json output.json --filter CoreLocation LocationSupport
  
  # With repo path for file existence checking
  python dev/hotspot_function_aggregator.py -i input.json output.json --repo /path/to/repo
  
  # Combined: filter + repo + merged functions
  python dev/hotspot_function_aggregator.py -i input.json output.json --filter CoreLocation --repo /path/to/repo -f /path/to/merged_functions.json
  
  # With custom number of slices for filtered traces
  python dev/hotspot_function_aggregator.py -i input.json output.json --slices 50

Default filter libraries (if --filter not specified):
  - locationd
  - CoreLocation
  - CoreLocationProtobuf
  - CoreLocationTiles
  - CoreMotion
  - LocationSupport
        """
    )
    
    parser.add_argument(
        '-i', '--input',
        required=True,
        dest='input_file',
        help='Path to input hotspot JSON file (nested callstack or array format)'
    )
    
    parser.add_argument(
        'output_file',
        nargs='?',
        default=None,
        help='Path to output JSON file (default: filtered_frames.json in same directory as input)'
    )
    
    parser.add_argument(
        '--filter',
        nargs='*',
        dest='filter_libraries',
        help='List of library names to filter by (e.g., CoreLocation LocationSupport)'
    )
    
    parser.add_argument(
        '-f', '--functions',
        type=str,
        dest='merged_functions_file',
        help='Path to merged_functions.json file for function implementation lookup'
    )
    
    parser.add_argument(
        '--repo', '-r',
        type=str,
        dest='repo_path',
        help='Path to repository for file existence checking. If a file does not exist in the repo, its cost is assigned to the caller.'
    )
    
    parser.add_argument(
        '--no-skip-existing',
        action='store_true',
        default=False,
        dest='no_skip_existing',
        help='Force regeneration of all output files even if they exist (default: skip existing files)'
    )
    
    parser.add_argument(
        '--slices',
        type=int,
        default=30,
        dest='num_slices',
        help='Number of slices to split filtered traces into (default: 30)'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    
    parser.add_argument(
        '-c', '--clean',
        action='store_true',
        dest='clean',
        help='Clean up previously created output files (aggregated_function_costs.txt and filtered_frames.json) before running'
    )
    
    args = parser.parse_args()
    
    # Validate input file exists
    if not Path(args.input_file).exists():
        print(f"Error: Input file does not exist: {args.input_file}")
        return 1
    
    # Handle comma-separated filter libraries (support both space and comma separation)
    # e.g., --filter lib1,lib2 lib3 -> ['lib1', 'lib2', 'lib3']
    if args.filter_libraries:
        expanded_libs = []
        for lib in args.filter_libraries:
            # Split by comma and strip whitespace
            expanded_libs.extend([l.strip() for l in lib.split(',') if l.strip()])
        args.filter_libraries = expanded_libs
    
    # Default output file to filtered_frames.json in same directory as input
    if args.output_file is None:
        args.output_file = str(Path(args.input_file).parent / "filtered_frames.json")
        print(f"Using default output file: {args.output_file}")
    
    # Clean up previously created files if --clean is specified
    if args.clean:
        input_dir = Path(args.input_file).parent
        files_to_clean = [
            input_dir / "aggregated_function_costs.txt",
            input_dir / "filtered_frames.json",
        ]
        for file_path in files_to_clean:
            if file_path.exists():
                try:
                    file_path.unlink()
                    print(f"Cleaned up: {file_path}")
                except Exception as e:
                    print(f"Warning: Could not delete {file_path}: {e}")
    
    # Create output directory if needed
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Determine if user provided filter
    user_provided_filter = args.filter_libraries is not None
    
    # Validate repo path if provided
    if args.repo_path and not Path(args.repo_path).exists():
        print(f"Error: Repository path does not exist: {args.repo_path}")
        return 1
    
    # Validate merged functions file if provided
    if args.merged_functions_file and not Path(args.merged_functions_file).exists():
        print(f"Error: Merged functions file does not exist: {args.merged_functions_file}")
        return 1
    
    # Determine skip_existing (default True, unless --no-skip-existing is passed)
    skip_existing = not args.no_skip_existing
    
    # Perform aggregation
    result = aggregate_hotspot_functions(
        args.input_file,
        args.output_file,
        filter_libraries=args.filter_libraries,
        user_provided_filter=user_provided_filter,
        repo_path=args.repo_path,
        skip_existing=skip_existing
    )
    
    if isinstance(result, tuple):
        success, aggregated_output = result
    else:
        success = result
        aggregated_output = {}
    
    if success:
        print("\nAggregation completed successfully!")
        
        # Show file sizes
        input_size = Path(args.input_file).stat().st_size
        output_size = Path(args.output_file).stat().st_size
        print(f"Input file size: {input_size / 1024 / 1024:.2f} MB")
        print(f"Output file size: {output_size / 1024:.2f} KB")
        print(f"Number of traces in original JSON: {count_traces_in_json(args.input_file)}")
        
        # Process merged functions if provided
        if args.merged_functions_file:
            print(f"\nProcessing merged functions from: {args.merged_functions_file}")
            print(f"Number of traces in original JSON: {count_traces_in_json(args.input_file)}")
            
            # Get filter libraries set
            filter_libs = set(args.filter_libraries) if args.filter_libraries else set(DEFAULT_FILTER_LIBRARIES)
            
            # Count traces with filter libraries
            filtered_trace_count, total_trace_count = count_traces_with_filter_libraries(args.input_file, filter_libs)
            print(f"Number of traces with filter libraries: {filtered_trace_count} / {total_trace_count}")
            
            # Generate filtered traces EARLY - right after counting
            # Determine output path for filtered traces directory
            # Always use the same directory as the input hotspots file
            filtered_traces_dir = Path(args.input_file).parent / "filtered_traces"
            
            # Generate filtered traces to directory with sliced text files
            filtered_count = filter_traces_to_directory(
                args.input_file,
                str(filtered_traces_dir),
                filter_libs,
                num_slices=args.num_slices,
                matched_functions=None,  # No matched functions yet at this point
                skip_existing=skip_existing
            )
            
            # Check if processed.txt exists in the same directory as the input file
            # If it exists, use it for callstack lookups instead of generating filtered traces
            processed_txt_file = Path(args.input_file).parent / "processed.txt"
            hotspot_text_file = None
            
            if processed_txt_file.exists():
                print(f"Found existing processed.txt file: {processed_txt_file}")
                hotspot_text_file = str(processed_txt_file)
            else:
                # Use the first slice file as the hotspot text file for subsequent processing
                # (or concatenate all slices if needed for callstack lookups)
                first_slice_file = filtered_traces_dir / "slice_000.txt"
                if first_slice_file.exists():
                    print(f"Using filtered_traces directory for callstack lookups ({filtered_count} callstacks in {args.num_slices} slices)")
                    hotspot_text_file = str(first_slice_file)
            
            # Build function implementation dictionary and file-to-functions index
            func_impl_dict, file_to_functions = build_function_implementation_dict(
                args.merged_functions_file,
                aggregated_output,
                filter_libs
            )
            
            # Build reverse index of keyword to function names
            keyword_index = build_keyword_to_functions_index(func_impl_dict)
            
            # Print summary
            print_function_keywords_dict(func_impl_dict, keyword_index)
            print(f"File to functions index built with {len(file_to_functions)} files")
            
            # For callstack lookup, we use the text files (processed.txt or filtered_traces directory)
            # For JSON-based lookup (fallback), we don't use the filtered traces JSON anymore
            hotspot_json_data = None
            if not hotspot_text_file:
                # Fallback to original input JSON if no text file available
                try:
                    with open(args.input_file, 'r', encoding='utf-8') as f:
                        hotspot_json_data = json.load(f)
                    print(f"Loaded original hotspot JSON for callstack lookup (fallback)")
                except Exception as e:
                    print(f"Warning: Could not load original hotspot JSON: {e}")
            
            # Always use the same directory as the input hotspots file for output files
            # (ignoring --out-dir for these files)
            hotspots_out_dir = str(Path(args.input_file).parent)
            print(f"Output files will be written to: {hotspots_out_dir}")
            
            # Match and print hotspot functions with their best matches
            # Pass filtered_traces_dir to enable slice-based search with random starting slice
            matched_functions, unmatched_functions = match_and_print_hotspot_functions(
                aggregated_output,
                func_impl_dict,
                file_to_functions,
                filter_libs,
                out_dir=hotspots_out_dir,
                hotspot_text_file=hotspot_text_file,
                hotspot_json_data=hotspot_json_data,
                skip_existing=skip_existing,
                filtered_traces_dir=str(filtered_traces_dir) if filtered_traces_dir.exists() else None,
                num_slices=args.num_slices
            )
        else:
            # Write only aggregated function costs if no merged functions file provided
            # Use the same directory as the input hotspots file
            hotspots_out_dir = str(Path(args.input_file).parent)
            write_aggregated_costs(hotspots_out_dir, aggregated_output, skip_existing=skip_existing)
        
        return 0
    else:
        print("\nAggregation failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
import os
import json
import logging
from typing import Dict, List, Any, Set, Optional

from ...utils.file_util import (
        extract_function_context,
        load_ast_tracking_data,
        read_json_file,
        get_artifacts_temp_file_path,
        get_artifacts_temp_subdir_path
    )
from ...utils.filtered_file_finder import FilteredFileFinder


# Get logger for this module
logger = logging.getLogger(__name__)

# module-scope cache: map the specific lookup dict id -> { base_name: [original_keys...] }
_FUNCTION_BASE_INDEX: Dict[int, Dict[str, list[str]]] = {}

def _base_name(name: str) -> str:
    # match existing logic: split at '(' and take left side
    i = name.find('(')
    return name if i == -1 else name[:i]

def _ensure_base_index(function_lookup: Dict[str, Dict]) -> Dict[str, list[str]]:
    lookup_id = id(function_lookup)
    idx = _FUNCTION_BASE_INDEX.get(lookup_id)
    if idx is None:
        # build once per dict instance
        idx = {}
        for k in function_lookup.keys():
            b = _base_name(k)
            lst = idx.get(b)
            if lst is None:
                idx[b] = [k]
            else:
                lst.append(k)  # keep all originals that share the same base
        _FUNCTION_BASE_INDEX[lookup_id] = idx
    return idx

# Constants
CALL_GRAPH_KEY = 'call_graph'
CONTEXT_KEY = 'context'
FUNCTION_CONTEXT_KEY = 'function_context'
FUNCTIONS_INVOKED_KEY = 'functions_invoked'
INVOKING_KEY = 'invoking'
FILE_KEY = 'file'
FUNCTION_KEY = 'function'
START_LINE_NUMBER_KEY = 'start'
END_LINE_NUMBER_KEY = 'end'
FUNCTION_CONTEXT_CONTENT_KEY = 'functionContext'
FILE_CONTEXT_KEY = 'fileContext'
CLASS_CONTEXT_KEY = 'classContext'
DATA_TYPES_USED_KEY = 'data_types_used'
FUNCTIONS_KEY = 'functions'
ALL_KEY = 'all'


class CallGraphUtilities:
    """
    Shared utilities for call graph processing that can be reused across different modules.
    Contains common functionality for nested invocation resolution, function matching, and context handling.
    """

    def __init__(self, repo_path: str, override_base_dir: str = None):
        self.repo_path = repo_path
        self.override_base_dir = override_base_dir

        # Class registry cache for data type lookups
        self._class_registry_cache = None
        self._class_registry_loaded = False

    @staticmethod
    def has_null_context(obj: Any) -> bool:
        """Check if an object or its nested structure contains null values in context.
        Skip entries where file name and line numbers cannot be deduced."""
        if isinstance(obj, dict):
            # Check new schema function_context structure
            context = obj.get(FUNCTION_CONTEXT_KEY)

            if context:
                # Skip if file name or line numbers are missing/null
                if (context.get(FILE_KEY) is None or
                    context.get(START_LINE_NUMBER_KEY) is None or
                    context.get(END_LINE_NUMBER_KEY) is None):
                    return True
            else:
                # If no function_context at all, consider it null
                return True

            # New format doesn't have nested context, so no need to check functions_invoked
        elif isinstance(obj, list):
            for item in obj:
                if CallGraphUtilities.has_null_context(item):
                    return True

        return False

    @staticmethod
    def remove_null_contexts(invoking_list: List[Dict]) -> List[Dict]:
        """This method is no longer needed with the new functions_invoked format."""
        # functions_invoked is just a list of function names, no processing needed
        return invoking_list

    @staticmethod
    def find_matching_function(function_name: str, function_lookup: Dict[str, Dict]) -> Optional[str]:
        """Find a matching function in the lookup table using exact or base-name match."""
        # 1) exact
        if function_name in function_lookup:
            return function_name

        # 2) base-name (computed once per lookup dict)
        base = _base_name(function_name)
        idx = _ensure_base_index(function_lookup)
        hits = idx.get(base)
        if hits:
            # preserve prior behavior by returning the first matching original key
            return hits[0]
        return None

    def resolve_nested_invocations(self, invoking_list: List[Dict], _function_lookup: Dict[str, Dict] = None,
                                 _visited_functions: Set[str] = None) -> List[Dict]:
        """No longer needed with new functions_invoked format - just return as-is."""
        return invoking_list

    def add_function_contexts_to_invoking(self, invoking_list: List[Dict]) -> List[Dict]:
        """No longer needed with new functions_invoked format - just return as-is."""
        return invoking_list

    def _load_class_registry(self) -> Optional[List[Dict[str, Any]]]:
        """
        Load class registry from merged class definitions file.

        Returns:
            List of class entries or None if not found
        """
        if self._class_registry_loaded:
            return self._class_registry_cache

        # Look in code_insights subdirectory for the actual generated files
        code_insights_dir = get_artifacts_temp_subdir_path(self.repo_path, "code_insights", self.override_base_dir)
        defined_classes_json_path = os.path.join(code_insights_dir, "merged_defined_classes.json")

        if not os.path.exists(defined_classes_json_path):
            logger.debug(f"Class registry path not found: {defined_classes_json_path}")
            return

        try:
            with open(defined_classes_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # Handle new schema with data_type_to_location key
                self._class_registry_cache = data['data_type_to_location_and_checksum']

                self._class_registry_loaded = True
                logger.info(f"Loaded class registry from {defined_classes_json_path} with {len(self._class_registry_cache)} entries")
                return self._class_registry_cache
        except Exception as e:
            logger.warning(f"Failed to load class registry from {defined_classes_json_path}: {e}")
            return

        self._class_registry_loaded = True
        self._class_registry_cache = []
        logger.warning("No class registry file found. Class context will have limited functionality.")
        return self._class_registry_cache

    def _find_class_files(self, class_name: str) -> List[Dict[str, Any]]:
        """
        Find files associated with a class name using the class registry.

        Args:
            class_name: Name of the class to find

        Returns:
            List of file entries with line numbers for the class
        """
        registry = self._load_class_registry()
        if not registry:
            return []

        # Search for exact match first
        for entry in registry:
            if entry.get("data_type_name") == class_name:
                return entry.get("files", [])

        # Search for partial matches (case-insensitive)
        class_name_lower = class_name.lower()
        matches = []
        for entry in registry:
            entry_name = entry.get("data_type_name", "")
            if class_name_lower in entry_name.lower():
                matches.extend(entry.get("files", []))

        return matches

    def add_invoked_class_contexts(self, invoking_list: List[Dict]) -> List[Dict]:
        """No longer needed with new functions_invoked format - just return as-is."""
        return invoking_list

    @staticmethod
    def load_call_graph_data(call_graph_file: str) -> Dict[str, Any]:
        """Load and parse call graph JSON file, returning a function lookup map."""
        logger.info(f"Loading call graph data from {call_graph_file}...")
        call_graph_data = read_json_file(call_graph_file)
        if call_graph_data is None:
            logger.error(f"Failed to load call graph from {call_graph_file}")
            return {}

        # Build a map of function names to their call graph entries
        function_map = {}

        if "call_graph" in call_graph_data:
            call_graph = call_graph_data["call_graph"]

            # Process file-grouped format
            for file_entry in call_graph:
                functions = file_entry.get("functions", [])
                for func_entry in functions:
                    function_name = func_entry.get("function", "")
                    if function_name:
                        function_map[function_name] = func_entry

        logger.info(f"Loaded {len(function_map)} functions from call graph")
        return function_map


class ASTCallGraphParser:
    def __init__(self, tracking_file: str, output_dir: str, repo_path: str, override_base_dir: str = None,
                 include_directories: List[str] = None, exclude_directories: List[str] = None,
                 exclude_files: List[str] = None, file_filter: List[str] = None):
        # External JSON file for tracking processed files and functions
        # If tracking_file is just a filename, use hindsight directory
        if os.path.dirname(tracking_file) == '':
            self.tracking_file = get_artifacts_temp_file_path(repo_path, tracking_file, override_base_dir)
        else:
            self.tracking_file = tracking_file

        self.output_dir = output_dir
        self.repo_path = repo_path
        self.override_base_dir = override_base_dir

        # Store filtering configuration for LLM analysis filtering
        self.include_directories = include_directories or []
        self.exclude_directories = exclude_directories or []
        self.exclude_files = exclude_files or []
        self.file_filter = file_filter or []

        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

        # Load existing tracking data or initialize empty
        self.processed_files: Dict[str, List[str]] = load_ast_tracking_data(self.tracking_file)
        self.file_line_counts: Dict[str, int] = {}

        # Function lookup table for cross-referencing nested invocations
        self.function_lookup: Dict[str, Dict] = {}

        # Initialize call graph utilities
        self.call_graph_utils = CallGraphUtilities(repo_path, override_base_dir)

    @staticmethod
    def _is_internal_function(primary_file: str, invoking_function: Dict) -> bool:
        """Determine if a function is internal (same file as primary) or external."""
        # Check new context structure
        file_path = None
        if FUNCTION_CONTEXT_KEY in invoking_function and isinstance(invoking_function[FUNCTION_CONTEXT_KEY], dict):
            file_path = invoking_function[FUNCTION_CONTEXT_KEY].get(FILE_KEY)

        return file_path == primary_file if file_path else False

    def _filter_external_functions(self, primary_file: str, invoking_list: List[Dict]) -> List[Dict]:
        """Filter out internal functions, keeping only external ones with valid context."""
        external_functions = []
        for func in invoking_list:
            # First check if this function has valid context
            if not self._has_valid_context(func):
                continue  # Skip functions without valid context

            if not self._is_internal_function(primary_file, func):
                # This is an external function with valid context - keep it
                cleaned_func = func.copy()
                if CONTEXT_KEY in cleaned_func and cleaned_func[CONTEXT_KEY]:
                    context = cleaned_func[CONTEXT_KEY].copy()

                    # Add function context if it doesn't exist
                    if FUNCTION_CONTEXT_KEY not in context and context.get(FILE_KEY):
                        temp_entry = {CONTEXT_KEY: func[CONTEXT_KEY]}  # Use original context for extraction
                        function_context = extract_function_context(temp_entry, self.repo_path)
                        context[FUNCTION_CONTEXT_KEY] = function_context

                    cleaned_func[CONTEXT_KEY] = context

                # Recursively filter nested invoking arrays
                if INVOKING_KEY in cleaned_func and isinstance(cleaned_func[INVOKING_KEY], list):
                    nested_external = self._filter_external_functions(primary_file, cleaned_func[INVOKING_KEY])
                    if nested_external:  # Only add if not empty
                        cleaned_func[INVOKING_KEY] = nested_external
                    else:
                        cleaned_func.pop(INVOKING_KEY, None)  # Remove empty invoking list

                external_functions.append(cleaned_func)
            else:
                # This is an internal function - don't keep it, but check its nested functions
                if INVOKING_KEY in func and isinstance(func[INVOKING_KEY], list):
                    nested_external = self._filter_external_functions(primary_file, func[INVOKING_KEY])
                    # Add any external functions found in the nested invoking list
                    external_functions.extend(nested_external)

        return external_functions

    def _has_valid_context(self, func: Dict) -> bool:
        """Check if a function has valid context with file path and line numbers."""
        # Check new context structure
        context = None
        if FUNCTION_CONTEXT_KEY in func and isinstance(func[FUNCTION_CONTEXT_KEY], dict):
            context = func[FUNCTION_CONTEXT_KEY]
        else:
            return False

        # Check for required fields
        file_path = context.get(FILE_KEY)
        start_line = context.get(START_LINE_NUMBER_KEY)
        end_line = context.get(END_LINE_NUMBER_KEY)

        # All three must be present and not None
        return (file_path is not None and
                start_line is not None and
                end_line is not None)

    def _should_analyze_function(self, file_path: str) -> bool:
        """
        Check if a function should be analyzed based on LLM analysis filtering logic.
        This implements the filtering precedence: file-filter > include_directories > exclude_directories
        """
        if not file_path:
            return False

        # Step 1: Check if --file-filter is provided. If so, use only file filter logic
        if self.file_filter:
            return self._should_analyze_function_by_file_filter(file_path)

        # Step 2: Apply include_directories, exclude_directories, and exclude_files logic
        return self._should_analyze_function_by_directory_filters(file_path)

    def _should_analyze_function_by_file_filter(self, file_path: str) -> bool:
        """
        Check if a function should be analyzed based on the --file-filter argument.
        """
        # Normalize file paths for comparison (remove leading ./ and handle relative paths)
        normalized_file_path = file_path.lstrip('./')

        # Check if the file is in our filter list
        for filter_file in self.file_filter:
            normalized_filter_file = filter_file.lstrip('./')
            if normalized_file_path == normalized_filter_file or normalized_file_path.endswith('/' + normalized_filter_file):
                return True

        return False

    def _should_analyze_function_by_directory_filters(self, file_path: str) -> bool:
        """
        Check if a function should be analyzed based on include_directories, exclude_directories, and exclude_files.
        Uses the unified filtering method from FilteredFileFinder to ensure consistent behavior.
        """


        # Normalize file path
        normalized_file_path = file_path.lstrip('./')

        # Use the unified filtering method from FilteredFileFinder
        result = FilteredFileFinder.should_analyze_by_directory_filters(
            normalized_file_path,
            self.include_directories,
            self.exclude_directories,
            self.exclude_files
        )

        return result

    def get_processing_summary(self) -> Dict:
        """Get a summary of processed files and functions."""
        summary = {}
        for file_path, functions in self.processed_files.items():
            if ALL_KEY in functions:
                summary[file_path] = {FUNCTIONS_KEY: [ALL_KEY]}
            else:
                summary[file_path] = {FUNCTIONS_KEY: functions}
        return summary

    def clear_tracking_data(self):
        """Clear all tracking data (useful for fresh starts)."""
        self.processed_files = {}
        self.file_line_counts = {}
        if os.path.exists(self.tracking_file):
            os.remove(self.tracking_file)
        logger.info(f"Cleared tracking data and removed {self.tracking_file}")

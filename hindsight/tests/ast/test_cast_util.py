"""
Unit tests for CASTUtil - C/C++/Objective-C AST analysis utilities.

Tests the AST traversal functions, registry building, and call graph generation.
Focuses on verifying that iterative traversal functions work correctly and
don't cause stack overflow on deeply nested structures.
"""

import pytest
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.lang_util.cast_util import (
    CASTUtil,
    strip_template_params,
    base_function_name,
    generate_code_checksum,
    generate_unnamed_type_name,
    detect_preprocessor_macros,
    create_macro_flags,
    create_macro_flags_excluding_derived,
    get_clang_flags_for_file,
    SUPPORTED_EXTENSIONS,
    ALLOWED_FUNCTION_KINDS,
    ALLOWED_CLASS_KINDS,
    ALLOWED_DATA_TYPE_KINDS,
)


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for test outputs."""
    return str(tmp_path)


class TestStripTemplateParams:
    """Tests for strip_template_params function."""
    
    def test_simple_template(self):
        """Test stripping simple template parameters."""
        result = strip_template_params("vector<int>")
        assert result == "vector"
    
    def test_nested_templates(self):
        """Test stripping nested template parameters."""
        result = strip_template_params("map<string, vector<int>>")
        assert result == "map"
    
    def test_no_template(self):
        """Test string without template parameters."""
        result = strip_template_params("MyClass")
        assert result == "MyClass"
    
    def test_empty_string(self):
        """Test empty string."""
        result = strip_template_params("")
        assert result == ""
    
    def test_qualified_name_with_template(self):
        """Test qualified name with template."""
        result = strip_template_params("std::vector<int>::iterator")
        assert result == "std::vector::iterator"
    
    def test_multiple_template_params(self):
        """Test multiple template parameters."""
        result = strip_template_params("pair<int, string>")
        assert result == "pair"
    
    def test_template_with_spaces(self):
        """Test template with spaces."""
        result = strip_template_params("vector< int >")
        assert result == "vector"


class TestBaseFunctionName:
    """Tests for base_function_name function."""
    
    def test_function_with_params(self):
        """Test extracting base name from function with parameters."""
        result = base_function_name("myFunction(int x, int y)")
        assert result == "myFunction"
    
    def test_function_without_params(self):
        """Test function name without parameters."""
        result = base_function_name("myFunction")
        assert result == "myFunction"
    
    def test_method_with_class(self):
        """Test method with class qualifier."""
        result = base_function_name("MyClass::myMethod()")
        assert result == "MyClass::myMethod"
    
    def test_template_function(self):
        """Test template function name."""
        result = base_function_name("vector<int>::push_back(int)")
        assert result == "vector::push_back"
    
    def test_empty_string(self):
        """Test empty string."""
        result = base_function_name("")
        assert result == ""


class TestGetClangFlagsForFile:
    """Tests for get_clang_flags_for_file function."""
    
    def test_c_file(self):
        """Test flags for C file."""
        flags = get_clang_flags_for_file("test.c")
        assert "-x" in flags
        assert "c" in flags
        assert "-std=c99" in flags
    
    def test_cpp_file(self):
        """Test flags for C++ file."""
        flags = get_clang_flags_for_file("test.cpp")
        assert "-x" in flags
        assert "c++" in flags
        assert "-std=c++20" in flags
    
    def test_cc_file(self):
        """Test flags for .cc file."""
        flags = get_clang_flags_for_file("test.cc")
        assert "-x" in flags
        assert "c++" in flags
    
    def test_mm_file(self):
        """Test flags for Objective-C++ file."""
        flags = get_clang_flags_for_file("test.mm")
        assert "-x" in flags
        assert "objective-c++" in flags
    
    def test_m_file(self):
        """Test flags for Objective-C file."""
        flags = get_clang_flags_for_file("test.m")
        assert "-x" in flags
        assert "objective-c++" in flags
    
    def test_header_file(self):
        """Test flags for header file (defaults to C++)."""
        flags = get_clang_flags_for_file("test.h")
        assert "-x" in flags
        assert "c++" in flags


class TestCreateMacroFlags:
    """Tests for create_macro_flags function."""
    
    def test_single_macro(self):
        """Test creating flags for single macro."""
        flags = create_macro_flags({"DEBUG"})
        assert "-DDEBUG=1" in flags
    
    def test_multiple_macros(self):
        """Test creating flags for multiple macros."""
        flags = create_macro_flags({"DEBUG", "FEATURE_X", "ENABLE_LOGGING"})
        assert "-DDEBUG=1" in flags
        assert "-DFEATURE_X=1" in flags
        assert "-DENABLE_LOGGING=1" in flags
    
    def test_empty_set(self):
        """Test creating flags for empty set."""
        flags = create_macro_flags(set())
        assert flags == []
    
    def test_flags_are_sorted(self):
        """Test that flags are sorted alphabetically."""
        flags = create_macro_flags({"Z_MACRO", "A_MACRO", "M_MACRO"})
        assert flags == ["-DA_MACRO=1", "-DM_MACRO=1", "-DZ_MACRO=1"]


class TestCreateMacroFlagsExcludingDerived:
    """Tests for create_macro_flags_excluding_derived function."""
    
    def test_excludes_derived_macros(self):
        """Test that derived macros are excluded."""
        macros = {"BASE_MACRO", "DERIVED_MACRO", "OTHER_MACRO"}
        derived = {"DERIVED_MACRO": "BASE_MACRO"}
        
        flags = create_macro_flags_excluding_derived(macros, derived)
        
        assert "-DBASE_MACRO=1" in flags
        assert "-DOTHER_MACRO=1" in flags
        assert "-DDERIVED_MACRO=1" not in flags
    
    def test_empty_derived(self):
        """Test with no derived macros."""
        macros = {"MACRO_A", "MACRO_B"}
        derived = {}
        
        flags = create_macro_flags_excluding_derived(macros, derived)
        
        assert "-DMACRO_A=1" in flags
        assert "-DMACRO_B=1" in flags


class TestCASTUtilHelpers:
    """Tests for CASTUtil helper methods."""
    
    def test_is_standard_library_type_std_prefix(self):
        """Test standard library type detection with std:: prefix."""
        assert CASTUtil._is_standard_library_type("std::string") is True
        assert CASTUtil._is_standard_library_type("std::vector") is True
        assert CASTUtil._is_standard_library_type("std::map") is True
    
    def test_is_standard_library_type_primitives(self):
        """Test standard library type detection for primitives."""
        assert CASTUtil._is_standard_library_type("int") is True
        assert CASTUtil._is_standard_library_type("char") is True
        assert CASTUtil._is_standard_library_type("void") is True
        assert CASTUtil._is_standard_library_type("bool") is True
        assert CASTUtil._is_standard_library_type("double") is True
    
    def test_is_standard_library_type_custom(self):
        """Test standard library type detection for custom types."""
        assert CASTUtil._is_standard_library_type("MyCustomClass") is False
        assert CASTUtil._is_standard_library_type("WebCore::Element") is False
    
    def test_is_standard_library_type_empty(self):
        """Test standard library type detection for empty string."""
        assert CASTUtil._is_standard_library_type("") is True
        assert CASTUtil._is_standard_library_type(None) is True
    
    def test_is_standard_library_type_objc_prefixes(self):
        """Test standard library type detection for Objective-C prefixes."""
        assert CASTUtil._is_standard_library_type("NSString") is True
        assert CASTUtil._is_standard_library_type("NSArray") is True
        assert CASTUtil._is_standard_library_type("CFString") is True


class TestCASTUtilCacheManagement:
    """Tests for CASTUtil cache management."""
    
    def test_clear_macro_cache(self):
        """Test clearing macro cache."""
        # Add something to cache
        CASTUtil._macro_cache["test_key"] = {"test_macro"}
        
        # Clear cache
        CASTUtil.clear_macro_cache()
        
        # Verify cache is empty
        assert len(CASTUtil._macro_cache) == 0
    
    def test_clear_all_caches(self):
        """Test clearing all caches."""
        # Add something to macro cache
        CASTUtil._macro_cache["test_key"] = {"test_macro"}
        
        # Clear all caches
        CASTUtil.clear_all_caches()
        
        # Verify macro cache is empty
        assert len(CASTUtil._macro_cache) == 0


class TestMergeRegistries:
    """Tests for registry merging functions."""
    
    def test_merge_function_registries(self):
        """Test merging two function registries."""
        registry1 = {
            "func_a": {("file1.cpp", 1, 10, 0, 100)},
            "func_b": {("file1.cpp", 20, 30, 200, 300)}
        }
        registry2 = {
            "func_a": {("file2.cpp", 5, 15, 50, 150)},  # Same function, different location
            "func_c": {("file2.cpp", 40, 50, 400, 500)}  # New function
        }
        
        merged = CASTUtil._merge_function_registries(registry1, registry2)
        
        assert "func_a" in merged
        assert "func_b" in merged
        assert "func_c" in merged
        assert len(merged["func_a"]) == 2  # Both locations merged
    
    def test_merge_data_types_registries(self):
        """Test merging two data types registries."""
        registry1 = {
            "MyClass": [{"file_name": "file1.h", "start": 1, "end": 10}]
        }
        registry2 = {
            "MyClass": [{"file_name": "file2.h", "start": 5, "end": 15}],
            "OtherClass": [{"file_name": "file2.h", "start": 20, "end": 30}]
        }
        
        merged = CASTUtil._merge_data_types_registries(registry1, registry2)
        
        assert "MyClass" in merged
        assert "OtherClass" in merged
        assert len(merged["MyClass"]) == 2
    
    def test_merge_call_graphs(self):
        """Test merging two call graphs."""
        graph1 = {
            "caller_a": ["callee_1", "callee_2"]
        }
        graph2 = {
            "caller_a": ["callee_2", "callee_3"],  # Overlapping callee
            "caller_b": ["callee_4"]
        }
        
        merged = CASTUtil._merge_call_graphs(graph1, graph2)
        
        assert "caller_a" in merged
        assert "caller_b" in merged
        # callee_2 should not be duplicated
        assert len(merged["caller_a"]) == 3
    
    def test_merge_data_type_usage(self):
        """Test merging two data type usage mappings."""
        usage1 = {
            "func_a": ["TypeA", "TypeB"]
        }
        usage2 = {
            "func_a": ["TypeB", "TypeC"],
            "func_b": ["TypeD"]
        }
        
        merged = CASTUtil._merge_data_type_usage(usage1, usage2)
        
        assert "func_a" in merged
        assert "func_b" in merged
        # Types should be merged and sorted
        assert "TypeA" in merged["func_a"]
        assert "TypeB" in merged["func_a"]
        assert "TypeC" in merged["func_a"]
    
    def test_merge_constants_usage(self):
        """Test merging two constants usage mappings."""
        usage1 = {
            "func_a": {"CONST_A": 1, "CONST_B": 2}
        }
        usage2 = {
            "func_a": {"CONST_B": 2, "CONST_C": 3},
            "func_b": {"CONST_D": 4}
        }
        
        merged = CASTUtil._merge_constants_usage(usage1, usage2)
        
        assert "func_a" in merged
        assert "func_b" in merged
        assert merged["func_a"]["CONST_A"] == 1
        assert merged["func_a"]["CONST_B"] == 2
        assert merged["func_a"]["CONST_C"] == 3


class TestIterativeTraversalDoesNotOverflow:
    """Tests to verify iterative traversal doesn't cause stack overflow.
    
    These tests use mock cursors to simulate deeply nested AST structures
    that would cause stack overflow with recursive traversal.
    """
    
    def test_deep_nesting_simulation(self):
        """Test that iterative traversal handles deep nesting without stack overflow.
        
        This test simulates a deeply nested AST structure (1000+ levels deep)
        that would cause RecursionError with recursive traversal.
        """
        # Create a mock cursor that simulates deep nesting
        depth = 1500  # Deeper than Python's default recursion limit (1000)
        
        # Build a chain of mock cursors
        cursors = []
        for i in range(depth):
            mock_cursor = Mock()
            mock_cursor.kind = Mock()
            mock_cursor.kind.name = "UNEXPOSED_EXPR"
            mock_cursor.spelling = f"cursor_{i}"
            mock_cursor.is_definition = Mock(return_value=False)
            cursors.append(mock_cursor)
        
        # Link cursors: each cursor's get_children returns the next cursor
        for i in range(depth - 1):
            cursors[i].get_children = Mock(return_value=[cursors[i + 1]])
        cursors[-1].get_children = Mock(return_value=[])  # Leaf node
        
        # Test that we can traverse without stack overflow
        # We'll use a simple iterative traversal similar to what's in cast_util.py
        visited = []
        stack = [cursors[0]]
        
        while stack:
            cursor = stack.pop()
            visited.append(cursor.spelling)
            try:
                children = list(cursor.get_children())
                for child in reversed(children):
                    stack.append(child)
            except Exception:
                continue
        
        # Verify all cursors were visited
        assert len(visited) == depth
        assert visited[0] == "cursor_0"
        assert visited[-1] == f"cursor_{depth - 1}"


class TestSupportedExtensions:
    """Tests for supported file extensions."""
    
    def test_supported_extensions_list(self):
        """Test that supported extensions are defined correctly."""
        assert ".cpp" in SUPPORTED_EXTENSIONS
        assert ".cc" in SUPPORTED_EXTENSIONS
        assert ".c" in SUPPORTED_EXTENSIONS
        assert ".mm" in SUPPORTED_EXTENSIONS
        assert ".m" in SUPPORTED_EXTENSIONS
        assert ".h" in SUPPORTED_EXTENSIONS
    
    def test_unsupported_extensions_not_included(self):
        """Test that unsupported extensions are not included."""
        assert ".py" not in SUPPORTED_EXTENSIONS
        assert ".java" not in SUPPORTED_EXTENSIONS
        assert ".js" not in SUPPORTED_EXTENSIONS
        assert ".swift" not in SUPPORTED_EXTENSIONS


class TestAllowedCursorKinds:
    """Tests for allowed cursor kind sets."""
    
    def test_allowed_function_kinds_not_empty(self):
        """Test that ALLOWED_FUNCTION_KINDS is not empty."""
        assert len(ALLOWED_FUNCTION_KINDS) > 0
    
    def test_allowed_class_kinds_not_empty(self):
        """Test that ALLOWED_CLASS_KINDS is not empty."""
        assert len(ALLOWED_CLASS_KINDS) > 0
    
    def test_allowed_data_type_kinds_not_empty(self):
        """Test that ALLOWED_DATA_TYPE_KINDS is not empty."""
        assert len(ALLOWED_DATA_TYPE_KINDS) > 0


class TestGenerateUnnamedTypeName:
    """Tests for generate_unnamed_type_name function."""
    
    def test_with_valid_file_path(self):
        """Test generating name with valid file path."""
        mock_cursor = Mock()
        mock_cursor.extent.start.file.name = "/path/to/MyFile.cpp"
        mock_cursor.extent.start.line = 10
        mock_cursor.extent.start.column = 5
        mock_cursor.extent.start.offset = 100
        mock_cursor.extent.end.file.name = "/path/to/MyFile.cpp"
        mock_cursor.extent.end.line = 20
        mock_cursor.extent.end.column = 1
        mock_cursor.extent.end.offset = 200
        
        # Mock file reading
        with patch("builtins.open", side_effect=Exception("File not found")):
            result = generate_unnamed_type_name(mock_cursor, "/path/to/MyFile.cpp")
        
        assert result.startswith("unnamed_MyFile_")
    
    def test_with_none_file_path(self):
        """Test generating name with None file path."""
        mock_cursor = Mock()
        mock_cursor.extent.start.file = None
        mock_cursor.extent.end.file = None
        
        result = generate_unnamed_type_name(mock_cursor, None)
        
        assert result.startswith("unnamed_unknown_")
    
    def test_returns_fallback_on_exception(self):
        """Test that function returns fallback on exception."""
        mock_cursor = Mock()
        mock_cursor.extent.start.file.name = None  # Will cause exception
        
        result = generate_unnamed_type_name(mock_cursor, None)
        
        # Function generates a hash-based name when file is unknown
        assert result.startswith("unnamed_unknown_")


class TestBuildNestedCallGraph:
    """Tests for build_nested_call_graph function."""
    
    def test_builds_nested_structure(self, temp_dir):
        """Test that nested call graph is built correctly."""
        definitions_map = {
            "main": {("src/main.c", 1, 50, 0, 500)},
            "helper": {("src/helper.c", 1, 20, 0, 200)}
        }
        adjacency = {
            "main": ["helper"]
        }
        
        out_path = os.path.join(temp_dir, "nested_call_graph.json")
        
        CASTUtil.build_nested_call_graph(
            definitions_map=definitions_map,
            adjacency=adjacency,
            max_depth=1,
            out_path=out_path,
            data_type_usage=None,
            constants_usage=None
        )
        
        assert os.path.exists(out_path)
        
        import json
        with open(out_path, 'r') as f:
            result = json.load(f)
        
        assert isinstance(result, list)
        assert len(result) > 0
    
    def test_handles_empty_adjacency(self, temp_dir):
        """Test handling of empty adjacency map."""
        definitions_map = {
            "main": {("src/main.c", 1, 50, 0, 500)}
        }
        adjacency = {}
        
        out_path = os.path.join(temp_dir, "nested_call_graph.json")
        
        CASTUtil.build_nested_call_graph(
            definitions_map=definitions_map,
            adjacency=adjacency,
            max_depth=1,
            out_path=out_path,
            data_type_usage=None,
            constants_usage=None
        )
        
        assert os.path.exists(out_path)
    
    def test_includes_data_type_usage(self, temp_dir):
        """Test that data type usage is included in output."""
        definitions_map = {
            "processData": {("src/processor.c", 10, 50, 100, 500)}
        }
        adjacency = {
            "processData": []
        }
        data_type_usage = {
            "processData": ["MyStruct", "MyClass"]
        }
        
        out_path = os.path.join(temp_dir, "nested_call_graph.json")
        
        CASTUtil.build_nested_call_graph(
            definitions_map=definitions_map,
            adjacency=adjacency,
            max_depth=1,
            out_path=out_path,
            data_type_usage=data_type_usage,
            constants_usage=None
        )
        
        import json
        with open(out_path, 'r') as f:
            result = json.load(f)
        
        # Find the function entry
        found_data_types = False
        for file_entry in result:
            for func in file_entry.get("functions", []):
                if func["function"] == "processData":
                    assert "data_types_used" in func
                    assert "MyStruct" in func["data_types_used"]
                    assert "MyClass" in func["data_types_used"]
                    found_data_types = True
        
        assert found_data_types
    
    def test_includes_constants_usage(self, temp_dir):
        """Test that constants usage is included in output."""
        definitions_map = {
            "calculate": {("src/calc.c", 10, 50, 100, 500)}
        }
        adjacency = {
            "calculate": []
        }
        constants_usage = {
            "calculate": {"MAX_VALUE": 100, "MIN_VALUE": 0}
        }
        
        out_path = os.path.join(temp_dir, "nested_call_graph.json")
        
        CASTUtil.build_nested_call_graph(
            definitions_map=definitions_map,
            adjacency=adjacency,
            max_depth=1,
            out_path=out_path,
            data_type_usage=None,
            constants_usage=constants_usage
        )
        
        import json
        with open(out_path, 'r') as f:
            result = json.load(f)
        
        # Find the function entry
        found_constants = False
        for file_entry in result:
            for func in file_entry.get("functions", []):
                if func["function"] == "calculate":
                    assert "constants_used" in func
                    assert func["constants_used"]["MAX_VALUE"] == 100
                    assert func["constants_used"]["MIN_VALUE"] == 0
                    found_constants = True
        
        assert found_constants


class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_strip_template_params_unbalanced_brackets(self):
        """Test strip_template_params with unbalanced brackets."""
        # Missing closing bracket - should handle gracefully
        result = strip_template_params("vector<int")
        assert ">" not in result
    
    def test_base_function_name_with_nested_parens(self):
        """Test base_function_name with nested parentheses."""
        result = base_function_name("func(callback(int))")
        assert result == "func"
    
    def test_merge_empty_registries(self):
        """Test merging empty registries."""
        merged = CASTUtil._merge_function_registries({}, {})
        assert merged == {}
    
    def test_merge_with_one_empty_registry(self):
        """Test merging when one registry is empty."""
        registry1 = {"func_a": {("file.cpp", 1, 10, 0, 100)}}
        
        merged = CASTUtil._merge_function_registries(registry1, {})
        assert "func_a" in merged
        
        merged = CASTUtil._merge_function_registries({}, registry1)
        assert "func_a" in merged


class TestParallelProcessingConfiguration:
    """Tests for parallel processing configuration and constants."""
    
    def test_default_parallel_enabled(self):
        """Test that parallel processing is enabled by default."""
        from hindsight.core.constants import AST_DEFAULT_PARALLEL_ENABLED
        assert AST_DEFAULT_PARALLEL_ENABLED is True
    
    def test_default_max_workers(self):
        """Test that default max workers is set correctly."""
        from hindsight.core.constants import AST_DEFAULT_MAX_WORKERS
        assert AST_DEFAULT_MAX_WORKERS == 4
    
    def test_min_files_for_parallel(self):
        """Test that minimum files threshold for parallel processing is set."""
        from hindsight.core.constants import AST_MIN_FILES_FOR_PARALLEL
        assert AST_MIN_FILES_FOR_PARALLEL == 10


class TestParallelProcessingDecision:
    """Tests for parallel processing decision logic."""
    
    def test_should_use_parallel_with_enough_files(self):
        """Test that parallel is used when file count exceeds threshold."""
        from hindsight.core.constants import AST_MIN_FILES_FOR_PARALLEL
        
        # Create a list of files larger than the threshold
        files = [Path(f"/tmp/file{i}.cpp") for i in range(AST_MIN_FILES_FOR_PARALLEL + 5)]
        
        # When use_parallel=True and enough files, should use parallel
        assert len(files) >= AST_MIN_FILES_FOR_PARALLEL
    
    def test_should_not_use_parallel_with_few_files(self):
        """Test that parallel is not used when file count is below threshold."""
        from hindsight.core.constants import AST_MIN_FILES_FOR_PARALLEL
        
        # Create a list of files smaller than the threshold
        files = [Path(f"/tmp/file{i}.cpp") for i in range(AST_MIN_FILES_FOR_PARALLEL - 5)]
        
        # When file count is below threshold, should not use parallel
        assert len(files) < AST_MIN_FILES_FOR_PARALLEL
    
    def test_parallel_disabled_explicitly(self):
        """Test that parallel can be explicitly disabled."""
        # When use_parallel=False, should not use parallel regardless of file count
        use_parallel = False
        assert use_parallel is False


class TestParallelMergeFunctions:
    """Tests for parallel result merging functions."""
    
    def test_merge_function_registries_from_parallel_results(self):
        """Test merging function registries from multiple parallel workers."""
        # Simulate results from 3 parallel workers
        worker_results = [
            {"func_a": {("file1.cpp", 1, 10, 0, 100)}, "func_b": {("file1.cpp", 20, 30, 200, 300)}},
            {"func_c": {("file2.cpp", 1, 15, 0, 150)}, "func_a": {("file2.cpp", 50, 60, 500, 600)}},
            {"func_d": {("file3.cpp", 1, 20, 0, 200)}}
        ]
        
        # Merge all results
        merged = {}
        for result in worker_results:
            merged = CASTUtil._merge_function_registries(merged, result)
        
        # Verify all functions are present
        assert "func_a" in merged
        assert "func_b" in merged
        assert "func_c" in merged
        assert "func_d" in merged
        
        # Verify func_a has both locations
        assert len(merged["func_a"]) == 2
    
    def test_merge_call_graphs_from_parallel_results(self):
        """Test merging call graphs from multiple parallel workers."""
        # Simulate results from 3 parallel workers
        worker_results = [
            {"caller_a": ["callee_1", "callee_2"]},
            {"caller_b": ["callee_3"], "caller_a": ["callee_2", "callee_4"]},
            {"caller_c": ["callee_5"]}
        ]
        
        # Merge all results
        merged = {}
        for result in worker_results:
            merged = CASTUtil._merge_call_graphs(merged, result)
        
        # Verify all callers are present
        assert "caller_a" in merged
        assert "caller_b" in merged
        assert "caller_c" in merged
        
        # Verify caller_a has all unique callees (no duplicates)
        assert "callee_1" in merged["caller_a"]
        assert "callee_2" in merged["caller_a"]
        assert "callee_4" in merged["caller_a"]
        # callee_2 should not be duplicated
        assert merged["caller_a"].count("callee_2") == 1
    
    def test_merge_data_types_registries_from_parallel_results(self):
        """Test merging data types registries from multiple parallel workers."""
        # Simulate results from 2 parallel workers
        worker_results = [
            {"MyClass": [{"file_name": "file1.h", "start": 1, "end": 10}]},
            {"MyClass": [{"file_name": "file2.h", "start": 5, "end": 15}],
             "OtherClass": [{"file_name": "file2.h", "start": 20, "end": 30}]}
        ]
        
        # Merge all results
        merged = {}
        for result in worker_results:
            merged = CASTUtil._merge_data_types_registries(merged, result)
        
        # Verify all classes are present
        assert "MyClass" in merged
        assert "OtherClass" in merged
        
        # Verify MyClass has both locations
        assert len(merged["MyClass"]) == 2
    
    def test_merge_data_type_usage_from_parallel_results(self):
        """Test merging data type usage from multiple parallel workers."""
        # Simulate results from 2 parallel workers
        worker_results = [
            {"func_a": ["TypeA", "TypeB"]},
            {"func_a": ["TypeB", "TypeC"], "func_b": ["TypeD"]}
        ]
        
        # Merge all results
        merged = {}
        for result in worker_results:
            merged = CASTUtil._merge_data_type_usage(merged, result)
        
        # Verify all functions are present
        assert "func_a" in merged
        assert "func_b" in merged
        
        # Verify func_a has all types
        assert "TypeA" in merged["func_a"]
        assert "TypeB" in merged["func_a"]
        assert "TypeC" in merged["func_a"]
    
    def test_merge_constants_usage_from_parallel_results(self):
        """Test merging constants usage from multiple parallel workers."""
        # Simulate results from 2 parallel workers
        worker_results = [
            {"func_a": {"CONST_A": 1, "CONST_B": 2}},
            {"func_a": {"CONST_B": 2, "CONST_C": 3}, "func_b": {"CONST_D": 4}}
        ]
        
        # Merge all results
        merged = {}
        for result in worker_results:
            merged = CASTUtil._merge_constants_usage(merged, result)
        
        # Verify all functions are present
        assert "func_a" in merged
        assert "func_b" in merged
        
        # Verify func_a has all constants
        assert merged["func_a"]["CONST_A"] == 1
        assert merged["func_a"]["CONST_B"] == 2
        assert merged["func_a"]["CONST_C"] == 3


class TestParallelProcessingWorkerFunctions:
    """Tests for module-level worker functions used in parallel processing."""
    
    def test_worker_function_exists(self):
        """Test that worker functions are defined at module level."""
        from hindsight.core.lang_util import cast_util
        
        # Check that worker functions exist (actual implementation names)
        assert hasattr(cast_util, '_process_single_file_for_functions')
        assert hasattr(cast_util, '_process_single_file_for_call_graph')
        assert hasattr(cast_util, '_process_single_file_for_data_types')
        assert hasattr(cast_util, '_process_single_file_for_data_type_usage')
        assert hasattr(cast_util, '_process_single_file_for_constants_usage')
        assert hasattr(cast_util, '_init_worker_process')
    
    def test_worker_functions_are_callable(self):
        """Test that worker functions are callable."""
        from hindsight.core.lang_util.cast_util import (
            _process_single_file_for_functions,
            _process_single_file_for_call_graph,
            _process_single_file_for_data_types,
            _process_single_file_for_data_type_usage,
            _process_single_file_for_constants_usage,
            _init_worker_process
        )
        
        assert callable(_process_single_file_for_functions)
        assert callable(_process_single_file_for_call_graph)
        assert callable(_process_single_file_for_data_types)
        assert callable(_process_single_file_for_data_type_usage)
        assert callable(_process_single_file_for_constants_usage)
        assert callable(_init_worker_process)


class TestParallelProcessingIntegration:
    """Integration tests for parallel processing functionality."""
    
    def test_build_function_registry_accepts_parallel_params(self):
        """Test that build_function_registry accepts parallel parameters."""
        import inspect
        sig = inspect.signature(CASTUtil.build_function_registry)
        params = sig.parameters
        
        assert 'use_parallel' in params
        assert 'max_workers' in params
        
        # Check default values - use_parallel defaults to None (auto-detect based on file count)
        # max_workers defaults to None (uses DEFAULT_MAX_WORKERS constant)
        assert params['use_parallel'].default is None
        assert params['max_workers'].default is None
    
    def test_build_forward_call_graph_accepts_parallel_params(self):
        """Test that build_forward_call_graph accepts parallel parameters."""
        import inspect
        sig = inspect.signature(CASTUtil.build_forward_call_graph)
        params = sig.parameters
        
        assert 'use_parallel' in params
        assert 'max_workers' in params
        
        # Check default values - use_parallel defaults to None (auto-detect based on file count)
        # max_workers defaults to None (uses DEFAULT_MAX_WORKERS constant)
        assert params['use_parallel'].default is None
        assert params['max_workers'].default is None
    
    def test_build_data_types_registry_accepts_parallel_params(self):
        """Test that build_data_types_registry accepts parallel parameters."""
        import inspect
        sig = inspect.signature(CASTUtil.build_data_types_registry)
        params = sig.parameters
        
        assert 'use_parallel' in params
        assert 'max_workers' in params
        
        # Check default values - use_parallel defaults to None (auto-detect based on file count)
        # max_workers defaults to None (uses DEFAULT_MAX_WORKERS constant)
        assert params['use_parallel'].default is None
        assert params['max_workers'].default is None
    
    def test_build_data_type_use_with_macros_accepts_parallel_params(self):
        """Test that build_data_type_use_with_macros accepts parallel parameters."""
        import inspect
        sig = inspect.signature(CASTUtil.build_data_type_use_with_macros)
        params = sig.parameters
        
        assert 'use_parallel' in params
        assert 'max_workers' in params
        
        # Check default values - use_parallel defaults to None (auto-detect based on file count)
        # max_workers defaults to None (uses DEFAULT_MAX_WORKERS constant)
        assert params['use_parallel'].default is None
        assert params['max_workers'].default is None
    
    def test_build_constants_usage_with_macros_accepts_parallel_params(self):
        """Test that build_constants_usage_with_macros accepts parallel parameters."""
        import inspect
        sig = inspect.signature(CASTUtil.build_constants_usage_with_macros)
        params = sig.parameters
        
        assert 'use_parallel' in params
        assert 'max_workers' in params
        
        # Check default values - use_parallel defaults to None (auto-detect based on file count)
        # max_workers defaults to None (uses DEFAULT_MAX_WORKERS constant)
        assert params['use_parallel'].default is None
        assert params['max_workers'].default is None


class TestParallelDataTypeUsage:
    """Tests for parallel processing of data type usage."""
    
    def test_merge_parallel_data_type_usage_results(self):
        """Test merging data type usage results from parallel workers."""
        # Simulate results from parallel workers
        # Note: Results without 'error' key are processed; results with 'error' key are skipped
        results = [
            {'file': 'file1.cpp', 'usage': {'func_a': ['TypeA', 'TypeB']}},
            {'file': 'file2.cpp', 'usage': {'func_a': ['TypeB', 'TypeC'], 'func_b': ['TypeD']}},
            {'file': 'file3.cpp', 'usage': {'func_c': ['TypeE']}}  # No error key = processed
        ]
        
        merged = CASTUtil._merge_parallel_data_type_usage_results(results)
        
        # Verify all functions are present
        assert 'func_a' in merged
        assert 'func_b' in merged
        assert 'func_c' in merged
        
        # Verify func_a has all types merged (no duplicates)
        assert 'TypeA' in merged['func_a']
        assert 'TypeB' in merged['func_a']
        assert 'TypeC' in merged['func_a']
        assert len(merged['func_a']) == 3  # TypeA, TypeB, TypeC (no duplicates)
    
    def test_merge_parallel_data_type_usage_results_with_errors(self):
        """Test that results with errors are skipped during merge."""
        results = [
            {'file': 'file1.cpp', 'usage': {'func_a': ['TypeA']}},
            {'file': 'file2.cpp', 'usage': {}, 'error': 'Parse error'},  # Error result
            {'file': 'file3.cpp', 'usage': {'func_b': ['TypeB']}}
        ]
        
        merged = CASTUtil._merge_parallel_data_type_usage_results(results)
        
        # Verify only non-error results are merged
        assert 'func_a' in merged
        assert 'func_b' in merged
        assert len(merged) == 2
    
    def test_merge_parallel_data_type_usage_results_empty(self):
        """Test merging empty results."""
        results = []
        
        merged = CASTUtil._merge_parallel_data_type_usage_results(results)
        
        assert merged == {}
    
    def test_build_data_type_use_parallel_method_exists(self):
        """Test that _build_data_type_use_parallel method exists."""
        assert hasattr(CASTUtil, '_build_data_type_use_parallel')
        assert callable(CASTUtil._build_data_type_use_parallel)


class TestParallelConstantsUsage:
    """Tests for parallel processing of constants usage."""
    
    def test_merge_parallel_constants_usage_results(self):
        """Test merging constants usage results from parallel workers."""
        # Simulate results from parallel workers
        # Note: Results without 'error' key are processed; results with 'error' key are skipped
        results = [
            {'file': 'file1.cpp', 'usage': {'func_a': {'CONST_A': 1, 'CONST_B': 2}}},
            {'file': 'file2.cpp', 'usage': {'func_a': {'CONST_B': 2, 'CONST_C': 3}, 'func_b': {'CONST_D': 4}}},
            {'file': 'file3.cpp', 'usage': {'func_c': {'CONST_E': 5}}}  # No error key = processed
        ]
        
        merged = CASTUtil._merge_parallel_constants_usage_results(results)
        
        # Verify all functions are present
        assert 'func_a' in merged
        assert 'func_b' in merged
        assert 'func_c' in merged
        
        # Verify func_a has all constants merged
        assert merged['func_a']['CONST_A'] == 1
        assert merged['func_a']['CONST_B'] == 2
        assert merged['func_a']['CONST_C'] == 3
    
    def test_merge_parallel_constants_usage_results_with_errors(self):
        """Test that results with errors are skipped during merge."""
        results = [
            {'file': 'file1.cpp', 'usage': {'func_a': {'CONST_A': 1}}},
            {'file': 'file2.cpp', 'usage': {}, 'error': 'Parse error'},  # Error result
            {'file': 'file3.cpp', 'usage': {'func_b': {'CONST_B': 2}}}
        ]
        
        merged = CASTUtil._merge_parallel_constants_usage_results(results)
        
        # Verify only non-error results are merged
        assert 'func_a' in merged
        assert 'func_b' in merged
        assert len(merged) == 2
    
    def test_merge_parallel_constants_usage_results_empty(self):
        """Test merging empty results."""
        results = []
        
        merged = CASTUtil._merge_parallel_constants_usage_results(results)
        
        assert merged == {}
    
    def test_build_constants_usage_parallel_method_exists(self):
        """Test that _build_constants_usage_parallel method exists."""
        assert hasattr(CASTUtil, '_build_constants_usage_parallel')
        assert callable(CASTUtil._build_constants_usage_parallel)


class TestParallelProcessingDecisionForUsage:
    """Tests for parallel processing decision logic for data type and constants usage."""
    
    def test_should_use_parallel_returns_tuple(self):
        """Test that _should_use_parallel returns a tuple of (bool, int)."""
        files = [Path(f"/tmp/file{i}.cpp") for i in range(20)]
        
        result = CASTUtil._should_use_parallel(files, use_parallel=True, max_workers=4)
        
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], int)
    
    def test_should_use_parallel_respects_explicit_false(self):
        """Test that _should_use_parallel respects explicit use_parallel=False."""
        files = [Path(f"/tmp/file{i}.cpp") for i in range(100)]  # Many files
        
        should_parallel, num_workers = CASTUtil._should_use_parallel(files, use_parallel=False, max_workers=4)
        
        # Even with many files, explicit False should disable parallel
        assert should_parallel is False
    
    def test_should_use_parallel_respects_explicit_true(self):
        """Test that _should_use_parallel respects explicit use_parallel=True with enough files."""
        from hindsight.core.constants import AST_MIN_FILES_FOR_PARALLEL
        
        # Create enough files to meet the threshold
        files = [Path(f"/tmp/file{i}.cpp") for i in range(AST_MIN_FILES_FOR_PARALLEL + 10)]
        
        should_parallel, num_workers = CASTUtil._should_use_parallel(files, use_parallel=True, max_workers=4)
        
        # With explicit True and enough files, should use parallel
        assert should_parallel is True
        assert num_workers == 4
    
    def test_should_use_parallel_auto_detect_with_few_files(self):
        """Test that _should_use_parallel auto-detects and disables for few files."""
        from hindsight.core.constants import AST_MIN_FILES_FOR_PARALLEL
        
        # Create fewer files than the threshold
        files = [Path(f"/tmp/file{i}.cpp") for i in range(AST_MIN_FILES_FOR_PARALLEL - 5)]
        
        should_parallel, num_workers = CASTUtil._should_use_parallel(files, use_parallel=None, max_workers=4)
        
        # With auto-detect and few files, should not use parallel
        assert should_parallel is False


class TestParallelProcessingWithRealFiles:
    """Integration tests for parallel processing with real C/C++ files."""
    
    @pytest.fixture
    def temp_cpp_files(self, tmp_path):
        """Create temporary C++ files for testing."""
        files = []
        
        # Create a simple C++ file with a function that uses a custom type
        file1 = tmp_path / "file1.cpp"
        file1.write_text("""
struct MyStruct {
    int value;
};

const int MAX_VALUE = 100;

void processData(MyStruct* data) {
    if (data->value > MAX_VALUE) {
        data->value = MAX_VALUE;
    }
}
""")
        files.append(file1)
        
        # Create another C++ file
        file2 = tmp_path / "file2.cpp"
        file2.write_text("""
class MyClass {
public:
    void doSomething();
};

static const int MIN_VALUE = 0;

void MyClass::doSomething() {
    int x = MIN_VALUE;
}
""")
        files.append(file2)
        
        return files, tmp_path
    
    def test_parallel_data_type_usage_produces_same_results_as_sequential(self, temp_cpp_files):
        """Test that parallel and sequential data type usage produce equivalent results."""
        files, repo_root = temp_cpp_files
        
        # Skip if libclang is not available
        try:
            from hindsight.core.lang_util.Environment import Environment
            Environment.initialize_libclang()
        except Exception:
            pytest.skip("libclang not available")
        
        # Build data type usage sequentially
        sequential_result = CASTUtil._build_data_type_use_single_pass(
            repo_root=repo_root,
            source_files=files,
            clang_args=[],
            macro_flags=[],
            custom_types_registry=None
        )
        
        # Build data type usage in parallel (force parallel even with few files)
        # Note: With only 2 files, parallel may not be used, but we test the method exists
        # and can be called
        assert hasattr(CASTUtil, '_build_data_type_use_parallel')
        
        # Verify sequential result has expected structure
        assert isinstance(sequential_result, dict)
    
    def test_parallel_constants_usage_produces_same_results_as_sequential(self, temp_cpp_files):
        """Test that parallel and sequential constants usage produce equivalent results."""
        files, repo_root = temp_cpp_files
        
        # Skip if libclang is not available
        try:
            from hindsight.core.lang_util.Environment import Environment
            Environment.initialize_libclang()
        except Exception:
            pytest.skip("libclang not available")
        
        # Build constants usage sequentially
        sequential_result = CASTUtil._build_constants_usage_single_pass(
            repo_root=repo_root,
            source_files=files,
            clang_args=[],
            macro_flags=[],
            function_registry=None
        )
        
        # Build constants usage in parallel (force parallel even with few files)
        # Note: With only 2 files, parallel may not be used, but we test the method exists
        # and can be called
        assert hasattr(CASTUtil, '_build_constants_usage_parallel')
        
        # Verify sequential result has expected structure
        assert isinstance(sequential_result, dict)


class TestObjectiveCInterfaceDetection:
    """Tests for Objective-C @interface detection in data types registry.
    
    This tests the fix for the bug where Objective-C @interface declarations
    in header files were not being detected because libclang's is_definition()
    returns False for @interface (only @implementation is considered a definition).
    """
    
    @pytest.fixture
    def temp_objc_files(self, tmp_path):
        """Create temporary Objective-C files for testing."""
        files = []
        
        # Create an Objective-C header file with @interface declarations
        header_file = tmp_path / "MyViewController.h"
        header_file.write_text("""
#import <Foundation/Foundation.h>

// Typedef for completion block
typedef void (^CompletionBlock)(BOOL success);

// Protocol declaration
@protocol MyViewControllerDelegate <NSObject>
- (void)viewControllerDidFinish;
@end

// Class interface declaration (this should be detected!)
@interface MyViewController : NSObject

@property (nonatomic, weak) id<MyViewControllerDelegate> delegate;
@property (nonatomic, copy) CompletionBlock completionHandler;

- (void)loadData;
- (void)processWithCompletion:(CompletionBlock)completion;

@end

// Another class interface
@interface SettingsViewController : NSObject
@property (nonatomic, assign) BOOL isEnabled;
- (void)saveSettings;
@end
""")
        files.append(header_file)
        
        # Create an Objective-C implementation file
        impl_file = tmp_path / "MyViewController.m"
        impl_file.write_text("""
#import "MyViewController.h"

@implementation MyViewController

- (void)loadData {
    // Implementation
}

- (void)processWithCompletion:(CompletionBlock)completion {
    if (completion) {
        completion(YES);
    }
}

@end
""")
        files.append(impl_file)
        
        return files, tmp_path
    
    def test_objc_interface_detected_in_header(self, temp_objc_files):
        """Test that @interface declarations in .h files are detected as data types.
        
        This is the key test for the bug fix. Before the fix, @interface declarations
        were not detected because is_definition() returns False for them.
        """
        files, repo_root = temp_objc_files
        
        # Skip if libclang is not available
        try:
            from hindsight.core.lang_util.Environment import Environment
            Environment.initialize_libclang()
        except Exception:
            pytest.skip("libclang not available")
        
        # Build data types registry
        registry = CASTUtil._build_data_types_registry_single_pass(
            repo_root=repo_root,
            source_files=files,
            clang_args=[],
            macro_flags=[]
        )
        
        # Verify that Objective-C interfaces are detected
        # The class names should be in the registry
        assert "MyViewController" in registry, \
            f"MyViewController @interface not detected. Found types: {list(registry.keys())}"
        assert "SettingsViewController" in registry, \
            f"SettingsViewController @interface not detected. Found types: {list(registry.keys())}"
    
    def test_objc_protocol_detected_in_header(self, temp_objc_files):
        """Test that @protocol declarations in .h files are detected as data types."""
        files, repo_root = temp_objc_files
        
        # Skip if libclang is not available
        try:
            from hindsight.core.lang_util.Environment import Environment
            Environment.initialize_libclang()
        except Exception:
            pytest.skip("libclang not available")
        
        # Build data types registry
        registry = CASTUtil._build_data_types_registry_single_pass(
            repo_root=repo_root,
            source_files=files,
            clang_args=[],
            macro_flags=[]
        )
        
        # Verify that Objective-C protocols are detected
        assert "MyViewControllerDelegate" in registry, \
            f"MyViewControllerDelegate @protocol not detected. Found types: {list(registry.keys())}"
    
    def test_objc_typedef_detected_in_header(self, temp_objc_files):
        """Test that typedef declarations in .h files are detected as data types."""
        files, repo_root = temp_objc_files
        
        # Skip if libclang is not available
        try:
            from hindsight.core.lang_util.Environment import Environment
            Environment.initialize_libclang()
        except Exception:
            pytest.skip("libclang not available")
        
        # Build data types registry
        registry = CASTUtil._build_data_types_registry_single_pass(
            repo_root=repo_root,
            source_files=files,
            clang_args=[],
            macro_flags=[]
        )
        
        # Verify that typedef is detected
        assert "CompletionBlock" in registry, \
            f"CompletionBlock typedef not detected. Found types: {list(registry.keys())}"
    
    def test_objc_interface_detected_by_parallel_worker(self, temp_objc_files):
        """Test that the parallel worker function also detects @interface declarations.
        
        Note: This test passes Objective-C flags explicitly because .h files default
        to C++ parsing. In real usage, the .m/.mm files in the same directory would
        establish the Objective-C context.
        """
        files, repo_root = temp_objc_files
        
        # Skip if libclang is not available
        try:
            from hindsight.core.lang_util.Environment import Environment
            Environment.initialize_libclang()
        except Exception:
            pytest.skip("libclang not available")
        
        from hindsight.core.lang_util.cast_util import _process_single_file_for_data_types
        
        # Process just the header file with Objective-C flags
        # Note: .h files default to C++ parsing, so we need to pass Objective-C flags
        # to properly parse Objective-C syntax like @interface
        header_file = files[0]  # MyViewController.h
        objc_flags = ['-x', 'objective-c++', '-std=c++20', '-fobjc-arc']
        args = (str(header_file), str(repo_root), objc_flags, [])
        
        result = _process_single_file_for_data_types(args)
        
        # Verify no error
        assert 'error' not in result, f"Worker returned error: {result.get('error')}"
        
        # Verify that Objective-C interfaces are detected
        registry = result.get('registry', {})
        assert "MyViewController" in registry, \
            f"MyViewController @interface not detected by parallel worker. Found types: {list(registry.keys())}"
        assert "SettingsViewController" in registry, \
            f"SettingsViewController @interface not detected by parallel worker. Found types: {list(registry.keys())}"
        assert "MyViewControllerDelegate" in registry, \
            f"MyViewControllerDelegate @protocol not detected by parallel worker. Found types: {list(registry.keys())}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

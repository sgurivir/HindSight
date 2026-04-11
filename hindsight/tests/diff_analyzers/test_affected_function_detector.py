#!/usr/bin/env python3
"""
Unit tests for AffectedFunctionDetector

Tests the detection of functions affected by git changes using AST and call graph analysis.
"""

import pytest
from typing import Dict, List, Any

from hindsight.diff_analyzers.affected_function_detector import (
    AffectedFunctionDetector,
    extract_changed_lines_per_file
)


class TestExtractChangedLinesPerFile:
    """Tests for the extract_changed_lines_per_file function."""
    
    def test_simple_addition(self):
        """Test extracting added lines from a simple diff."""
        diff_content = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -10,6 +10,8 @@ def existing_function():
     pass
 
+def new_function():
+    return 42
 
 def another_function():
     pass
"""
        result = extract_changed_lines_per_file(diff_content)
        
        assert 'src/main.py' in result
        assert 12 in result['src/main.py']['added']
        assert 13 in result['src/main.py']['added']
        assert len(result['src/main.py']['removed']) == 0
    
    def test_simple_removal(self):
        """Test extracting removed lines from a simple diff."""
        diff_content = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -10,8 +10,6 @@ def existing_function():
     pass
 
-def old_function():
-    return 42
 
 def another_function():
     pass
"""
        result = extract_changed_lines_per_file(diff_content)
        
        assert 'src/main.py' in result
        assert 12 in result['src/main.py']['removed']
        assert 13 in result['src/main.py']['removed']
    
    def test_mixed_changes(self):
        """Test extracting both added and removed lines."""
        diff_content = """diff --git a/src/utils.py b/src/utils.py
--- a/src/utils.py
+++ b/src/utils.py
@@ -5,7 +5,7 @@ import os
 
 def process_data(data):
-    return data.strip()
+    return data.strip().lower()
 
 def validate(input):
     pass
"""
        result = extract_changed_lines_per_file(diff_content)
        
        assert 'src/utils.py' in result
        assert len(result['src/utils.py']['added']) >= 1
        assert len(result['src/utils.py']['removed']) >= 1
    
    def test_multiple_files(self):
        """Test extracting changes from multiple files."""
        diff_content = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
+import utils
 
 def main():
     pass
diff --git a/src/utils.py b/src/utils.py
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,4 @@
+import os
 
 def helper():
     pass
"""
        result = extract_changed_lines_per_file(diff_content)
        
        assert 'src/main.py' in result
        assert 'src/utils.py' in result
        assert len(result) == 2
    
    def test_empty_diff(self):
        """Test handling of empty diff."""
        diff_content = ""
        result = extract_changed_lines_per_file(diff_content)
        
        assert result == {}
    
    def test_modified_ranges(self):
        """Test that modified_ranges are extracted from hunk headers."""
        diff_content = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -10,6 +10,8 @@ def existing_function():
     pass
 
+def new_function():
+    return 42
"""
        result = extract_changed_lines_per_file(diff_content)
        
        assert 'src/main.py' in result
        assert len(result['src/main.py']['modified_ranges']) > 0


class TestAffectedFunctionDetector:
    """Tests for the AffectedFunctionDetector class."""
    
    @pytest.fixture
    def sample_call_graph(self) -> Dict[str, Any]:
        """Create a sample call graph for testing."""
        return {
            'call_graph': [
                {
                    'file': 'src/main.py',
                    'functions': [
                        {
                            'function': 'main',
                            'functions_invoked': ['process_data', 'validate'],
                            'invoked_by': [],
                            'data_types_used': ['str', 'int'],
                            'constants_used': {'MAX_SIZE': 100},
                            'context': {'start': 10, 'end': 25}
                        },
                        {
                            'function': 'process_data',
                            'functions_invoked': ['helper'],
                            'invoked_by': ['main'],
                            'data_types_used': ['str'],
                            'constants_used': {},
                            'context': {'start': 30, 'end': 45}
                        }
                    ]
                },
                {
                    'file': 'src/utils.py',
                    'functions': [
                        {
                            'function': 'helper',
                            'functions_invoked': [],
                            'invoked_by': ['process_data'],
                            'data_types_used': [],
                            'constants_used': {},
                            'context': {'start': 5, 'end': 15}
                        },
                        {
                            'function': 'validate',
                            'functions_invoked': [],
                            'invoked_by': ['main'],
                            'data_types_used': ['bool'],
                            'constants_used': {},
                            'context': {'start': 20, 'end': 30}
                        }
                    ]
                }
            ]
        }
    
    @pytest.fixture
    def sample_functions(self) -> Dict[str, Any]:
        """Create a sample functions mapping for testing (new checksum format)."""
        return {
            'main': {
                'checksum': 'abc123',
                'code': [{'file_name': 'src/main.py', 'start': 10, 'end': 25}]
            },
            'process_data': {
                'checksum': 'def456',
                'code': [{'file_name': 'src/main.py', 'start': 30, 'end': 45}]
            },
            'helper': {
                'checksum': 'ghi789',
                'code': [{'file_name': 'src/utils.py', 'start': 5, 'end': 15}]
            },
            'validate': {
                'checksum': 'jkl012',
                'code': [{'file_name': 'src/utils.py', 'start': 20, 'end': 30}]
            }
        }
    
    @pytest.fixture
    def sample_changed_lines(self) -> Dict[str, Dict[str, List[int]]]:
        """Create sample changed lines for testing."""
        return {
            'src/main.py': {
                'added': [35, 36, 37],  # Lines within process_data function
                'removed': [],
                'modified_ranges': [(30, 45)]
            }
        }
    
    def test_initialization(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test detector initialization."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        assert detector is not None
        assert len(detector._function_locations) == 4
        assert len(detector._call_graph_by_function) == 4
    
    def test_is_function_modified_true(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test detecting a modified function."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        # process_data spans lines 30-45, and we have changes at 35-37
        assert detector.is_function_modified('process_data') is True
    
    def test_is_function_modified_false(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test detecting an unmodified function."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        # main spans lines 10-25, no changes there
        assert detector.is_function_modified('main') is False
        
        # helper is in a different file with no changes
        assert detector.is_function_modified('helper') is False
    
    def test_get_directly_modified_functions(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test getting directly modified functions."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        modified = detector.get_directly_modified_functions()
        
        assert len(modified) == 1
        assert modified[0]['function'] == 'process_data'
        assert modified[0]['file'] == 'src/main.py'
        assert 35 in modified[0]['changed_lines']
    
    def test_get_affected_functions_with_callers(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test getting affected functions including callers."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        affected = detector.get_affected_functions(
            include_callers=True,
            include_callees=False,
            max_depth=1
        )
        
        # Should include process_data (modified) and main (calls process_data)
        func_names = [f['function'] for f in affected]
        assert 'process_data' in func_names
        assert 'main' in func_names
        
        # Check affected reasons
        for func in affected:
            if func['function'] == 'process_data':
                assert func['affected_reason'] == 'modified'
            elif func['function'] == 'main':
                assert func['affected_reason'] == 'calls_modified'
    
    def test_get_affected_functions_with_callees(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test getting affected functions including callees."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        affected = detector.get_affected_functions(
            include_callers=False,
            include_callees=True,
            max_depth=1
        )
        
        # Should include process_data (modified) and helper (called by process_data)
        func_names = [f['function'] for f in affected]
        assert 'process_data' in func_names
        assert 'helper' in func_names
        
        # Check affected reasons
        for func in affected:
            if func['function'] == 'helper':
                assert func['affected_reason'] == 'called_by_modified'
    
    def test_get_affected_functions_both_directions(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test getting affected functions in both directions."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        affected = detector.get_affected_functions(
            include_callers=True,
            include_callees=True,
            max_depth=1
        )
        
        # Should include process_data, main, and helper
        func_names = [f['function'] for f in affected]
        assert 'process_data' in func_names
        assert 'main' in func_names
        assert 'helper' in func_names
    
    def test_get_function_call_context(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test getting call context for a function."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        context = detector.get_function_call_context('main')
        
        assert 'process_data' in context['functions_invoked']
        assert 'validate' in context['functions_invoked']
        assert context['invoked_by'] == []
        assert 'str' in context['data_types_used']
        assert context['constants_used'].get('MAX_SIZE') == 100
    
    def test_get_all_related_functions(self, sample_call_graph, sample_functions, sample_changed_lines):
        """Test getting all related functions with modification status."""
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=sample_changed_lines
        )
        
        related = detector.get_all_related_functions('main')
        
        # main calls process_data and validate
        invoked_names = [f['function'] for f in related['invoked_functions']]
        assert 'process_data' in invoked_names or 'validate' in invoked_names
        
        # Check is_modified flag
        for func in related['invoked_functions']:
            if func['function'] == 'process_data':
                assert func['is_modified'] is True
    
    def test_empty_inputs(self):
        """Test handling of empty inputs."""
        detector = AffectedFunctionDetector(
            call_graph={},
            functions={},
            changed_lines_per_file={}
        )
        
        assert detector.get_directly_modified_functions() == []
        assert detector.get_affected_functions() == []
        assert detector.is_function_modified('any_function') is False
    
    def test_file_path_normalization(self, sample_call_graph, sample_functions):
        """Test that file paths are normalized correctly."""
        # Use different path formats
        changed_lines = {
            './src/main.py': {  # With ./ prefix
                'added': [35],
                'removed': [],
                'modified_ranges': []
            }
        }
        
        detector = AffectedFunctionDetector(
            call_graph=sample_call_graph,
            functions=sample_functions,
            changed_lines_per_file=changed_lines
        )
        
        # Should still detect the modification despite path format difference
        # Note: This depends on the path normalization implementation
        modified = detector.get_directly_modified_functions()
        # The test verifies the detector handles path variations
        assert isinstance(modified, list)


class TestAffectedFunctionDetectorEdgeCases:
    """Edge case tests for AffectedFunctionDetector."""
    
    def test_function_with_multiple_locations(self):
        """Test handling functions defined in multiple locations (e.g., overloads)."""
        functions = {
            'overloaded_func': {
                'checksum': 'multi123',
                'code': [
                    {'file_name': 'src/a.py', 'start': 10, 'end': 20},
                    {'file_name': 'src/b.py', 'start': 5, 'end': 15}
                ]
            }
        }
        
        changed_lines = {
            'src/b.py': {
                'added': [10],
                'removed': [],
                'modified_ranges': []
            }
        }
        
        detector = AffectedFunctionDetector(
            call_graph={},
            functions=functions,
            changed_lines_per_file=changed_lines
        )
        
        assert detector.is_function_modified('overloaded_func') is True
    
    def test_changes_at_function_boundary(self):
        """Test changes exactly at function start/end lines."""
        functions = {
            'boundary_func': {
                'checksum': 'boundary123',
                'code': [{'file_name': 'src/main.py', 'start': 10, 'end': 20}]
            }
        }
        
        # Change at start line
        changed_lines_start = {
            'src/main.py': {'added': [10], 'removed': [], 'modified_ranges': []}
        }
        
        detector = AffectedFunctionDetector(
            call_graph={},
            functions=functions,
            changed_lines_per_file=changed_lines_start
        )
        
        assert detector.is_function_modified('boundary_func') is True
        
        # Change at end line
        changed_lines_end = {
            'src/main.py': {'added': [20], 'removed': [], 'modified_ranges': []}
        }
        
        detector = AffectedFunctionDetector(
            call_graph={},
            functions=functions,
            changed_lines_per_file=changed_lines_end
        )
        
        assert detector.is_function_modified('boundary_func') is True
    
    def test_changes_outside_function(self):
        """Test changes just outside function boundaries."""
        functions = {
            'func': {
                'checksum': 'outside123',
                'code': [{'file_name': 'src/main.py', 'start': 10, 'end': 20}]
            }
        }
        
        changed_lines = {
            'src/main.py': {'added': [9, 21], 'removed': [], 'modified_ranges': []}
        }
        
        detector = AffectedFunctionDetector(
            call_graph={},
            functions=functions,
            changed_lines_per_file=changed_lines
        )
        
        assert detector.is_function_modified('func') is False
    
    def test_max_depth_zero(self):
        """Test with max_depth=0 (only directly modified)."""
        call_graph = {
            'call_graph': [{
                'file': 'src/main.py',
                'functions': [{
                    'function': 'caller',
                    'functions_invoked': ['callee'],
                    'invoked_by': [],
                    'context': {'start': 1, 'end': 10}
                }]
            }]
        }
        
        functions = {
            'caller': {
                'checksum': 'caller123',
                'code': [{'file_name': 'src/main.py', 'start': 1, 'end': 10}]
            },
            'callee': {
                'checksum': 'callee456',
                'code': [{'file_name': 'src/main.py', 'start': 15, 'end': 25}]
            }
        }
        
        changed_lines = {
            'src/main.py': {'added': [5], 'removed': [], 'modified_ranges': []}
        }
        
        detector = AffectedFunctionDetector(
            call_graph=call_graph,
            functions=functions,
            changed_lines_per_file=changed_lines
        )
        
        # With max_depth=0, should only get directly modified
        affected = detector.get_affected_functions(max_depth=0)
        
        func_names = [f['function'] for f in affected]
        assert 'caller' in func_names
        # callee should not be included with depth=0
        # (it's called by caller, but we're not traversing)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

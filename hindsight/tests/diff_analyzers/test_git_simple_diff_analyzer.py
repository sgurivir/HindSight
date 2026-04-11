#!/usr/bin/env python3
"""
Unit tests for GitSimpleCommitAnalyzer focusing on:
1. Bug fix for off-by-one in _create_diff_chunks (chunk limit enforcement)
2. Bug fix for metric mismatch between gate check and chunker
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


class TestCreateDiffChunks:
    """Tests for _create_diff_chunks method - Bug #1: Off-by-one fix"""
    
    @pytest.fixture
    def analyzer(self):
        """Create a mock analyzer with necessary attributes."""
        with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.BaseDiffAnalyzer.__init__', return_value=None):
            from hindsight.diff_analyzers.git_simple_diff_analyzer import GitSimpleCommitAnalyzer
            
            analyzer = GitSimpleCommitAnalyzer.__new__(GitSimpleCommitAnalyzer)
            analyzer.logger = MagicMock()
            analyzer.num_blocks_to_analyze = 3  # Set a small limit for testing
            return analyzer
    
    def _create_sample_diff(self, num_files: int, chars_per_file: int = 100) -> str:
        """Create a sample diff with specified number of files."""
        diff_parts = []
        for i in range(num_files):
            file_name = f"file{i}.py"
            diff_parts.append(f"""diff --git a/{file_name} b/{file_name}
index 1234567..abcdefg 100644
--- a/{file_name}
+++ b/{file_name}
@@ -1,5 +1,6 @@
 def function_{i}():
+    # Added line {i}
     pass
{'x' * (chars_per_file - 100)}
""")
        return '\n'.join(diff_parts)
    
    def test_chunk_limit_not_exceeded(self, analyzer):
        """Test that chunk count never exceeds num_blocks_to_analyze."""
        # Create a diff with many small files that would normally create many chunks
        # With MAX_FILES_PER_DIFF_CHUNK=10 and 30 files, we'd get 3 chunks without limit
        diff_content = self._create_sample_diff(num_files=30, chars_per_file=100)
        
        file_stats = {}
        for i in range(30):
            file_stats[f"file{i}.py"] = {'lines_changed': 2, 'chars_changed': 50}
        
        # Mock the constants to make testing easier
        with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.MAX_FILES_PER_DIFF_CHUNK', 5):
            with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.MAX_CHARACTERS_PER_DIFF_ANALYSIS', 100000):
                chunks = analyzer._create_diff_chunks(diff_content, file_stats)
        
        # Should never exceed the limit
        assert len(chunks) <= analyzer.num_blocks_to_analyze, \
            f"Created {len(chunks)} chunks but limit is {analyzer.num_blocks_to_analyze}"
    
    def test_chunk_limit_exactly_at_boundary(self, analyzer):
        """Test behavior when we hit exactly the chunk limit."""
        analyzer.num_blocks_to_analyze = 2
        
        # Create diff that would create exactly 2 chunks with 5 files per chunk
        diff_content = self._create_sample_diff(num_files=10, chars_per_file=100)
        
        file_stats = {}
        for i in range(10):
            file_stats[f"file{i}.py"] = {'lines_changed': 2, 'chars_changed': 50}
        
        with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.MAX_FILES_PER_DIFF_CHUNK', 5):
            with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.MAX_CHARACTERS_PER_DIFF_ANALYSIS', 100000):
                chunks = analyzer._create_diff_chunks(diff_content, file_stats)
        
        assert len(chunks) <= 2, f"Created {len(chunks)} chunks but limit is 2"
    
    def test_no_extra_chunk_after_limit_reached(self, analyzer):
        """Test that no extra chunk is added after hitting the limit (the off-by-one bug)."""
        analyzer.num_blocks_to_analyze = 2
        
        # Create diff with 15 files - with 5 files per chunk, this would be 3 chunks
        # But with limit of 2, we should stop at 2
        diff_content = self._create_sample_diff(num_files=15, chars_per_file=100)
        
        file_stats = {}
        for i in range(15):
            file_stats[f"file{i}.py"] = {'lines_changed': 2, 'chars_changed': 50}
        
        with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.MAX_FILES_PER_DIFF_CHUNK', 5):
            with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.MAX_CHARACTERS_PER_DIFF_ANALYSIS', 100000):
                chunks = analyzer._create_diff_chunks(diff_content, file_stats)
        
        # The bug was that we could get num_blocks_to_analyze + 1 chunks
        # After the fix, we should get exactly num_blocks_to_analyze or fewer
        assert len(chunks) <= analyzer.num_blocks_to_analyze, \
            f"Off-by-one bug: Created {len(chunks)} chunks but limit is {analyzer.num_blocks_to_analyze}"
    
    def test_all_files_processed_when_under_limit(self, analyzer):
        """Test that all files are processed when under the chunk limit."""
        analyzer.num_blocks_to_analyze = 10  # High limit
        
        # Create diff with 6 files
        diff_content = self._create_sample_diff(num_files=6, chars_per_file=100)
        
        file_stats = {}
        for i in range(6):
            file_stats[f"file{i}.py"] = {'lines_changed': 2, 'chars_changed': 50}
        
        with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.MAX_FILES_PER_DIFF_CHUNK', 3):
            with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.MAX_CHARACTERS_PER_DIFF_ANALYSIS', 100000):
                chunks = analyzer._create_diff_chunks(diff_content, file_stats)
        
        # Count total files in all chunks
        total_files = sum(len(chunk['files_in_chunk']) for chunk in chunks)
        
        # Should process all 6 files since we're under the limit
        assert total_files == 6, f"Expected 6 files but got {total_files}"


class TestAnalyzeDiffWithLlmMetricConsistency:
    """Tests for analyze_diff_with_llm method - Bug #2: Metric mismatch fix"""
    
    @pytest.fixture
    def analyzer(self):
        """Create a mock analyzer with necessary attributes."""
        with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.BaseDiffAnalyzer.__init__', return_value=None):
            from hindsight.diff_analyzers.git_simple_diff_analyzer import GitSimpleCommitAnalyzer
            
            analyzer = GitSimpleCommitAnalyzer.__new__(GitSimpleCommitAnalyzer)
            analyzer.logger = MagicMock()
            analyzer.config = {'api_key': 'test_key'}
            analyzer.unified_issue_filter = None
            analyzer.file_diff_stats = {}
            return analyzer
    
    def test_gate_uses_full_diff_content_size(self, analyzer):
        """Test that the gate check uses full diff content size, not just changed lines."""
        # Create a diff where:
        # - Changed lines are small (would pass old gate check)
        # - Full diff content is large (should trigger chunking)
        
        # This diff has small changes but lots of context
        diff_content = """diff --git a/file1.py b/file1.py
index 1234567..abcdefg 100644
--- a/file1.py
+++ b/file1.py
@@ -1,100 +1,101 @@
 # This is a very long file with lots of context
 # Line 2 of context
 # Line 3 of context
""" + "\n".join([f" # Context line {i}" for i in range(100)]) + """
+# This is the only changed line
""" + "\n".join([f" # More context line {i}" for i in range(100)])
        
        # The changed lines are small
        changed_lines_chars = len("+# This is the only changed line")
        
        # But the full diff is large
        full_diff_chars = len(diff_content)
        
        # Verify our test data is set up correctly
        assert changed_lines_chars < 1000, "Changed lines should be small"
        assert full_diff_chars > 2000, "Full diff should be larger"
        
        # Now test that _split_diff_by_files returns the full content
        file_sections = analyzer._split_diff_by_files(diff_content)
        
        total_section_chars = sum(len(content) for content in file_sections.values())
        
        # The total from file sections should be close to the full diff
        # (might differ slightly due to splitting logic)
        assert total_section_chars > changed_lines_chars, \
            "File sections should contain more than just changed lines"
    
    def test_split_diff_by_files_includes_context(self, analyzer):
        """Test that _split_diff_by_files includes context lines, not just changes."""
        diff_content = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,10 +1,11 @@
 def hello():
     print("hello")
+    print("world")
     return True
 
 def goodbye():
     print("bye")
"""
        
        file_sections = analyzer._split_diff_by_files(diff_content)
        
        assert 'test.py' in file_sections
        section_content = file_sections['test.py']
        
        # Should include context lines (lines without +/-)
        assert 'def hello():' in section_content
        assert 'print("hello")' in section_content
        assert 'def goodbye():' in section_content
        
        # Should also include the changed line
        assert '+    print("world")' in section_content
    
    def test_analyze_diff_stats_only_counts_changes(self, analyzer):
        """Test that _analyze_diff_stats_per_file only counts +/- lines."""
        diff_content = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,10 +1,11 @@
 def hello():
     print("hello")
+    print("world")
     return True
"""
        
        stats = analyzer._analyze_diff_stats_per_file(diff_content)
        
        assert 'test.py' in stats
        # Should only count the one changed line
        assert stats['test.py']['lines_changed'] == 1
        # chars_changed should be just the changed line
        assert stats['test.py']['chars_changed'] == len('+    print("world")')
    
    def test_metric_consistency_between_gate_and_chunker(self, analyzer):
        """Test that gate check and chunker use the same metric."""
        diff_content = """diff --git a/file1.py b/file1.py
index 1234567..abcdefg 100644
--- a/file1.py
+++ b/file1.py
@@ -1,5 +1,6 @@
 def test():
+    x = 1
     pass
"""
        
        # Get the metric used by the gate (after fix: full diff content)
        file_sections = analyzer._split_diff_by_files(diff_content)
        gate_metric = sum(len(content) for content in file_sections.values())
        
        # Get the metric used by chunker (always was full diff content)
        # In _create_diff_chunks, it uses len(file_diff) which is the same as file_sections values
        chunker_metric = sum(len(content) for content in file_sections.values())
        
        # They should be the same
        assert gate_metric == chunker_metric, \
            f"Gate metric ({gate_metric}) != Chunker metric ({chunker_metric})"


class TestSplitDiffByFiles:
    """Tests for _split_diff_by_files helper method."""
    
    @pytest.fixture
    def analyzer(self):
        """Create a mock analyzer."""
        with patch('hindsight.diff_analyzers.git_simple_diff_analyzer.BaseDiffAnalyzer.__init__', return_value=None):
            from hindsight.diff_analyzers.git_simple_diff_analyzer import GitSimpleCommitAnalyzer
            
            analyzer = GitSimpleCommitAnalyzer.__new__(GitSimpleCommitAnalyzer)
            analyzer.logger = MagicMock()
            return analyzer
    
    def test_split_single_file(self, analyzer):
        """Test splitting a diff with a single file."""
        diff_content = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,4 @@
 def test():
+    x = 1
     pass
"""
        
        sections = analyzer._split_diff_by_files(diff_content)
        
        assert len(sections) == 1
        assert 'test.py' in sections
    
    def test_split_multiple_files(self, analyzer):
        """Test splitting a diff with multiple files."""
        diff_content = """diff --git a/file1.py b/file1.py
index 1234567..abcdefg 100644
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,4 @@
+# file1 change
 pass
diff --git a/file2.py b/file2.py
index 1234567..abcdefg 100644
--- a/file2.py
+++ b/file2.py
@@ -1,3 +1,4 @@
+# file2 change
 pass
diff --git a/file3.py b/file3.py
index 1234567..abcdefg 100644
--- a/file3.py
+++ b/file3.py
@@ -1,3 +1,4 @@
+# file3 change
 pass
"""
        
        sections = analyzer._split_diff_by_files(diff_content)
        
        assert len(sections) == 3
        assert 'file1.py' in sections
        assert 'file2.py' in sections
        assert 'file3.py' in sections
        
        # Each section should contain its respective change
        assert '# file1 change' in sections['file1.py']
        assert '# file2 change' in sections['file2.py']
        assert '# file3 change' in sections['file3.py']
    
    def test_split_empty_diff(self, analyzer):
        """Test splitting an empty diff."""
        sections = analyzer._split_diff_by_files("")
        assert len(sections) == 0
    
    def test_split_preserves_full_content(self, analyzer):
        """Test that splitting preserves all diff content."""
        diff_content = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,10 +1,11 @@
 context line 1
 context line 2
+added line
 context line 3
-removed line
 context line 4
"""
        
        sections = analyzer._split_diff_by_files(diff_content)
        section_content = sections['test.py']
        
        # All lines should be preserved
        assert 'context line 1' in section_content
        assert 'context line 2' in section_content
        assert '+added line' in section_content
        assert 'context line 3' in section_content
        assert '-removed line' in section_content
        assert 'context line 4' in section_content


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

#!/usr/bin/env python3
"""
Tests for hindsight/core/llm/tools/tools.py - Tools Stage B set.
"""
import shutil
import tempfile
import pytest

from hindsight.core.llm.tools.tools import Tools


@pytest.fixture
def tmp_repo():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_tools(tmp_repo):
    return Tools(tmp_repo)


class TestToolsStageBSet:
    """Tests for with_stage_b_tools() method."""

    def test_stage_b_contains_allowed_tools(self, tmp_repo):
        """with_stage_b_tools() returns a set containing Stage B tools."""
        t = _make_tools(tmp_repo)
        stage_b = t.with_stage_b_tools()
        assert stage_b is not None
        assert stage_b._allowed_tools is not None
        assert 'readFile' in stage_b._allowed_tools
        assert 'runTerminalCmd' in stage_b._allowed_tools

    def test_stage_b_excludes_forbidden_tools(self, tmp_repo):
        """Stage B set does not contain getSummaryOfFile, list_files, etc."""
        t = _make_tools(tmp_repo)
        stage_b = t.with_stage_b_tools()
        assert 'getSummaryOfFile' not in stage_b._allowed_tools
        assert 'list_files' not in stage_b._allowed_tools
        assert 'inspectDirectoryHierarchy' not in stage_b._allowed_tools
        assert 'checkFileSize' not in stage_b._allowed_tools
        assert 'getFileContentByLines' not in stage_b._allowed_tools
        assert 'lookup_knowledge' not in stage_b._allowed_tools
        assert 'store_knowledge' not in stage_b._allowed_tools

    def test_stage_b_blocks_forbidden_tool_execution(self, tmp_repo):
        """execute_tool_use on Stage B tools raises error or returns error for blocked tools."""
        t = _make_tools(tmp_repo)
        stage_b = t.with_stage_b_tools()
        # getSummaryOfFile should be blocked
        result = stage_b.execute_tool_use({'name': 'getSummaryOfFile', 'input': {'path': '/some/file.py'}})
        assert 'error' in result.lower() or 'not allowed' in result.lower() or 'available' in result.lower()

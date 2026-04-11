"""
Issue Deduper - A tool for identifying potential duplicate issues.

This module provides functionality to:
1. Build a vector database from issue description markdown files
2. Parse LLM static analyzer HTML reports to extract issues
3. Find close matches between new issues and existing issues
4. Generate annotated HTML reports with deduplication information
"""

__version__ = "0.1.0"
__author__ = "Janus Team"

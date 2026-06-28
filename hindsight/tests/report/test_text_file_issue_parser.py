#!/usr/bin/env python3
"""Tests for hindsight.report.text_file_issue_parser."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.report.text_file_issue_parser import parse_issues_text_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_issue(issue: dict) -> str:
    """Format a single issue dict the way the JS Copy All template does.

    Mirrors hindsight/report/report_generator.py copyAllIssues() so the
    round-trip test is faithful to the actual format users will paste.
    """
    parts = [
        f"Title:\n{issue['issue']}",
        "",
        f"Severity: {issue['severity']}",
        f"Category: {issue['category']}",
        f"File: {issue['file_path']}",
        f"Function: {issue['function_name']}",
        f"Lines: {issue['line_number']}",
        "",
        f"Impact:\n{issue['description']}",
    ]
    if issue.get("evidence"):
        parts.append("")
        parts.append(f"Evidence:\n{issue['evidence']}")
    parts.append("")
    parts.append(f"Potential Solution:\n{issue['suggestion']}")
    return "\n".join(parts)


def _format_dump(issues: list) -> str:
    """Format a list of issues with the Copy All separator between blocks."""
    return "\n======================\n".join(_format_issue(i) for i in issues)


def _write_text(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Format → parse → assert dict-equality."""

    def test_single_issue_with_evidence(self):
        issue = {
            "issue": "Force unwrap may crash",
            "description": "Calling ! on optional that may be nil.",
            "evidence": "Line 42: foo!.bar()",
            "suggestion": "Use guard let.",
            "severity": "high",
            "category": "stability",
            "file_path": "src/Foo.swift",
            "function_name": "doWork",
            "line_number": "42-50",
        }
        path = _write_text(_format_dump([issue]))
        try:
            parsed = parse_issues_text_file(path)
        finally:
            os.unlink(path)

        assert len(parsed) == 1
        p = parsed[0]
        assert p["issue"] == issue["issue"]
        assert p["description"] == issue["description"]
        assert p["evidence"] == issue["evidence"]
        assert p["suggestion"] == issue["suggestion"]
        assert p["severity"] == "high"
        assert p["category"] == "stability"
        assert p["file_path"] == "src/Foo.swift"
        assert p["original_file_path"] == "src/Foo.swift"
        assert p["function_name"] == "doWork"
        assert p["line_number"] == "42-50"
        assert p["external_references"] == []

    def test_multiple_issues_round_trip(self):
        issues = [
            {
                "issue": f"Issue {i}",
                "description": f"Description {i}\nSecond line.",
                "evidence": f"Evidence {i}" if i % 2 == 0 else "",
                "suggestion": f"Fix it {i}",
                "severity": ["critical", "high", "medium"][i % 3],
                "category": "perf",
                "file_path": f"src/file_{i}.py",
                "function_name": f"fn_{i}",
                "line_number": str(10 + i),
            }
            for i in range(5)
        ]
        path = _write_text(_format_dump(issues))
        try:
            parsed = parse_issues_text_file(path)
        finally:
            os.unlink(path)

        assert len(parsed) == 5
        for src, got in zip(issues, parsed):
            assert got["issue"] == src["issue"]
            assert got["description"] == src["description"]
            assert got["suggestion"] == src["suggestion"]
            assert got["severity"] == src["severity"].lower()
            assert got["file_path"] == src["file_path"]
            assert got["function_name"] == src["function_name"]
            assert got["line_number"] == src["line_number"]
            if src["evidence"]:
                assert got.get("evidence") == src["evidence"]
            else:
                assert "evidence" not in got


class TestOptionalEvidence:
    def test_block_without_evidence_omits_key(self):
        issue = {
            "issue": "No evidence here",
            "description": "Just an impact.",
            "evidence": "",
            "suggestion": "Do better.",
            "severity": "low",
            "category": "style",
            "file_path": "a.py",
            "function_name": "f",
            "line_number": "1",
        }
        path = _write_text(_format_dump([issue]))
        try:
            parsed = parse_issues_text_file(path)
        finally:
            os.unlink(path)

        assert len(parsed) == 1
        assert "evidence" not in parsed[0]


class TestMalformedInput:
    def test_missing_severity_raises(self):
        block = (
            "Title:\nMy issue\n\n"
            # Severity intentionally omitted
            "Category: bugs\n"
            "File: a.py\n"
            "Function: f\n"
            "Lines: 1\n\n"
            "Impact:\nbad\n\n"
            "Potential Solution:\nfix it\n"
        )
        path = _write_text(block)
        try:
            with pytest.raises(ValueError) as exc_info:
                parse_issues_text_file(path)
        finally:
            os.unlink(path)
        assert "Severity" in str(exc_info.value)
        assert "Block 0" in str(exc_info.value)

    def test_missing_function_in_second_block_reports_index(self):
        good = _format_issue({
            "issue": "First",
            "description": "ok",
            "evidence": "",
            "suggestion": "fix",
            "severity": "high",
            "category": "perf",
            "file_path": "a.py",
            "function_name": "f",
            "line_number": "1",
        })
        bad = (
            "Title:\nSecond\n\n"
            "Severity: high\n"
            "Category: perf\n"
            "File: b.py\n"
            # Function intentionally omitted
            "Lines: 2\n\n"
            "Impact:\nbad\n\n"
            "Potential Solution:\nfix\n"
        )
        path = _write_text(good + "\n======================\n" + bad)
        try:
            with pytest.raises(ValueError) as exc_info:
                parse_issues_text_file(path)
        finally:
            os.unlink(path)
        assert "Block 1" in str(exc_info.value)
        assert "Function" in str(exc_info.value)


class TestRealFixture:
    def test_fixture_parses(self):
        fixture = Path(__file__).parent / "fixtures" / "copy_all_sample.txt"
        issues = parse_issues_text_file(str(fixture))
        assert len(issues) == 3

        first = issues[0]
        assert first["severity"] == "critical"
        assert first["file_path"] == "src/Network/Client.swift"
        assert first["function_name"] == "sendRequest"
        assert first["line_number"] == "120-145"
        assert "evidence" in first

        last = issues[-1]
        assert last["severity"] == "medium"
        assert last["file_path"] == "src/UI/SettingsView.swift"
        assert "evidence" not in last
        assert last["suggestion"].startswith("Cache the formatter")

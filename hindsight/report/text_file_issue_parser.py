#!/usr/bin/env python3
"""
Parses the text produced by an HTML report's "Copy All" button back into the
issue dict shape consumed by report_generator.generate_html_report.

Format expected (matches the JS template in report_generator.py / enhanced_report_generator.py):

    Title:
    {issue}

    Severity: {severity}
    Category: {category}
    File: {file_path}
    Function: {function_name}
    Lines: {line_number}

    Impact:
    {description}

    Evidence:                 (optional — block omitted when issue had no evidence)
    {evidence}

    Potential Solution:
    {suggestion}
    ======================
"""

from __future__ import annotations

import re
from typing import List, Dict


# Section headers in the order they appear in a well-formed block. "Evidence"
# is optional. The parser walks these headers in sequence so that multi-line
# bodies are preserved verbatim.
_SECTION_HEADERS = [
    "Title:",
    "Severity:",
    "Category:",
    "File:",
    "Function:",
    "Lines:",
    "Impact:",
    "Evidence:",
    "Potential Solution:",
]

_REQUIRED_HEADERS = [h for h in _SECTION_HEADERS if h != "Evidence:"]

_BLOCK_SEPARATOR = "======================"

# A header is recognized only when it appears at the start of a line. Inline
# fields (Severity/Category/File/Function/Lines) carry their value on the same
# line; multi-line fields (Title/Impact/Evidence/Potential Solution) put the
# value on subsequent lines.
_HEADER_RE = re.compile(
    r"^(?P<header>" + "|".join(re.escape(h) for h in _SECTION_HEADERS) + r")\s?(?P<inline>.*)$"
)


def parse_issues_text_file(path: str) -> List[Dict]:
    """Read a Copy-All text dump and return a list of issue dicts.

    Raises ValueError on malformed input (missing required header in a block).
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = [b.strip() for b in text.split(_BLOCK_SEPARATOR)]
    blocks = [b for b in blocks if b]

    issues: List[Dict] = []
    for index, block in enumerate(blocks):
        issues.append(_parse_block(block, index))
    return issues


def _parse_block(block: str, block_index: int) -> Dict:
    """Parse a single block delimited by ====================== separators."""
    sections = _split_sections(block)

    for required in _REQUIRED_HEADERS:
        if required not in sections:
            raise ValueError(
                f"Block {block_index}: missing required field '{required.rstrip(':')}'"
            )

    severity = sections["Severity:"].strip().lower()
    category = sections["Category:"].strip()
    file_path = sections["File:"].strip()
    function_name = sections["Function:"].strip()
    line_number = sections["Lines:"].strip()

    issue_dict: Dict = {
        "issue": sections["Title:"].strip(),
        "description": sections["Impact:"].strip(),
        "suggestion": sections["Potential Solution:"].strip(),
        "severity": severity,
        "category": category,
        "file_path": file_path,
        "original_file_path": file_path,
        "function_name": function_name,
        "line_number": line_number,
        "external_references": [],
    }

    if "Evidence:" in sections:
        evidence = sections["Evidence:"].strip()
        if evidence:
            issue_dict["evidence"] = evidence

    return issue_dict


def _split_sections(block: str) -> Dict[str, str]:
    """Walk the block line-by-line, splitting on known headers.

    Inline-style headers (e.g. ``Severity: high``) capture the rest of the
    same line. Multi-line headers (``Title:``, ``Impact:``, ``Evidence:``,
    ``Potential Solution:``) capture all subsequent lines until the next
    recognized header.
    """
    sections: Dict[str, List[str]] = {}
    current_header: str | None = None
    current_lines: List[str] = []

    def flush():
        if current_header is not None:
            sections[current_header] = "\n".join(current_lines).rstrip("\n")

    for line in block.splitlines():
        match = _HEADER_RE.match(line)
        if match:
            flush()
            current_header = match.group("header")
            inline = match.group("inline")
            current_lines = [inline] if inline else []
        else:
            if current_header is None:
                # Stray content before the first header — ignore. The Copy All
                # template always starts with ``Title:`` so this is unreachable
                # for well-formed input.
                continue
            current_lines.append(line)

    flush()
    return {h: v for h, v in sections.items()}

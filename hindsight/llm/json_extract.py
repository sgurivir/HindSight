"""String-aware JSON extraction from LLM response text.

Two helpers identical in behavior to the old
`BaseIterativeAnalyzer._find_all_json_objects/_arrays`: scan the text for
balanced `{}` or `[]` regions, parse-test each candidate, return the valid
ones sorted by size (largest first). Braces/brackets inside JSON strings are
ignored so embedded source code does not corrupt the boundary.

The stage-specific extractors in `stages.py` use these to find the FIRST
candidate matching the expected shape (largest first), unlike the legacy
`clean_json_response` which returns the LAST valid JSON in the response.
"""

from __future__ import annotations

import json
from typing import List


def find_all_json_objects(content: str) -> List[str]:
    """Return every balanced JSON object in `content`, largest first.

    Each returned string is guaranteed to parse with `json.loads`. Braces
    inside string literals do not affect matching.
    """
    return _scan(content, open_char="{", close_char="}")


def find_all_json_arrays(content: str) -> List[str]:
    """Return every balanced JSON array in `content`, largest first.

    Each returned string is guaranteed to parse with `json.loads`. Brackets
    inside string literals do not affect matching.
    """
    return _scan(content, open_char="[", close_char="]")


def _scan(content: str, *, open_char: str, close_char: str) -> List[str]:
    candidates: list[str] = []
    n = len(content)
    for i, ch in enumerate(content):
        if ch != open_char:
            continue
        depth = 1
        in_string = False
        j = i + 1
        while j < n:
            c = content[j]
            if in_string:
                if c == "\\":
                    j += 2
                    continue
                if c == '"':
                    in_string = False
            else:
                if c == '"':
                    in_string = True
                elif c == open_char:
                    depth += 1
                elif c == close_char:
                    depth -= 1
                    if depth == 0:
                        snippet = content[i:j + 1]
                        try:
                            json.loads(snippet)
                            candidates.append(snippet)
                        except json.JSONDecodeError:
                            pass
                        break
            j += 1
    candidates.sort(key=len, reverse=True)
    return candidates

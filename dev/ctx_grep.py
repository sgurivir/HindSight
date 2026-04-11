#!/usr/bin/env python3
"""
Usage:
  python3 ctx_grep.py "needle" /path/to/bigfile.txt
Notes:
  - Finds first 3 lines containing the substring `needle`
  - Prints up to 20 lines before + the matching line + up to 20 lines after
  - While collecting context, stops early if it encounters an empty line
"""
import sys
from collections import deque

MAX_MATCHES = 3
BEFORE = 20
AFTER = 20

def is_empty(line: str) -> bool:
    return line.strip() == ""

def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <substring> <file>", file=sys.stderr)
        return 2

    needle = sys.argv[1]
    path = sys.argv[2]

    before_buf: deque[tuple[int, str]] = deque(maxlen=BEFORE)

    matches = 0
    pending = None  # dict with keys: before(list), match(tuple), after(list), remain(int), stop_after(bool)

    def flush(block):
        nonlocal matches
        matches += 1
        print(f"\n===== MATCH {matches} @ line {block['match'][0]} =====")
        for ln, s in block["before"]:
            print(f"{ln}: {s}", end="")
        ln, s = block["match"]
        print(f"{ln}: {s}", end="")
        for ln, s in block["after"]:
            print(f"{ln}: {s}", end="")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            # If we're collecting "after" context for a previous match
            if pending is not None:
                # truncate on empty line
                if is_empty(line):
                    flush(pending)
                    pending = None
                    if matches >= MAX_MATCHES:
                        break
                    # reset before buffer at block boundary
                    before_buf.clear()
                    continue

                pending["after"].append((lineno, line))
                pending["remain"] -= 1

                if pending["remain"] <= 0:
                    flush(pending)
                    pending = None
                    if matches >= MAX_MATCHES:
                        break
                # still keep building before_buf as we stream
                before_buf.append((lineno, line))
                continue

            # Not pending: check for a new match
            if needle in line:
                # collect up to 20 lines before, but truncate if empty line appears
                before_lines = list(before_buf)
                # keep only suffix after last empty line
                last_empty_idx = -1
                for i, (_, s) in enumerate(before_lines):
                    if is_empty(s):
                        last_empty_idx = i
                if last_empty_idx != -1:
                    before_lines = before_lines[last_empty_idx + 1 :]

                pending = {
                    "before": before_lines,
                    "match": (lineno, line),
                    "after": [],
                    "remain": AFTER,
                }
                # don't put match line into before_buf (optional); helps keep blocks clean
                continue

            # Normal streaming
            before_buf.append((lineno, line))

    # EOF while pending: flush whatever we collected
    if pending is not None and matches < MAX_MATCHES:
        flush(pending)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())

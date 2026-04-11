#!/usr/bin/env python3
"""
Compare function checksums across two snapshots and report mismatches vs a provided "functions_modified" list.

Inputs:
  - earlier JSON:  {"function_to_location_and_checksum": { fn: [ {file_name,start,end,checksum}, ... ], ... }}
  - later   JSON:  same shape as above
  - modified JSON: EITHER
        {"functions_modified": ["fnA","fnB",...]}  OR
        {"function_to_location_and_checksum": { "fnA": [...], ... }}  OR
        ["fnA","fnB",...]  OR { "fnA": true, "fnB": false, ... }

Output:
  - Lists ALL mismatches:
      * changed-by-checksum BUT NOT in functions_modified  (missing)
      * NOT changed-by-checksum BUT IN functions_modified  (extra)

Usage:
  python3 check_function_mismatches.py \
    --earlier earlier_functions_checksum.json \
    --later   later_functions_checksum.json \
    --modified functions_modified.json \
    [--details]
"""
import argparse, json, sys
from pathlib import Path

def load_json(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def to_func_map(obj):
    """Return dict[str, list[dict]] from an input JSON that may already be in that shape."""
    # Handle flattened structure (direct function mapping)
    if isinstance(obj, dict) and all(isinstance(v, dict) and 'code' in v for v in obj.values()):
        # Convert from new format {func: {checksum: ..., code: [...]}} to old format {func: [...]}
        result = {}
        for func_name, func_info in obj.items():
            result[func_name] = func_info.get('code', [])
        return result
    # fallback: if already {fn: [...]}
    if isinstance(obj, dict) and all(isinstance(v, list) for v in obj.values()):
        return obj
    raise ValueError("Expected flattened function structure {function: {checksum: ..., code: [...]}} or legacy {function: [entries,...]}")

def to_modified_set(obj):
    """Normalize the modified list into a set of function names."""
    # common keys
    if isinstance(obj, dict):
        for k in ("functions_modified", "modified_functions", "functions", "modified"):
            if k in obj and isinstance(obj[k], list):
                return set(obj[k])
        # Handle flattened structure
        if isinstance(obj, dict) and all(isinstance(v, dict) and 'code' in v for v in obj.values()):
            return set(obj.keys())
        # dict of booleans
        boolish = {k for k,v in obj.items() if isinstance(v, (bool,int,str)) and str(v).lower() in ("1","true")}
        if boolish:
            return boolish
    if isinstance(obj, list):
        return set(obj)
    raise ValueError("Could not extract modified functions set from modified JSON.")

def checksum_set(entries):
    """Extract set of checksum strings from a list of entry dicts."""
    cs = set()
    for e in entries or []:
        c = e.get("checksum")
        if c is not None:
            cs.add(str(c))
    return cs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--earlier", required=True, type=Path)
    ap.add_argument("--later", required=True, type=Path)
    ap.add_argument("--modified", required=True, type=Path)
    ap.add_argument("--details", action="store_true", help="Print added/removed checksum details for mismatches")
    args = ap.parse_args()

    earlier = to_func_map(load_json(args.earlier))
    later   = to_func_map(load_json(args.later))
    declared = to_modified_set(load_json(args.modified))

    all_funcs = set(earlier.keys()) | set(later.keys())

    changed_by_checksum = set()
    checksum_diffs = {}  # fn -> (added, removed)
    for fn in all_funcs:
        e_cs = checksum_set(earlier.get(fn, []))
        l_cs = checksum_set(later.get(fn, []))
        if e_cs != l_cs:
            changed_by_checksum.add(fn)
            checksum_diffs[fn] = (sorted(l_cs - e_cs), sorted(e_cs - l_cs))

    # Mismatches
    missing_in_declared = sorted(changed_by_checksum - declared)
    extra_in_declared   = sorted(declared - changed_by_checksum)

    ok = True
    if missing_in_declared:
        ok = False
        print("MISMATCH: Changed-by-checksum but NOT in functions_modified:")
        for fn in missing_in_declared:
            print(f"  - {fn}")
            if args.details:
                added, removed = checksum_diffs.get(fn, ([],[]))
                print(f"      checksums +{len(added)} / -{len(removed)}")
        print()

    if extra_in_declared:
        ok = False
        print("MISMATCH: Listed in functions_modified but checksum did NOT change:")
        for fn in extra_in_declared:
            print(f"  - {fn}")
            if args.details:
                e_cs = checksum_set(earlier.get(fn, []))
                l_cs = checksum_set(later.get(fn, []))
                added = sorted(l_cs - e_cs); removed = sorted(e_cs - l_cs)
                print(f"      checksums +{len(added)} / -{len(removed)}")
        print()

    if ok:
        print("All good: declared functions_modified matches checksum-based changes.")

    # Exit non-zero if mismatches
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()

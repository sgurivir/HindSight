"""Tests for hindsight.core.call_tree.root_selector."""

import os
import tempfile
import unittest

from hindsight.core.call_tree import CallTreeBuilder, RootSelector


def _make_func_entry(name, file, start, end, callees=None, callers=None):
    return {
        "function": name,
        "context": {"file": file, "start": start, "end": end},
        "functions_invoked": [{"function": c} for c in (callees or [])],
        "invoked_by": list(callers or []),
        "data_types_used": [],
        "constants_used": {},
    }


def _write(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    os.makedirs(os.path.dirname(path) or tmpdir, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


class RootSelectorTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="rstest_")
        # Chain a -> b -> c, plus standalone d, plus standalone utility e.
        _write(self.tmpdir, "x.py", "def a():\n    b()\ndef b():\n    c()\ndef c():\n    pass\ndef d():\n    pass\ndef e():\n    pass\n")
        self.graph = [{
            "file": "x.py",
            "functions": [
                _make_func_entry("a", "x.py", 1, 2, callees=["b"]),
                _make_func_entry("b", "x.py", 3, 4, callees=["c"], callers=["a"]),
                _make_func_entry("c", "x.py", 5, 6, callers=["b"]),
                _make_func_entry("d", "x.py", 7, 8, callees=["c"]),
                _make_func_entry("e", "x.py", 9, 10),
            ],
        }]
        # Ensure c records both callers.
        for fn in self.graph[0]["functions"]:
            if fn["function"] == "c":
                fn["invoked_by"] = ["b", "d"]
        self.builder = CallTreeBuilder(self.graph, self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_select_for_code_keeps_only_top_callers(self):
        selector = RootSelector(self.builder)
        result = selector.select_for_code(["a", "b", "c"])
        # a has no in-set callers → root. b has a as caller → excluded. c has b as caller → excluded.
        self.assertEqual(result.roots, ["a"])
        self.assertIn("b", result.excluded_as_callee)
        self.assertIn("c", result.excluded_as_callee)
        self.assertEqual(result.covered, {"a", "b", "c"})

    def test_select_for_code_drops_isolated_orphan(self):
        # 'e' has no callers and no callees — pure orphan utility.
        selector = RootSelector(self.builder)
        result = selector.select_for_code(["e"])
        self.assertEqual(result.roots, [])
        self.assertEqual(result.skipped_orphans, ["e"])

    def test_select_for_code_keeps_caller_with_callees_even_if_no_in_set_callee(self):
        # 'd' has no callers and one out-of-set callee (c not in set). Keep it.
        selector = RootSelector(self.builder)
        result = selector.select_for_code(["d"])
        self.assertEqual(result.roots, ["d"])
        self.assertEqual(result.skipped_orphans, [])

    def test_select_for_diff_keeps_isolated_modified(self):
        # In diff mode, isolated modified functions are kept (will be single-node trees).
        selector = RootSelector(self.builder)
        result = selector.select_for_diff(["e"])
        self.assertEqual(result.roots, ["e"])
        self.assertEqual(result.skipped_orphans, [])

    def test_select_for_diff_picks_highest_modified_ancestor(self):
        # Affected: b and c. b is the highest modified — root = b. c is excluded.
        selector = RootSelector(self.builder)
        result = selector.select_for_diff(["b", "c"])
        self.assertEqual(result.roots, ["b"])
        self.assertIn("c", result.excluded_as_callee)

    def test_unknown_functions_silently_ignored(self):
        selector = RootSelector(self.builder)
        result = selector.select_for_code(["a", "unknown_fn"])
        self.assertEqual(result.roots, ["a"])

    def test_multiple_disconnected_roots(self):
        # a is an in-set top caller; d is also an in-set top caller (different chain).
        selector = RootSelector(self.builder)
        result = selector.select_for_code(["a", "d"])
        self.assertEqual(sorted(result.roots), ["a", "d"])


if __name__ == "__main__":
    unittest.main()

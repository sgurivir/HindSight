"""Tests for hindsight.core.call_tree.call_tree_builder."""

import os
import tempfile
import unittest

from hindsight.core.call_tree import CallTree, CallTreeBuilder, CallTreeNode


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_func_entry(name: str, file: str, start: int, end: int, callees=None, callers=None):
    return {
        "function": name,
        "context": {"file": file, "start": start, "end": end},
        "functions_invoked": [
            {"function": c} if isinstance(c, str) else c
            for c in (callees or [])
        ],
        "invoked_by": list(callers or []),
        "data_types_used": [],
        "constants_used": {},
    }


class CallTreeBuilderTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="cttest_")
        # File 'mod.py' with three functions stacked vertically.
        _write_file(
            os.path.join(self.tmpdir, "mod.py"),
            "\n".join([
                "def root():",         # line 1
                "    mid_a()",          # line 2
                "    mid_b()",          # line 3
                "",                     # line 4
                "def mid_a():",        # line 5
                "    leaf()",          # line 6
                "",                     # line 7
                "def mid_b():",        # line 8
                "    leaf()",          # line 9
                "",                     # line 10
                "def leaf():",          # line 11
                "    pass",             # line 12
                "",                     # line 13
            ])
        )
        self.graph = [{
            "file": "mod.py",
            "functions": [
                _make_func_entry("root", "mod.py", 1, 3, callees=["mid_a", "mid_b"]),
                _make_func_entry("mid_a", "mod.py", 5, 6, callees=["leaf"], callers=["root"]),
                _make_func_entry("mid_b", "mod.py", 8, 9, callees=["leaf"], callers=["root"]),
                _make_func_entry("leaf", "mod.py", 11, 12, callers=["mid_a", "mid_b"]),
            ],
        }]

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_builds_simple_tree(self):
        builder = CallTreeBuilder(self.graph, self.tmpdir)
        tree = builder.build("root")
        self.assertIsNotNone(tree)
        names = [n.function for n in tree.nodes]
        self.assertEqual(names[0], "root")
        # All four functions should appear; leaf is shared but appears once.
        self.assertEqual(set(names), {"root", "mid_a", "mid_b", "leaf"})
        # leaf has two callers in the graph — shared callee dedup means it
        # appears exactly once in the tree.
        self.assertEqual(names.count("leaf"), 1)

    def test_root_records_callers_as_out_of_tree(self):
        # Add a caller of root that is NOT in the tree.
        self.graph[0]["functions"].append(
            _make_func_entry("entry", "mod.py", 14, 15, callees=["root"])
        )
        # Reflect the new edge in root's invoked_by.
        for fn in self.graph[0]["functions"]:
            if fn["function"] == "root":
                fn["invoked_by"] = ["entry"]
        builder = CallTreeBuilder(self.graph, self.tmpdir)
        tree = builder.build("root")
        root_node = tree.nodes[0]
        self.assertIn("entry", root_node.out_of_tree_callers)

    def test_cycle_marked_as_out_of_tree(self):
        # Make leaf call root → cycle.
        for fn in self.graph[0]["functions"]:
            if fn["function"] == "leaf":
                fn["functions_invoked"] = [{"function": "root"}]
        builder = CallTreeBuilder(self.graph, self.tmpdir)
        tree = builder.build("root")
        # The back-edge from leaf -> root must NOT create a duplicate root node.
        names = [n.function for n in tree.nodes]
        self.assertEqual(names.count("root"), 1)
        leaf_node = next(n for n in tree.nodes if n.function == "leaf")
        self.assertIn("root", leaf_node.callees_out_of_tree)

    def test_depth_cap_stubs_deep_nodes(self):
        builder = CallTreeBuilder(self.graph, self.tmpdir, max_depth=1)
        tree = builder.build("root")
        # leaf is at depth 2, beyond max_depth=1 → should be stubbed.
        leaf_node = next(n for n in tree.nodes if n.function == "leaf")
        self.assertTrue(leaf_node.is_stub())
        self.assertEqual(leaf_node.source_omitted_reason, "exceeds_max_depth")
        self.assertTrue(tree.truncated_at_depth)

    def test_char_cap_stubs_remaining_nodes(self):
        # Tight budget: root source alone fits, others should be stubbed.
        builder = CallTreeBuilder(self.graph, self.tmpdir, max_chars=50)
        tree = builder.build("root")
        # Root is always inlined.
        self.assertTrue(tree.nodes[0].source)
        # At least one non-root must be stubbed under this budget.
        stubbed = tree.stubbed_function_names()
        self.assertGreater(len(stubbed), 0)
        self.assertTrue(tree.truncated_at_chars)

    def test_node_cap(self):
        builder = CallTreeBuilder(self.graph, self.tmpdir, max_nodes=2)
        tree = builder.build("root")
        self.assertLessEqual(tree.node_count(), 2)
        self.assertTrue(tree.truncated_at_nodes)

    def test_tree_signature_changes_with_checksum(self):
        builder = CallTreeBuilder(self.graph, self.tmpdir)
        t1 = builder.build("root")
        sig1 = t1.tree_signature
        # Modify leaf's body.
        _write_file(
            os.path.join(self.tmpdir, "mod.py"),
            "\n".join([
                "def root():",
                "    mid_a()",
                "    mid_b()",
                "",
                "def mid_a():",
                "    leaf()",
                "",
                "def mid_b():",
                "    leaf()",
                "",
                "def leaf():",
                "    return 1",  # changed body
                "",
            ])
        )
        t2 = builder.build("root")
        self.assertNotEqual(sig1, t2.tree_signature)

    def test_missing_root_returns_none(self):
        builder = CallTreeBuilder(self.graph, self.tmpdir)
        self.assertIsNone(builder.build("nonexistent"))

    def test_to_dict_shape(self):
        builder = CallTreeBuilder(self.graph, self.tmpdir)
        tree = builder.build("root")
        d = tree.to_dict()
        self.assertEqual(d["schema_version"], "2.0")
        self.assertEqual(d["root"]["function"], "root")
        self.assertIn("nodes", d)
        self.assertIn("truncation", d)
        self.assertIn("stats", d)
        self.assertEqual(d["stats"]["node_count"], tree.node_count())


if __name__ == "__main__":
    unittest.main()

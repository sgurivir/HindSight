#!/usr/bin/env python3
"""
Call-tree builder.

Builds a deterministic ``CallTree`` (root function + reachable callees, plus
data types and constants used) from the repo's nested call graph and the
repo source on disk. The result is fed directly to an LLM as the analysis
unit — one tree per LLM run — replacing the legacy "one function at a time"
pipeline.

Key design points:

- BFS layer-by-layer assembly. Root and its direct callees are inlined first,
  then progressively deeper layers, until ``max_chars`` or ``max_nodes`` is
  reached. Nodes beyond that point are emitted as STUBS (no ``source``, just
  location metadata) so the LLM still sees the topology and can fetch the
  body on demand via ``readFile`` / ``getFileContentByLines``.

- Hard ``max_depth`` cap. Layers below this depth are stubbed regardless of
  remaining budget.

- Cycle-safe: a node is visited at most once; revisits become ``back_edge``
  markers on the parent node so the LLM doesn't infinitely chase recursion.

- Pure orchestration — no LLM calls. All data comes from the call graph and
  the source files on disk.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ..constants import (
    CALL_TREE_ANALYSIS_MAX_CHARS,
    CALL_TREE_ANALYSIS_MAX_DEPTH,
    CALL_TREE_ANALYSIS_MAX_NODES,
)
from ...utils.hash_util import HashUtil
from ...utils.log_util import get_logger

logger = get_logger(__name__)


@dataclass
class CallTreeNode:
    """A single function in a call tree.

    ``source`` is populated when the node is inlined within budget; when the
    char/node/depth caps push the body out of the prompt, ``source`` is left
    empty and ``source_omitted_reason`` is set. The LLM can still read the
    body using its tools because ``file``, ``start_line``, ``end_line`` are
    always present.
    """

    function: str
    file: str
    start_line: int
    end_line: int
    depth: int                              # 0 for root, +1 per layer
    parent: Optional[str]                   # name of the parent in this tree (None for root)
    source: str = ""                        # numbered source lines (empty when stubbed)
    source_omitted_reason: Optional[str] = None
    checksum: str = ""
    callees_in_tree: List[str] = field(default_factory=list)
    callees_out_of_tree: List[str] = field(default_factory=list)  # callees skipped (back-edge, cap, etc.)
    out_of_tree_callers: List[str] = field(default_factory=list)  # callers known to call graph but not in this tree
    data_types: List[str] = field(default_factory=list)
    constants: List[str] = field(default_factory=list)
    back_edge: bool = False                 # this node is a recursion back-edge marker (no children expanded)

    def is_stub(self) -> bool:
        return not self.source

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "function": self.function,
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "depth": self.depth,
            "parent": self.parent,
            "checksum": self.checksum,
            "callees_in_tree": self.callees_in_tree,
        }
        if self.source:
            out["source"] = self.source
        else:
            out["source_omitted_reason"] = self.source_omitted_reason or "exceeds_budget"
        if self.callees_out_of_tree:
            out["callees_out_of_tree"] = self.callees_out_of_tree
        if self.out_of_tree_callers:
            out["out_of_tree_callers"] = self.out_of_tree_callers
        if self.data_types:
            out["data_types"] = self.data_types
        if self.constants:
            out["constants"] = self.constants
        if self.back_edge:
            out["back_edge"] = True
        return out


@dataclass
class CallTree:
    """A complete call tree centered on ``root``."""

    root: str
    root_file: str
    root_checksum: str
    nodes: List[CallTreeNode]                      # always includes the root at index 0
    truncated_at_depth: bool = False
    truncated_at_chars: bool = False
    truncated_at_nodes: bool = False
    total_chars: int = 0
    tree_signature: str = ""                       # MD5 over (sorted (function, checksum)) — detects subtree drift

    def node_count(self) -> int:
        return len(self.nodes)

    def stubbed_function_names(self) -> List[str]:
        return [n.function for n in self.nodes if n.is_stub() and not n.back_edge]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "2.0",
            "root": {
                "function": self.root,
                "file": self.root_file,
                "checksum": self.root_checksum,
            },
            "nodes": [n.to_dict() for n in self.nodes],
            "truncation": {
                "depth_cap_hit": self.truncated_at_depth,
                "char_cap_hit": self.truncated_at_chars,
                "node_cap_hit": self.truncated_at_nodes,
                "stubbed_nodes": self.stubbed_function_names(),
            },
            "stats": {
                "node_count": self.node_count(),
                "total_chars": self.total_chars,
                "tree_signature": self.tree_signature,
            },
        }


class CallTreeBuilder:
    """Builds ``CallTree`` instances from a nested call graph and repo sources.

    The builder is intentionally stateless w.r.t. a particular tree: one
    instance can build many trees from the same indices. Indices are computed
    once up front from the call graph data.
    """

    def __init__(
        self,
        nested_call_graph: List[Dict[str, Any]],
        repo_path: str,
        max_depth: int = CALL_TREE_ANALYSIS_MAX_DEPTH,
        max_chars: int = CALL_TREE_ANALYSIS_MAX_CHARS,
        max_nodes: int = CALL_TREE_ANALYSIS_MAX_NODES,
    ):
        self.repo_path = repo_path
        self.max_depth = max_depth
        self.max_chars = max_chars
        self.max_nodes = max_nodes

        # function name -> raw call-graph entry (with context, functions_invoked,
        # invoked_by, data_types_used, constants_used)
        self._index_by_name: Dict[str, Dict[str, Any]] = {}
        # function name -> set of caller names (from invoked_by)
        self._callers: Dict[str, Set[str]] = {}
        # function name -> ordered list of callee names (from functions_invoked)
        self._callees: Dict[str, List[str]] = {}

        self._build_indices(nested_call_graph)

        logger.info(
            "CallTreeBuilder ready: %d functions indexed, max_depth=%d, max_chars=%d, max_nodes=%d",
            len(self._index_by_name), max_depth, max_chars, max_nodes,
        )

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    def _build_indices(self, nested_call_graph: List[Dict[str, Any]]) -> None:
        if not isinstance(nested_call_graph, list):
            logger.warning("CallTreeBuilder: nested_call_graph is not a list (%s)", type(nested_call_graph))
            return

        for file_entry in nested_call_graph:
            if not isinstance(file_entry, dict):
                continue
            for func_entry in file_entry.get("functions", []) or []:
                if not isinstance(func_entry, dict):
                    continue
                name = func_entry.get("function") or ""
                if not name:
                    continue
                # Ensure each entry has a file in its context — fall back to file_entry.
                ctx = func_entry.setdefault("context", {})
                if not ctx.get("file"):
                    ctx["file"] = file_entry.get("file", "")
                # Last write wins for duplicate names — call graph is expected to be deduped.
                self._index_by_name[name] = func_entry

                callees = []
                for invoked in func_entry.get("functions_invoked", []) or []:
                    if isinstance(invoked, str) and invoked:
                        callees.append(invoked)
                    elif isinstance(invoked, dict):
                        n = invoked.get("function", "")
                        if n:
                            callees.append(n)
                self._callees[name] = callees

                callers: Set[str] = set()
                for inv_by in func_entry.get("invoked_by", []) or []:
                    if isinstance(inv_by, str) and inv_by:
                        callers.add(inv_by)
                    elif isinstance(inv_by, dict):
                        n = inv_by.get("function", "")
                        if n:
                            callers.add(n)
                # Merge (don't overwrite) in case the same function appears in multiple file entries.
                self._callers.setdefault(name, set()).update(callers)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def function_names(self) -> Set[str]:
        return set(self._index_by_name.keys())

    def get_callers(self, name: str) -> Set[str]:
        return set(self._callers.get(name, set()))

    def get_callees(self, name: str) -> List[str]:
        return list(self._callees.get(name, []))

    def has_function(self, name: str) -> bool:
        return name in self._index_by_name

    def build(self, root: str) -> Optional[CallTree]:
        """Build a CallTree rooted at ``root``. Returns None if root is unknown."""
        root_entry = self._index_by_name.get(root)
        if not root_entry:
            logger.warning("CallTreeBuilder: root function %r not in index", root)
            return None

        root_ctx = root_entry.get("context", {}) or {}
        root_file = root_ctx.get("file", "")
        root_start = int(root_ctx.get("start") or 0)
        root_end = int(root_ctx.get("end") or 0)

        root_source = self._read_function_source(root_file, root_start, root_end)
        root_checksum = HashUtil.checksum_for_function_source(
            self.repo_path, root_file, root_start, root_end
        )

        root_node = CallTreeNode(
            function=root,
            file=root_file,
            start_line=root_start,
            end_line=root_end,
            depth=0,
            parent=None,
            source=root_source,
            checksum=root_checksum,
            callees_in_tree=[],
            data_types=list(root_entry.get("data_types_used", []) or []),
            constants=self._extract_constant_names(root_entry.get("constants_used", {})),
            out_of_tree_callers=sorted(self._callers.get(root, set())),
        )

        nodes: List[CallTreeNode] = [root_node]
        visited: Set[str] = {root}
        total_chars = len(root_source)

        truncated_at_depth = False
        truncated_at_chars = False
        truncated_at_nodes = False

        # BFS queue of nodes whose CHILDREN we still need to consider.
        queue: deque = deque()
        queue.append(root_node)

        while queue:
            parent_node = queue.popleft()
            parent_name = parent_node.function

            # Don't expand children past the depth cap — but we still record
            # them as stubs so the LLM sees what's there.
            child_depth = parent_node.depth + 1
            past_depth_cap = child_depth > self.max_depth
            if past_depth_cap:
                truncated_at_depth = True

            for callee_name in self._callees.get(parent_name, []):
                if callee_name in visited:
                    # Cycle / shared callee — mark on parent, don't re-expand.
                    if callee_name not in parent_node.callees_in_tree:
                        parent_node.callees_out_of_tree.append(callee_name)
                    continue

                callee_entry = self._index_by_name.get(callee_name)
                if not callee_entry:
                    # Callee not in our index (framework/external) — surface to parent.
                    parent_node.callees_out_of_tree.append(callee_name)
                    continue

                visited.add(callee_name)

                # Node cap is absolute — if we've already inlined the max,
                # everything after this point becomes a back-edge / out-of-tree
                # marker rather than a full node.
                if len(nodes) >= self.max_nodes:
                    truncated_at_nodes = True
                    parent_node.callees_out_of_tree.append(callee_name)
                    continue

                ctx = callee_entry.get("context", {}) or {}
                file = ctx.get("file", "")
                start = int(ctx.get("start") or 0)
                end = int(ctx.get("end") or 0)

                # Decide whether to inline source for this node.
                source = ""
                omit_reason: Optional[str] = None

                if past_depth_cap:
                    omit_reason = "exceeds_max_depth"
                else:
                    candidate_source = self._read_function_source(file, start, end)
                    candidate_len = len(candidate_source)
                    if total_chars + candidate_len > self.max_chars:
                        # Don't inline — record a stub so the LLM still sees it.
                        omit_reason = "exceeds_char_budget"
                        truncated_at_chars = True
                    else:
                        source = candidate_source
                        total_chars += candidate_len

                checksum = HashUtil.checksum_for_function_source(
                    self.repo_path, file, start, end
                )

                child_node = CallTreeNode(
                    function=callee_name,
                    file=file,
                    start_line=start,
                    end_line=end,
                    depth=child_depth,
                    parent=parent_name,
                    source=source,
                    source_omitted_reason=omit_reason,
                    checksum=checksum,
                    callees_in_tree=[],
                    data_types=list(callee_entry.get("data_types_used", []) or []),
                    constants=self._extract_constant_names(callee_entry.get("constants_used", {})),
                    out_of_tree_callers=sorted(
                        c for c in self._callers.get(callee_name, set()) if c != parent_name
                    ),
                )
                nodes.append(child_node)
                parent_node.callees_in_tree.append(callee_name)

                # Only continue descending when the body is actually inlined.
                # Stubbed nodes still appear, but we don't enqueue their children —
                # otherwise we'd keep expanding stubs without ever showing code,
                # which buys us nothing and wastes the node cap.
                if source and not past_depth_cap:
                    queue.append(child_node)

        tree_sig = self._compute_tree_signature(nodes)

        tree = CallTree(
            root=root,
            root_file=root_file,
            root_checksum=root_checksum,
            nodes=nodes,
            truncated_at_depth=truncated_at_depth,
            truncated_at_chars=truncated_at_chars,
            truncated_at_nodes=truncated_at_nodes,
            total_chars=total_chars,
            tree_signature=tree_sig,
        )

        logger.info(
            "Built call tree for %r: %d nodes (%d stubbed), %d chars, depth_cap=%s char_cap=%s node_cap=%s",
            root, tree.node_count(), len(tree.stubbed_function_names()),
            total_chars, truncated_at_depth, truncated_at_chars, truncated_at_nodes,
        )
        return tree

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_function_source(self, file_path: str, start_line: int, end_line: int) -> str:
        """Read function source with 1-based line numbers prepended.

        Returns "" on any failure — the caller treats that as a stub.
        """
        if not file_path or start_line <= 0 or end_line < start_line:
            return ""
        full_path = os.path.join(self.repo_path, file_path)
        if not os.path.isfile(full_path):
            return ""
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as e:
            logger.debug("CallTreeBuilder: could not read %s: %s", full_path, e)
            return ""
        if start_line > len(lines):
            return ""
        end = min(end_line, len(lines))
        body_lines = lines[start_line - 1:end]
        return "\n".join(
            f"{start_line + i:5d} | {line.rstrip()}"
            for i, line in enumerate(body_lines)
        )

    @staticmethod
    def _extract_constant_names(constants_used: Any) -> List[str]:
        """Constants are stored as ``{name: value}`` in the call graph. We only
        carry the names through — the LLM can read the value with a tool if it
        actually matters."""
        if isinstance(constants_used, dict):
            return sorted(str(k) for k in constants_used.keys())
        if isinstance(constants_used, list):
            return [str(x) for x in constants_used]
        return []

    @staticmethod
    def _compute_tree_signature(nodes: Iterable[CallTreeNode]) -> str:
        """MD5 over the sorted (name, checksum) tuples in the tree. Used by the
        cache to detect whether the tree drifted under the same root."""
        pairs: List[Tuple[str, str]] = sorted(
            (n.function, n.checksum or "") for n in nodes
        )
        joined = "|".join(f"{f}:{c}" for f, c in pairs)
        return HashUtil.hash_for_content_md5(joined)

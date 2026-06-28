#!/usr/bin/env python3
"""
Root selector for call-tree analysis.

Given a builder (which has the full caller/callee index) and a set of
"interesting" functions, returns the minimal set of *filter-relative roots* —
the highest in-set caller along each chain. Every interesting function ends up
inside the subtree of exactly one of these roots.

Two modes:

- ``select_for_code()`` — code analyzer. Interesting set = functions that pass
  the user's directory / file / function filters. Functions that pass the
  filter but are reachable as a callee of another in-filter function are NOT
  roots; they'll be analyzed inside the larger tree.

- ``select_for_diff()`` — diff analyzer. Interesting set = affected (modified)
  functions. Same logic: walk up the *modified*-only caller chain to the
  highest modified ancestor; that's the root. Unmodified callers don't become
  roots (we never analyze code the user didn't touch as a target — they only
  appear inside trees as supporting context for a modified callee).

"Uncalled utility code" — a function that is in the interesting set, has no
in-set callers, AND has no in-set callees — is silently skipped (logged).
This matches the explicit user direction: don't analyze callee-only utilities
no one in the analysis scope calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Set

from ...utils.log_util import get_logger
from .call_tree_builder import CallTreeBuilder

logger = get_logger(__name__)


@dataclass
class RootSelectionResult:
    roots: List[str]                    # ordered list of selected root function names
    covered: Set[str]                   # functions in the interesting set covered by some root's subtree
    skipped_orphans: List[str]          # interesting functions with no in-set caller AND no in-set callee
    excluded_as_callee: Set[str]        # interesting functions that became non-root because an in-set caller covered them


class RootSelector:
    """Picks filter-relative roots for the call-tree pipeline."""

    def __init__(self, builder: CallTreeBuilder):
        self._builder = builder

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_for_code(self, interesting: Iterable[str]) -> RootSelectionResult:
        """Roots for the code analyzer.

        A function is a root iff it is in ``interesting`` AND none of its
        callers are in ``interesting``. Orphans (no in-set caller, no in-set
        callee reachable) are dropped — they are isolated utility-like nodes
        with no chain to anchor the analysis to.
        """
        return self._select(interesting, drop_orphans=True)

    def select_for_diff(self, affected: Iterable[str]) -> RootSelectionResult:
        """Roots for the diff analyzer.

        Same rule as ``select_for_code`` — the highest *affected* function in
        any chain is the root. Affected nodes with no affected caller AND no
        affected callee are kept (they are isolated modified functions that
        still must be analyzed); they become single-node trees that the LLM
        will expand by reading callers/callees through tools if needed.
        """
        return self._select(affected, drop_orphans=False)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select(self, interesting: Iterable[str], drop_orphans: bool) -> RootSelectionResult:
        in_set: Set[str] = {f for f in interesting if self._builder.has_function(f)}
        unknown = {f for f in interesting if not self._builder.has_function(f)}
        if unknown:
            logger.info(
                "RootSelector: %d input function(s) not in call graph index, ignored: %s",
                len(unknown), sorted(list(unknown))[:5],
            )

        if not in_set:
            return RootSelectionResult(roots=[], covered=set(), skipped_orphans=[], excluded_as_callee=set())

        # A node is a root if no in-set caller exists (in the call graph's
        # caller index). We don't need to recurse upward — the call graph
        # already encodes that.
        roots: List[str] = []
        excluded_as_callee: Set[str] = set()

        for fn in sorted(in_set):
            callers = self._builder.get_callers(fn)
            in_set_callers = callers & in_set
            if in_set_callers:
                excluded_as_callee.add(fn)
            else:
                roots.append(fn)

        # Cover-set: walk each root's subtree (in the FULL call graph, not just
        # within ``interesting``) and intersect with ``in_set`` to see who is
        # actually covered. Tells us which interesting functions never appear in
        # any analyzed tree.
        covered: Set[str] = set()
        for root in roots:
            covered |= self._reachable(root) & in_set

        skipped_orphans: List[str] = []
        if drop_orphans:
            filtered_roots: List[str] = []
            for root in roots:
                reachable = self._reachable(root)
                has_in_set_callee = bool((reachable - {root}) & in_set)
                # If this root has no callers in the interesting set (it
                # wouldn't be a root otherwise) AND no callees in the
                # interesting set, it's an isolated utility node — drop.
                if not has_in_set_callee:
                    # But: keep if it has ANY callees at all that the analysis
                    # could reach — a top-level function with no in-set callees
                    # but real callees still has analyzable downstream code.
                    # That's the case the user is keeping (orphan utility with
                    # NO callers in scope is the only thing we silently skip).
                    if not self._builder.get_callees(root):
                        skipped_orphans.append(root)
                        continue
                filtered_roots.append(root)
            roots = filtered_roots

        logger.info(
            "RootSelector: interesting=%d, roots=%d, excluded_as_callee=%d, orphans_skipped=%d, covered=%d/%d",
            len(in_set), len(roots), len(excluded_as_callee), len(skipped_orphans),
            len(covered), len(in_set),
        )

        return RootSelectionResult(
            roots=roots,
            covered=covered,
            skipped_orphans=skipped_orphans,
            excluded_as_callee=excluded_as_callee,
        )

    def _reachable(self, start: str) -> Set[str]:
        """Return all functions reachable from ``start`` through callee edges,
        cycle-safe. Used to compute cover-sets for root selection — independent
        of the budget caps in the builder (we want the topological cover, not
        what fits in a prompt)."""
        seen: Set[str] = {start}
        stack: List[str] = [start]
        while stack:
            current = stack.pop()
            for callee in self._builder.get_callees(current):
                if callee in seen:
                    continue
                if not self._builder.has_function(callee):
                    continue
                seen.add(callee)
                stack.append(callee)
        return seen

"""
Call-tree-at-once analysis package.

Builds deterministic call trees (root + reachable callees) from a repo's call
graph and selects which roots to analyze. Pairs with the LLM call-tree analysis
stage that examines an entire tree in one prompt and reports cross-function
defects that propagate up to a caller.
"""

from .call_tree_builder import CallTree, CallTreeBuilder, CallTreeNode
from .root_selector import RootSelector

__all__ = ["CallTree", "CallTreeBuilder", "CallTreeNode", "RootSelector"]

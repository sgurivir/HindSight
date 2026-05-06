#!/usr/bin/env python3
"""
Code Navigation MCP Server — provides tools for LLM-driven code exploration.

Built on fastmcp. Exposes tools that allow an LLM to navigate the call graph,
read function bodies, find references, and inspect the AST structure of a repo.

Usage:
    server = CodeNavigationServer(repo_path, call_graph_data, implementations)
    # The server's tool functions can be called directly (in-process MCP style)
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Set

from fastmcp import FastMCP

from ..lang_util.call_graph_util import CallGraph, load_call_graph_from_json
from ..lang_util.call_tree_util import extract_implementations
from ...utils.log_util import get_logger

logger = get_logger(__name__)


class CodeNavigationServer:
    """
    In-process MCP server providing code navigation tools.

    Rather than running as a network service, this class exposes tool methods
    that can be called directly by the external input analyzer. The FastMCP
    instance is used to define the tool schemas and provide a standard interface.
    """

    def __init__(
        self,
        repo_path: str,
        call_graph_data: Any,
        implementations: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        graph: Optional[CallGraph] = None
    ):
        self.repo_path = repo_path
        self._raw_data = call_graph_data

        if graph is not None:
            self.graph = graph
        else:
            self.graph = load_call_graph_from_json(call_graph_data)

        if implementations is not None:
            self.implementations = implementations
        else:
            self.implementations = extract_implementations(call_graph_data)

        # Build symbol index: function_name -> list of locations
        self._symbol_index: Dict[str, List[Dict[str, Any]]] = self.implementations

        # Build file -> functions index
        self._file_functions: Dict[str, List[str]] = {}
        for func_name, locations in self._symbol_index.items():
            for loc in locations:
                fp = loc.get("file_path", "")
                if fp:
                    self._file_functions.setdefault(fp, []).append(func_name)

        # Create FastMCP instance with tool definitions
        self.mcp = FastMCP("code-navigation")
        self._register_tools()

    def _register_tools(self) -> None:
        """Register all MCP tools with the FastMCP instance."""

        @self.mcp.tool()
        def search_symbol(query: str) -> str:
            """Search for symbols (functions/methods) matching a query string.
            Returns up to 20 matches with their file locations."""
            return self.search_symbol(query)

        @self.mcp.tool()
        def get_symbol(symbol_id: str) -> str:
            """Get detailed information about a specific symbol including all its implementations."""
            return self.get_symbol(symbol_id)

        @self.mcp.tool()
        def get_function_body(symbol_id: str) -> str:
            """Read the source code body of a function given its symbol name.
            Returns the function source from the first known implementation location."""
            return self.get_function_body(symbol_id)

        @self.mcp.tool()
        def get_file_ast(file_path: str, max_depth: int = 2) -> str:
            """Get a summary of functions defined in a file (like a shallow AST).
            Shows function names and their line ranges."""
            return self.get_file_ast(file_path, max_depth)

        @self.mcp.tool()
        def get_callers(symbol_id: str) -> str:
            """Get all functions that call the given symbol (incoming edges in the call graph)."""
            return self.get_callers(symbol_id)

        @self.mcp.tool()
        def get_callees(symbol_id: str) -> str:
            """Get all functions that are called by the given symbol (outgoing edges in the call graph)."""
            return self.get_callees(symbol_id)

        @self.mcp.tool()
        def find_references(symbol_id: str) -> str:
            """Find all references to a symbol — both callers and callees, plus implementation sites."""
            return self.find_references(symbol_id)

    # --- Tool implementations ---

    def search_symbol(self, query: str) -> str:
        query_lower = query.lower()
        matches = []
        for func_name in self._symbol_index:
            if query_lower in func_name.lower():
                locs = self._symbol_index[func_name]
                matches.append({
                    "symbol": func_name,
                    "locations": locs[:3]
                })
                if len(matches) >= 20:
                    break
        if not matches:
            return json.dumps({"results": [], "message": f"No symbols matching '{query}'"})
        return json.dumps({"results": matches})

    def get_symbol(self, symbol_id: str) -> str:
        locations = self._symbol_index.get(symbol_id, [])
        callers = sorted(self.graph.get_incoming_edges(symbol_id))
        callees = sorted(self.graph.get_outgoing_edges(symbol_id))
        if not locations and symbol_id not in self.graph.nodes:
            return json.dumps({"error": f"Symbol '{symbol_id}' not found"})
        return json.dumps({
            "symbol": symbol_id,
            "locations": locations,
            "callers_count": len(callers),
            "callees_count": len(callees),
            "callers": callers[:20],
            "callees": callees[:20]
        })

    def get_function_body(self, symbol_id: str) -> str:
        locations = self._symbol_index.get(symbol_id, [])
        if not locations:
            return json.dumps({"error": f"Symbol '{symbol_id}' not found or has no known location"})

        loc = locations[0]
        file_path = loc.get("file_path", "")
        start_line = loc.get("start_line", 0)
        end_line = loc.get("end_line", 0)

        # Resolve relative paths against repo_path
        if file_path and not os.path.isabs(file_path):
            file_path = os.path.join(self.repo_path, file_path)

        if not file_path or not os.path.exists(file_path):
            return json.dumps({
                "error": f"Source file not found: {loc.get('file_path', '')}",
                "location": loc
            })

        try:
            with open(file_path, 'r', errors='replace') as f:
                lines = f.readlines()
        except IOError as e:
            return json.dumps({"error": f"Cannot read file: {e}"})

        # Extract the function body (with some context)
        if start_line > 0 and end_line > 0:
            # 0-indexed adjustment
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            body_lines = lines[start_idx:end_idx]
        elif start_line > 0:
            # Only start known — grab up to 100 lines
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), start_idx + 100)
            body_lines = lines[start_idx:end_idx]
        else:
            return json.dumps({"error": "No line information available", "location": loc})

        # Cap output at ~8000 chars to stay within LLM context budget
        body = "".join(body_lines)
        if len(body) > 8000:
            body = body[:8000] + "\n... [truncated]"

        return json.dumps({
            "symbol": symbol_id,
            "file_path": loc.get("file_path", ""),
            "start_line": start_line,
            "end_line": end_line,
            "body": body
        })

    def get_file_ast(self, file_path: str, max_depth: int = 2) -> str:
        # Normalize path
        normalized = file_path
        if not os.path.isabs(file_path):
            normalized = file_path

        functions_in_file = self._file_functions.get(normalized, [])
        if not functions_in_file:
            # Try matching by basename or partial path
            for fp, funcs in self._file_functions.items():
                if fp.endswith(file_path) or file_path.endswith(fp):
                    functions_in_file = funcs
                    normalized = fp
                    break

        if not functions_in_file:
            return json.dumps({"error": f"No functions found in '{file_path}'"})

        entries = []
        for func_name in sorted(set(functions_in_file)):
            locs = self._symbol_index.get(func_name, [])
            relevant_locs = [l for l in locs if l.get("file_path") == normalized]
            entries.append({
                "function": func_name,
                "locations": relevant_locs
            })

        return json.dumps({
            "file_path": normalized,
            "functions": entries[:50]
        })

    def get_callers(self, symbol_id: str) -> str:
        if symbol_id not in self.graph.nodes:
            return json.dumps({"error": f"Symbol '{symbol_id}' not found in call graph"})
        callers = sorted(self.graph.get_incoming_edges(symbol_id))
        return json.dumps({
            "symbol": symbol_id,
            "callers": callers,
            "count": len(callers)
        })

    def get_callees(self, symbol_id: str) -> str:
        if symbol_id not in self.graph.nodes:
            return json.dumps({"error": f"Symbol '{symbol_id}' not found in call graph"})
        callees = sorted(self.graph.get_outgoing_edges(symbol_id))
        return json.dumps({
            "symbol": symbol_id,
            "callees": callees,
            "count": len(callees)
        })

    def find_references(self, symbol_id: str) -> str:
        locations = self._symbol_index.get(symbol_id, [])
        callers = sorted(self.graph.get_incoming_edges(symbol_id))
        callees = sorted(self.graph.get_outgoing_edges(symbol_id))
        if not locations and symbol_id not in self.graph.nodes:
            return json.dumps({"error": f"Symbol '{symbol_id}' not found"})
        return json.dumps({
            "symbol": symbol_id,
            "implementations": locations,
            "callers": callers[:30],
            "callees": callees[:30],
            "total_callers": len(callers),
            "total_callees": len(callees)
        })

    def get_tool_descriptions(self) -> str:
        """Return a formatted string describing available MCP tools for inclusion in LLM prompts."""
        return """Available code navigation tools (use JSON format to invoke):

1. search_symbol: Search for functions/methods by name substring.
   ```json {"tool": "search_symbol", "query": "<search_term>"}```

2. get_symbol: Get full info about a symbol (locations, callers, callees).
   ```json {"tool": "get_symbol", "symbol_id": "<function_name>"}```

3. get_function_body: Read the source code of a function.
   ```json {"tool": "get_function_body", "symbol_id": "<function_name>"}```

4. get_file_ast: List all functions defined in a file.
   ```json {"tool": "get_file_ast", "file_path": "<path>"}```

5. get_callers: Get all functions that call a given function.
   ```json {"tool": "get_callers", "symbol_id": "<function_name>"}```

6. get_callees: Get all functions called by a given function.
   ```json {"tool": "get_callees", "symbol_id": "<function_name>"}```

7. find_references: Find all references (callers + callees + implementation sites).
   ```json {"tool": "find_references", "symbol_id": "<function_name>"}```
"""

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> str:
        """Execute a tool by name with given parameters. Returns JSON string."""
        dispatch = {
            "search_symbol": lambda p: self.search_symbol(p.get("query", "")),
            "get_symbol": lambda p: self.get_symbol(p.get("symbol_id", "")),
            "get_function_body": lambda p: self.get_function_body(p.get("symbol_id", "")),
            "get_file_ast": lambda p: self.get_file_ast(
                p.get("file_path", ""), p.get("max_depth", 2)
            ),
            "get_callers": lambda p: self.get_callers(p.get("symbol_id", "")),
            "get_callees": lambda p: self.get_callees(p.get("symbol_id", "")),
            "find_references": lambda p: self.find_references(p.get("symbol_id", "")),
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        return handler(params)

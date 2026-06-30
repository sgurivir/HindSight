#!/usr/bin/env python3
"""Call-graph navigation utility.

Originally lived under `hindsight/core/mcp_tools/` and was named
`CodeNavigationServer`. The class is NOT a real MCP server — it just
exposes `execute_tool(name, params) -> str` so the legacy security
analyzers can dispatch tool calls through a uniform interface. The
FastMCP decorators were decorative; callers (SinkAnalyzer,
ExternalInputAnalyzer, FlowVulnerabilityAnalyzer) bypass FastMCP and
call `execute_tool` / `get_tool_descriptions` directly.

It was relocated here as part of the Step 6 async-orchestration rewrite
so that `hindsight/core/mcp_tools/` can be deleted in Step 7 without
breaking the security pipeline. The behavior, public methods, and JSON
output formats are byte-identical to the legacy class — only the
import path changed.
"""

import json
import os
from typing import Any, Dict, List, Optional

from .call_graph_util import CallGraph, load_call_graph_from_json
from .call_tree_util import extract_implementations
from ...utils.log_util import get_logger

logger = get_logger(__name__)


class CodeNavigationServer:
    """In-process call-graph + source navigation toolset.

    Constructed once per analysis run from the merged call graph data;
    used by the security analyzers to answer tool requests during LLM
    iterations (`execute_tool`) and to advertise the tool catalog in
    prompts (`get_tool_descriptions`).
    """

    def __init__(
        self,
        repo_path: str,
        call_graph_data: Any,
        implementations: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        graph: Optional[CallGraph] = None,
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

        # function_name -> list of {file_path, start_line, end_line}
        self._symbol_index: Dict[str, List[Dict[str, Any]]] = self.implementations

        # file_path -> list of function names
        self._file_functions: Dict[str, List[str]] = {}
        for func_name, locations in self._symbol_index.items():
            for loc in locations:
                fp = loc.get("file_path", "")
                if fp:
                    self._file_functions.setdefault(fp, []).append(func_name)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def search_symbol(self, query: str) -> str:
        query_lower = query.lower()
        matches = []
        for func_name in self._symbol_index:
            if query_lower in func_name.lower():
                locs = self._symbol_index[func_name]
                matches.append({"symbol": func_name, "locations": locs[:3]})
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
            "callees": callees[:20],
        })

    def get_function_body(self, symbol_id: str) -> str:
        locations = self._symbol_index.get(symbol_id, [])
        if not locations:
            return json.dumps({"error": f"Symbol '{symbol_id}' not found or has no known location"})

        loc = locations[0]
        file_path = loc.get("file_path", "")
        start_line = loc.get("start_line", 0)
        end_line = loc.get("end_line", 0)

        if file_path and not os.path.isabs(file_path):
            file_path = os.path.join(self.repo_path, file_path)

        if not file_path or not os.path.exists(file_path):
            return json.dumps({
                "error": f"Source file not found: {loc.get('file_path', '')}",
                "location": loc,
            })

        try:
            with open(file_path, 'r', errors='replace') as f:
                lines = f.readlines()
        except IOError as e:
            return json.dumps({"error": f"Cannot read file: {e}"})

        if start_line > 0 and end_line > 0:
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            body_lines = lines[start_idx:end_idx]
        elif start_line > 0:
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), start_idx + 100)
            body_lines = lines[start_idx:end_idx]
        else:
            return json.dumps({"error": "No line information available", "location": loc})

        body = "".join(body_lines)
        if len(body) > 8000:
            body = body[:8000] + "\n... [truncated]"

        return json.dumps({
            "symbol": symbol_id,
            "file_path": loc.get("file_path", ""),
            "start_line": start_line,
            "end_line": end_line,
            "body": body,
        })

    def get_file_ast(self, file_path: str, max_depth: int = 2) -> str:
        normalized = file_path
        functions_in_file = self._file_functions.get(normalized, [])
        if not functions_in_file:
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
            entries.append({"function": func_name, "locations": relevant_locs})

        return json.dumps({"file_path": normalized, "functions": entries[:50]})

    def get_callers(self, symbol_id: str) -> str:
        if symbol_id not in self.graph.nodes:
            return json.dumps({"error": f"Symbol '{symbol_id}' not found in call graph"})
        callers = sorted(self.graph.get_incoming_edges(symbol_id))
        return json.dumps({"symbol": symbol_id, "callers": callers, "count": len(callers)})

    def get_callees(self, symbol_id: str) -> str:
        if symbol_id not in self.graph.nodes:
            return json.dumps({"error": f"Symbol '{symbol_id}' not found in call graph"})
        callees = sorted(self.graph.get_outgoing_edges(symbol_id))
        return json.dumps({"symbol": symbol_id, "callees": callees, "count": len(callees)})

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
            "total_callees": len(callees),
        })

    # ------------------------------------------------------------------
    # Prompt + dispatch helpers
    # ------------------------------------------------------------------

    def get_tool_descriptions(self) -> str:
        """Return a formatted string describing available navigation tools.

        Used by the security analyzers to inject the tool catalog into
        their LLM system prompts. The exact wording matters: prompts
        downstream are tuned against this format.
        """
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
        """Dispatch a tool by name with given parameters. Returns a JSON string."""
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

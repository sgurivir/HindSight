#!/usr/bin/env python3
"""
CWE Store — In-memory index of CWE entries loaded from cwe_catalog.yaml.

Provides keyword search, category listing, and ID-based lookup.
Designed to be queried by LLM tool calls during data flow analysis.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

import yaml

from ...utils.log_util import get_logger

logger = get_logger(__name__)

_CATALOG_FILE = os.path.join(os.path.dirname(__file__), "cwe_catalog.yaml")


class CWEEntry:
    """Single CWE entry with searchable fields."""

    __slots__ = ("id", "name", "category", "anti_pattern", "data_flow_signal",
                 "severity", "languages")

    def __init__(self, entry: Dict[str, Any], category_name: str):
        self.id: str = entry["id"]
        self.name: str = entry["name"]
        self.category: str = category_name
        self.anti_pattern: str = entry.get("anti_pattern", "")
        self.data_flow_signal: str = entry.get("data_flow_signal", "")
        self.severity: str = entry.get("severity", "medium")
        self.languages: List[str] = entry.get("languages", [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "anti_pattern": self.anti_pattern,
            "data_flow_signal": self.data_flow_signal,
            "severity": self.severity,
            "languages": self.languages,
        }

    @property
    def searchable_text(self) -> str:
        return f"{self.id} {self.name} {self.category} {self.anti_pattern} {self.data_flow_signal}".lower()


class CWEStore:
    """
    In-memory CWE catalog with keyword search.

    Loads once from cwe_catalog.yaml. Intended to be instantiated once
    per analysis run and passed to the FlowVulnerabilityAnalyzer.
    """

    def __init__(self, catalog_path: str = None):
        self._entries: List[CWEEntry] = []
        self._by_id: Dict[str, CWEEntry] = {}
        self._categories: Dict[str, List[CWEEntry]] = {}
        self._load(catalog_path or _CATALOG_FILE)

    def _load(self, path: str) -> None:
        if not os.path.exists(path):
            logger.warning(f"CWE catalog not found at {path}")
            return

        with open(path, "r") as f:
            data = yaml.safe_load(f)

        for category in data.get("categories", []):
            category_name = category["name"]
            cat_entries = []
            for entry_data in category.get("entries", []):
                entry = CWEEntry(entry_data, category_name)
                self._entries.append(entry)
                self._by_id[entry.id] = entry
                cat_entries.append(entry)
            self._categories[category_name] = cat_entries

        logger.info(f"CWE catalog loaded: {len(self._entries)} entries in {len(self._categories)} categories")

    def search(self, query: str, max_results: int = 10, language: str = None) -> List[Dict[str, Any]]:
        """
        Keyword search across CWE name, anti_pattern, and data_flow_signal.

        Args:
            query: Space-separated keywords (any may match, ranked by fraction matched)
            max_results: Maximum entries to return
            language: Optional language filter (e.g., "swift")

        Returns:
            List of matching CWE entry dicts, ranked by keyword match fraction.
        """
        keywords = [k.lower() for k in query.split() if k.strip()]
        if not keywords:
            return []

        scored: List[tuple] = []
        for entry in self._entries:
            if language and entry.languages and language.lower() not in entry.languages:
                continue
            text = entry.searchable_text
            matches = sum(1 for kw in keywords if kw in text)
            if matches > 0:
                fraction = matches / len(keywords)
                scored.append((fraction, matches, entry))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [e.to_dict() for _, _, e in scored[:max_results]]

    def get_by_id(self, cwe_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific CWE entry by ID (e.g., 'CWE-79')."""
        entry = self._by_id.get(cwe_id)
        if entry:
            return entry.to_dict()
        normalized = f"CWE-{cwe_id}" if not cwe_id.startswith("CWE-") else cwe_id
        entry = self._by_id.get(normalized)
        return entry.to_dict() if entry else None

    def list_categories(self) -> List[Dict[str, Any]]:
        """Return all category names with entry counts."""
        return [
            {"name": name, "count": len(entries)}
            for name, entries in self._categories.items()
        ]

    def get_category_entries(self, category_name: str) -> List[Dict[str, Any]]:
        """Get all entries in a category by name (case-insensitive partial match)."""
        query_lower = category_name.lower()
        for name, entries in self._categories.items():
            if query_lower in name.lower():
                return [e.to_dict() for e in entries]
        return []

    def get_all_entries(self) -> List[Dict[str, Any]]:
        """Get all CWE entries."""
        return [e.to_dict() for e in self._entries]

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    # ─── Tool dispatch interface ────────────────────────────────────────

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> str:
        """Execute a CWE tool by name. Returns JSON string."""
        dispatch = {
            "searchCWE": lambda p: json.dumps(
                self.search(
                    p.get("query", ""),
                    max_results=int(p.get("max_results", 10)),
                    language=p.get("language"),
                ),
                indent=2,
            ),
            "getCWE": lambda p: json.dumps(
                self.get_by_id(p.get("id", "")) or {"error": f"CWE {p.get('id', '')} not found"},
                indent=2,
            ),
            "listCWECategories": lambda p: json.dumps(
                self.list_categories(), indent=2
            ),
            "getCWECategory": lambda p: json.dumps(
                self.get_category_entries(p.get("category", "")),
                indent=2,
            ),
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            return json.dumps({"error": f"Unknown CWE tool: {tool_name}"})
        return handler(params)

    def get_tool_descriptions(self) -> str:
        """Return formatted tool descriptions for inclusion in LLM prompts."""
        return """Available CWE (Common Weakness Enumeration) tools:

1. searchCWE: Search for weakness patterns by keyword. Returns matching CWE entries with anti-patterns and data flow signals.
   ```json {"tool": "searchCWE", "query": "<keywords>", "max_results": 10}```
   Optional: "language": "<swift|objc|c|cpp>" to filter by language.

2. getCWE: Get full details of a specific CWE by ID.
   ```json {"tool": "getCWE", "id": "CWE-79"}```

3. listCWECategories: List all available weakness categories.
   ```json {"tool": "listCWECategories"}```

4. getCWECategory: Get all CWE entries in a specific category.
   ```json {"tool": "getCWECategory", "category": "Concurrency Issues"}```
"""

    CWE_TOOL_NAMES = {"searchCWE", "getCWE", "listCWECategories", "getCWECategory"}

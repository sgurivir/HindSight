#!/usr/bin/env python3
# Created by Sridhar Gurivireddy on 11/02/2025
# ast_util_symbol_demangler.py
# Symbol demangling utility for AST analysis

import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple, Union, Iterable

class SymbolDemanglerUtil:
    """
    Class to demangle a name in callstack back to entry in merged_functions.json
    """
    def __init__(self, defined_functions_json: Path, nested_functions_json: Path = None):
        """defined_functions_json: path to JSON file with symbol -> locations array"""
        self.defined: Dict[str, list] = self._load(Path(defined_functions_json))
        self.reverse_index: Dict[str, List[str]] = self._build_reverse_index(self.defined.keys())

        # If nested json is provided, load it
        self.nested_functions: Dict[str, list] = {}
        if nested_functions_json is not None:
            self.nested_functions: Dict[str, list] = self._load(nested_functions_json)

    @staticmethod
    def _load(path: Path) -> Dict[str, list]:
        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
            # Use new schema with "function_to_location" wrapper
            return data['function_to_location']


    # ---- Public API ----
    def lookup_defs(self, word: str) -> List[dict]:
        """Return a flat list of location dicts for all symbols matching `word`."""
        out: List[dict] = []
        for sym in self.reverse_index.get(word, []):
            locs = self.defined.get(sym, [])
            if isinstance(locs, list):
                out.extend(locs)
        return out

    def lookup(self, word: str) -> List[str]:
        """Return list of original keys containing this token (in insertion order)."""
        return self.reverse_index.get(word, [])

    # ---- Internals ----
    @staticmethod
    def _load(src: Union[str, Path, Dict[str, list]]) -> Dict[str, list]:
        if isinstance(src, dict):
            return src
        p = Path(src)
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
            # Use new schema with "function_to_location" wrapper
            return data['function_to_location']

    @staticmethod
    def _tokenize(symbol: str) -> List[str]:
        """
        Keep alphanumerics, underscores and colons as tokens; split on everything else (e.g., '.').
        """
        symbol_with_no_params = symbol.partition("(")[0] # TODO : for now stripping everything on and after (
        return [t for t in re.findall(r"[A-Za-z0-9_:]+", symbol_with_no_params) if t]

    def _build_reverse_index(self, keys: Iterable[str]) -> Dict[str, List[str]]:
        rev: Dict[str, List[str]] = {}
        seen_per_word: Dict[str, set] = {}
        for k in keys:
            for tok in self._tokenize(k):
                if tok not in rev:
                    rev[tok] = []
                    seen_per_word[tok] = set()
                if k not in seen_per_word[tok]:
                    rev[tok].append(k)
                    seen_per_word[tok].add(k)
        return rev

    # add this helper inside the class
    @staticmethod
    def _top_tokens(symbol: str, n: int = 10) -> Set[str]:
        """
        # match n longest tokens
        """
        toks = SymbolDemanglerUtil._tokenize(symbol)
        #print(f"Symbol: {symbol} toks: {toks}")
        # unique, longest-first (tie-break by alphabetical for stability)
        ordered = sorted(set(toks), key=lambda t: (-len(t), t))
        return set(ordered[:n])

    # Find the best match
    def _best_match_defs(self, full_symbol: str) -> Tuple[List[dict], str]:
        query = self._top_tokens(full_symbol)
        if not query:
            return [], None
        best_key = None
        best_score = (0, 0)  # (overlap_count, key_token_count)

        for k in self.defined.keys():
            ktoks = set(self._tokenize(k))

            # TODO (define this number 6 somewhere)
            # Ignore frames with more than 6 symbols
            # They seem bogus and seem to come from errors
            if len(ktoks) > 6:
                continue

            overlap = len(query & ktoks)
            score = (overlap, len(ktoks))  # prefer more overlap, then more specific keys
            if score[0] > best_score[0] and score[1] > best_score[1]:
                best_score, best_key = score, k
                #print(f"score: {score} and best {best_score} for {query} and {ktoks} ")

        return (self.defined.get(best_key, []) if best_key else [], best_key)

    def best_match_defs(self, full_symbol: str) -> Tuple[List[dict], str]:
        # First try the symbol as-is
        matches, key = self._best_match_defs(full_symbol)
        if len(matches) > 0:
            return matches, key

        # Add first :: after class_name and try again
        return self._best_match_defs(full_symbol.replace(" ", "::", 1))

    # Returns invoking list along with best matches and the key found in merged_functions.json
    def get_best_match_and_invoking_list(self, full_symbol: str) -> dict:
        best_matches, found_key = self.best_match_defs(full_symbol)

        # If no matches or no nested_functions source, just return what we have with function added
        if not best_matches or self.nested_functions is None:
            matches_with_key = []
            for match in (best_matches or []):
                if isinstance(match, dict):
                    matches_with_key.append({**match, 'function': found_key})
                else:
                    matches_with_key.append(match)
            return {
                'matches': matches_with_key
            }

        # Normalize nested_functions into a dict mapping function_name -> invoking list
        inv_map = {}
        nf = self.nested_functions
        if isinstance(nf, dict):
            inv_map = nf
        elif isinstance(nf, list):
            # Expect objects with 'function' and 'invoking'
            for item in nf:
                if isinstance(item, dict):
                    fn = item.get('function') or item.get('function_name') or item.get('name')
                    if isinstance(fn, str):
                        inv_map[fn] = item.get('invoking', []) or []
        # else leave empty

        # Build merged result list
        functions_with_their_invocations: List[dict] = []
        #print(f"Best matches: {best_matches}")
        for match in best_matches:
            if not isinstance(match, dict):
                continue
            fn = match.get('function_name') or match.get('function') or match.get('name')
            inv_list = list(inv_map.get(fn, [])) if fn else []
            merged = {**match, 'invoking': inv_list, 'function': found_key}
            functions_with_their_invocations.append(merged)

        #for _f in functions_with_their_invocations:
        #    print(f"\t invoking: {_f['invoking']}")
        return {
            'matches': functions_with_their_invocations
        }
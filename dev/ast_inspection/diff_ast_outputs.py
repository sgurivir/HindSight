#!/usr/bin/env python3
"""
Script to diff merged_symbols and merged_callgraph between three AST output directories.

Compares:
- /Users/sgurivireddy/ast_diff/without_macros
- /Users/sgurivireddy/ast_diff/with_macros
- /Users/sgurivireddy/ast_diff/ambivalent

Prints a summary of differences.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Optional


def load_json_file(filepath: Path) -> Dict[str, Any]:
    """Load a JSON file and return its contents."""
    if not filepath.exists():
        print(f"Warning: File not found: {filepath}")
        return {}
    
    with open(filepath, 'r') as f:
        return json.load(f)


def get_symbol_key(symbol: Dict) -> str:
    """Generate a unique key for a symbol based on its name and file."""
    name = symbol.get('name', '')
    context = symbol.get('context', {})
    file = context.get('file', '')
    symbol_type = symbol.get('type', '')
    return f"{symbol_type}:{name}@{file}"


def compare_symbol_lists_three_way(without_list: List[Dict], with_list: List[Dict], ambivalent_list: List[Dict]) -> Dict[str, Any]:
    """Compare three lists of symbols."""
    # Create dictionaries keyed by symbol identifier
    without_dict = {get_symbol_key(s): s for s in without_list}
    with_dict = {get_symbol_key(s): s for s in with_list}
    ambivalent_dict = {get_symbol_key(s): s for s in ambivalent_list}
    
    without_keys = set(without_dict.keys())
    with_keys = set(with_dict.keys())
    ambivalent_keys = set(ambivalent_dict.keys())
    
    all_keys = without_keys | with_keys | ambivalent_keys
    
    # Categorize symbols
    only_without = without_keys - with_keys - ambivalent_keys
    only_with = with_keys - without_keys - ambivalent_keys
    only_ambivalent = ambivalent_keys - without_keys - with_keys
    in_all_three = without_keys & with_keys & ambivalent_keys
    
    return {
        "only_in_without_macros": sorted(only_without),
        "only_in_with_macros": sorted(only_with),
        "only_in_ambivalent": sorted(only_ambivalent),
        "in_all_three": len(in_all_three),
        "total_unique": len(all_keys)
    }


def compare_metadata_three_way(without_meta: Dict, with_meta: Dict, ambivalent_meta: Dict) -> Dict[str, Any]:
    """Compare metadata summaries from three sources."""
    results = {}
    
    without_summary = without_meta.get('summary', {})
    with_summary = with_meta.get('summary', {})
    ambivalent_summary = ambivalent_meta.get('summary', {})
    
    # Compare top-level counts
    for key in ['total_symbols', 'total_graph_nodes']:
        without_val = without_summary.get(key, 0)
        with_val = with_summary.get(key, 0)
        ambivalent_val = ambivalent_summary.get(key, 0)
        results[key] = {
            'without_macros': without_val,
            'with_macros': with_val,
            'ambivalent': ambivalent_val,
            'diff_with_vs_without': with_val - without_val,
            'diff_ambivalent_vs_without': ambivalent_val - without_val
        }
    
    # Compare clang-specific stats
    without_clang = without_summary.get('clang', {})
    with_clang = with_summary.get('clang', {})
    ambivalent_clang = ambivalent_summary.get('clang', {})
    
    clang_stats = {}
    for key in ['symbols', 'functions', 'classes', 'methods', 'graph_nodes']:
        without_val = without_clang.get(key, 0)
        with_val = with_clang.get(key, 0)
        ambivalent_val = ambivalent_clang.get(key, 0)
        clang_stats[key] = {
            'without_macros': without_val,
            'with_macros': with_val,
            'ambivalent': ambivalent_val,
            'diff_with_vs_without': with_val - without_val,
            'diff_ambivalent_vs_without': ambivalent_val - without_val
        }
    results['clang'] = clang_stats
    
    return results


def compare_callgraph_three_way(without_macros: Dict, with_macros: Dict, ambivalent: Dict) -> Dict[str, Any]:
    """Compare merged_callgraph between three versions."""
    without_keys = set(without_macros.keys()) if without_macros else set()
    with_keys = set(with_macros.keys()) if with_macros else set()
    ambivalent_keys = set(ambivalent.keys()) if ambivalent else set()
    
    all_keys = without_keys | with_keys | ambivalent_keys
    
    results = {
        "only_in_without_macros": sorted(without_keys - with_keys - ambivalent_keys),
        "only_in_with_macros": sorted(with_keys - without_keys - ambivalent_keys),
        "only_in_ambivalent": sorted(ambivalent_keys - without_keys - with_keys),
        "in_all_three": len(without_keys & with_keys & ambivalent_keys),
        "total_unique": len(all_keys),
        "without_count": len(without_keys),
        "with_count": len(with_keys),
        "ambivalent_count": len(ambivalent_keys)
    }
    
    return results


def print_callgraph_summary(title: str, comparison: Dict[str, Any]):
    """Print a summary of the callgraph comparison results."""
    print(f"\n{'='*80}")
    print(f" {title}")
    print(f"{'='*80}")
    
    print(f"\n📊 Node Counts:")
    print(f"  - Without macros: {comparison.get('without_count', 0):,}")
    print(f"  - With macros:    {comparison.get('with_count', 0):,}")
    print(f"  - Ambivalent:     {comparison.get('ambivalent_count', 0):,}")
    print(f"  - In all three:   {comparison.get('in_all_three', 0):,}")
    print(f"  - Total unique:   {comparison.get('total_unique', 0):,}")
    
    only_without = comparison.get("only_in_without_macros", [])
    only_with = comparison.get("only_in_with_macros", [])
    only_ambivalent = comparison.get("only_in_ambivalent", [])
    
    if only_without:
        print(f"\n🔴 Only in without_macros ({len(only_without):,}):")
        for item in only_without[:10]:
            print(f"    - {item}")
        if len(only_without) > 10:
            print(f"    ... and {len(only_without) - 10} more")
    
    if only_with:
        print(f"\n🟢 Only in with_macros ({len(only_with):,}):")
        for item in only_with[:10]:
            print(f"    - {item}")
        if len(only_with) > 10:
            print(f"    ... and {len(only_with) - 10} more")
    
    if only_ambivalent:
        print(f"\n🟡 Only in ambivalent ({len(only_ambivalent):,}):")
        for item in only_ambivalent[:10]:
            print(f"    - {item}")
        if len(only_ambivalent) > 10:
            print(f"    ... and {len(only_ambivalent) - 10} more")


def print_metadata_comparison(metadata_comp: Dict[str, Any]):
    """Print metadata comparison in a formatted way."""
    print(f"\n{'='*80}")
    print(" METADATA COMPARISON")
    print(f"{'='*80}")
    
    print("\n📊 Overall Statistics:")
    print(f"  {'Metric':<20} {'Without':>12} {'With':>12} {'Ambivalent':>12} {'Δ With':>10} {'Δ Ambiv':>10}")
    print(f"  {'-'*76}")
    
    for key in ['total_symbols', 'total_graph_nodes']:
        if key in metadata_comp:
            stats = metadata_comp[key]
            print(f"  {key:<20} {stats['without_macros']:>12,} {stats['with_macros']:>12,} {stats['ambivalent']:>12,} {stats['diff_with_vs_without']:>+10,} {stats['diff_ambivalent_vs_without']:>+10,}")
    
    if 'clang' in metadata_comp:
        print("\n📊 Clang-specific Statistics:")
        print(f"  {'Metric':<20} {'Without':>12} {'With':>12} {'Ambivalent':>12} {'Δ With':>10} {'Δ Ambiv':>10}")
        print(f"  {'-'*76}")
        
        for key, stats in metadata_comp['clang'].items():
            print(f"  {key:<20} {stats['without_macros']:>12,} {stats['with_macros']:>12,} {stats['ambivalent']:>12,} {stats['diff_with_vs_without']:>+10,} {stats['diff_ambivalent_vs_without']:>+10,}")


def print_symbol_comparison(symbol_comp: Dict[str, Any]):
    """Print symbol list comparison."""
    print(f"\n{'='*80}")
    print(" SYMBOL LIST COMPARISON")
    print(f"{'='*80}")
    
    only_without = symbol_comp.get("only_in_without_macros", [])
    only_with = symbol_comp.get("only_in_with_macros", [])
    only_ambivalent = symbol_comp.get("only_in_ambivalent", [])
    in_all = symbol_comp.get("in_all_three", 0)
    total = symbol_comp.get("total_unique", 0)
    
    print(f"\n📊 Summary:")
    print(f"  - Only in without_macros: {len(only_without):,}")
    print(f"  - Only in with_macros:    {len(only_with):,}")
    print(f"  - Only in ambivalent:     {len(only_ambivalent):,}")
    print(f"  - In all three:           {in_all:,}")
    print(f"  - Total unique symbols:   {total:,}")
    
    def print_symbols_by_type(symbols: List[str], label: str, emoji: str):
        if symbols:
            print(f"\n{emoji} {label} ({len(symbols):,}):")
            by_type = {}
            for sym in symbols:
                sym_type = sym.split(':')[0] if ':' in sym else 'unknown'
                by_type.setdefault(sym_type, []).append(sym)
            
            for sym_type, type_symbols in sorted(by_type.items()):
                print(f"    {sym_type}: {len(type_symbols):,}")
                for sym in type_symbols[:3]:
                    print(f"      - {sym}")
                if len(type_symbols) > 3:
                    print(f"      ... and {len(type_symbols) - 3} more")
    
    print_symbols_by_type(only_without, "Symbols only in without_macros", "🔴")
    print_symbols_by_type(only_with, "Symbols only in with_macros", "🟢")
    print_symbols_by_type(only_ambivalent, "Symbols only in ambivalent", "🟡")


def main():
    """Main function to run the diff comparison."""
    without_macros_dir = Path("/Users/sgurivireddy/ast_diff/without_macros")
    with_macros_dir = Path("/Users/sgurivireddy/ast_diff/with_macros")
    ambivalent_dir = Path("/Users/sgurivireddy/ast_diff/ambivalent")
    
    print("🔍 AST Diff Comparison Tool (Three-Way)")
    print(f"   1. Without macros: {without_macros_dir}")
    print(f"   2. With macros:    {with_macros_dir}")
    print(f"   3. Ambivalent:     {ambivalent_dir}")
    
    # Check directories exist
    dirs_exist = True
    for d, name in [(without_macros_dir, "without_macros"), (with_macros_dir, "with_macros"), (ambivalent_dir, "ambivalent")]:
        if not d.exists():
            print(f"❌ Error: Directory not found: {d}")
            dirs_exist = False
    
    if not dirs_exist:
        return
    
    # Load merged_symbols
    print("\n📂 Loading merged_symbols...")
    without_symbols = load_json_file(without_macros_dir / "merged_symbols.json")
    with_symbols = load_json_file(with_macros_dir / "merged_symbols.json")
    ambivalent_symbols = load_json_file(ambivalent_dir / "merged_symbols.json")
    
    # Load merged_callgraph
    print("📂 Loading merged_callgraph...")
    without_callgraph = load_json_file(without_macros_dir / "merged_callgraph.json")
    with_callgraph = load_json_file(with_macros_dir / "merged_callgraph.json")
    ambivalent_callgraph = load_json_file(ambivalent_dir / "merged_callgraph.json")
    
    # Compare metadata first
    if without_symbols or with_symbols or ambivalent_symbols:
        without_meta = without_symbols.get('metadata', {})
        with_meta = with_symbols.get('metadata', {})
        ambivalent_meta = ambivalent_symbols.get('metadata', {})
        
        if without_meta or with_meta or ambivalent_meta:
            metadata_comp = compare_metadata_three_way(without_meta, with_meta, ambivalent_meta)
            print_metadata_comparison(metadata_comp)
        
        # Compare actual symbol lists
        without_sym_list = without_symbols.get('symbols', [])
        with_sym_list = with_symbols.get('symbols', [])
        ambivalent_sym_list = ambivalent_symbols.get('symbols', [])
        
        if without_sym_list or with_sym_list or ambivalent_sym_list:
            symbol_comp = compare_symbol_lists_three_way(without_sym_list, with_sym_list, ambivalent_sym_list)
            print_symbol_comparison(symbol_comp)
    else:
        print("\n⚠️  No merged_symbols.json files found to compare")
    
    # Compare callgraph
    if without_callgraph or with_callgraph or ambivalent_callgraph:
        callgraph_comparison = compare_callgraph_three_way(without_callgraph, with_callgraph, ambivalent_callgraph)
        print_callgraph_summary("MERGED CALLGRAPH COMPARISON", callgraph_comparison)
    else:
        print("\n⚠️  No merged_callgraph.json files found to compare")
    
    # Overall summary
    print(f"\n{'='*80}")
    print(" OVERALL SUMMARY")
    print(f"{'='*80}")
    
    print(f"\n{'Metric':<25} {'Without':>12} {'With':>12} {'Ambivalent':>12}")
    print(f"{'-'*61}")
    
    for data, name in [(without_symbols, 'without'), (with_symbols, 'with'), (ambivalent_symbols, 'ambivalent')]:
        pass  # Just for structure
    
    without_total = without_symbols.get('metadata', {}).get('summary', {}).get('total_symbols', 0)
    with_total = with_symbols.get('metadata', {}).get('summary', {}).get('total_symbols', 0)
    ambivalent_total = ambivalent_symbols.get('metadata', {}).get('summary', {}).get('total_symbols', 0)
    print(f"{'Total Symbols':<25} {without_total:>12,} {with_total:>12,} {ambivalent_total:>12,}")
    
    without_nodes = without_symbols.get('metadata', {}).get('summary', {}).get('total_graph_nodes', 0)
    with_nodes = with_symbols.get('metadata', {}).get('summary', {}).get('total_graph_nodes', 0)
    ambivalent_nodes = ambivalent_symbols.get('metadata', {}).get('summary', {}).get('total_graph_nodes', 0)
    print(f"{'Total Graph Nodes':<25} {without_nodes:>12,} {with_nodes:>12,} {ambivalent_nodes:>12,}")


if __name__ == "__main__":
    main()

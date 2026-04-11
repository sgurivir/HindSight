# Data Flow Analyzer Implementation Plan

## Overview

The Data Flow Analyzer is a new analyzer that generates call trees from AST call graphs. It follows the same architectural patterns as `code_analyzer.py` and `trace_analyzer.py`, leveraging existing infrastructure for directory classification, AST generation, and LLM-based analysis.

## Architecture Summary

### Existing Patterns to Follow

Based on analysis of the codebase:

1. **Analyzer Pattern** (`code_analyzer.py`, `trace_analyzer.py`):
   - Extends `BaseAnalyzer` for the analyzer class
   - Extends `AnalysisRunner` with mixins for the runner class
   - Uses `UnifiedIssueFilterMixin` and `ReportGeneratorMixin`
   - Takes config file and repo path as arguments
   - Uses LLM-based directory classification to identify directories to ignore

2. **AST Generation** (`hindsight/core/lang_util/ast_util.py`):
   - `ASTUtil.run_full_analysis()` generates merged call graphs
   - Outputs: `merged_call_graph.json`, `merged_functions.json`, `merged_defined_classes.json`
   - Supports multiple languages via language-specific helpers

3. **Call Tree Generation** (`dev/call_graph_util/`):
   - `GraphUtil.py`: Core graph algorithms (cycle detection, DAG creation, level computation)
   - `call_tree_generator.py`: Generates call trees from call graphs
   - `call_graph_stats.py`: Statistics and sample callstack generation

## Implementation Plan

### Phase 1: Move Core Logic to `hindsight/core/lang_util`

#### 1.1 Create `hindsight/core/lang_util/call_graph_util.py`

Move and refactor the `CallGraph` class from `dev/call_graph_util/GraphUtil.py`:

```python
# hindsight/core/lang_util/call_graph_util.py

class CallGraph:
    """
    A directed graph representation of a call graph.
    
    Nodes are function names (strings).
    Edges represent function calls (from caller to callee).
    """
    
    def __init__(self):
        self.nodes: Set[str] = set()
        self.edges: Dict[str, Set[str]] = defaultdict(set)
        self.reverse_edges: Dict[str, Set[str]] = defaultdict(set)
    
    # Methods to move from GraphUtil.py:
    # - add_node(), add_edge()
    # - get_num_nodes(), get_num_edges()
    # - get_outgoing_edges(), get_incoming_edges()
    # - get_edges_per_node_stats()
    # - get_leaf_nodes(), get_root_nodes()
    # - compute_levels_from_bottom()
    # - compute_graph_depth()
    # - detect_cycles()
    # - count_paths_dag()
    # - get_statistics()


def load_call_graph_from_json(data) -> CallGraph:
    """Load a CallGraph from merged_call_graph.json format."""
    # Move from GraphUtil.py


def print_statistics(stats: Dict, indent: int = 0) -> None:
    """Print statistics in a formatted way."""
    # Move from GraphUtil.py
```

#### 1.2 Create `hindsight/core/lang_util/call_tree_util.py`

Move and refactor call tree generation logic from `dev/call_graph_util/call_tree_generator.py`:

```python
# hindsight/core/lang_util/call_tree_util.py

from .call_graph_util import CallGraph


def extract_implementations(data: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Extract implementation locations for each function."""
    # Move from call_tree_generator.py


def create_dag(graph: CallGraph, max_depth: int = 20) -> Dict[str, Set[str]]:
    """Break cycles in the graph to create a DAG."""
    # Move from call_tree_generator.py


def get_dag_root_nodes(graph: CallGraph, dag_edges: Dict[str, Set[str]]) -> Set[str]:
    """Find root nodes in the DAG."""
    # Move from call_tree_generator.py


def build_call_tree_node(
    func: str,
    dag_edges: Dict[str, Set[str]],
    implementations: Dict[str, List[Dict[str, Any]]],
    visited: Set[str]
) -> Dict[str, Any]:
    """Recursively build a call tree node with its children."""
    # Move from call_tree_generator.py


def generate_call_tree(
    graph: CallGraph,
    dag_edges: Dict[str, Set[str]],
    implementations: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, Any]:
    """Generate the complete call tree JSON structure."""
    # Move from call_tree_generator.py


def format_location(location: List[Dict[str, Any]]) -> str:
    """Format location information as a string."""
    # Move from call_tree_generator.py


def write_tree_text_format(
    call_tree: Dict[str, Any],
    output_path: str,
    show_location: bool = False
) -> None:
    """Write the call tree in text format with tree-style indentation."""
    # Move from call_tree_generator.py


class CallTreeGenerator:
    """
    High-level class for generating call trees from call graphs.
    
    This class provides a clean API for the data_flow_analyzer to use.
    """
    
    def __init__(self, max_depth: int = 20):
        self.max_depth = max_depth
        self.graph: Optional[CallGraph] = None
        self.implementations: Dict[str, List[Dict[str, Any]]] = {}
        self.dag_edges: Dict[str, Set[str]] = {}
    
    def load_from_json(self, json_path: str) -> None:
        """Load call graph from JSON file."""
        pass
    
    def load_from_data(self, data: Any) -> None:
        """Load call graph from parsed JSON data."""
        pass
    
    def generate_call_tree(self) -> Dict[str, Any]:
        """Generate the call tree structure."""
        pass
    
    def write_json(self, output_path: str, pretty: bool = False) -> None:
        """Write call tree to JSON file."""
        pass
    
    def write_text(self, output_path: str, show_location: bool = False) -> None:
        """Write call tree to text file."""
        pass
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the call graph."""
        pass
```

#### 1.3 Update `dev/call_graph_util/call_graph_stats.py`

Keep the script but import from the new location:

```python
# dev/call_graph_util/call_graph_stats.py

# Import from the new location
from hindsight.core.lang_util.call_graph_util import (
    CallGraph,
    load_call_graph_from_json,
    print_statistics
)

# Keep the script functionality for standalone usage
# The generate_sample_callstacks function can stay here as it's script-specific
```

### Phase 2: Create Data Flow Analyzer

#### 2.1 Create `hindsight/analyzers/data_flow_analyzer.py`

```python
#!/usr/bin/env python3
"""
Data Flow Analyzer - Generates call trees from AST call graphs.

This analyzer:
1. Uses LLM-based directory classification to identify directories to ignore
2. Generates AST call graphs (similar to code_analyzer)
3. Generates call trees from the call graphs

Usage:
    python -m hindsight.analyzers.data_flow_analyzer --config config.json --repo /path/to/repo
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .analysis_runner import AnalysisRunner
from .analysis_runner_mixins import UnifiedIssueFilterMixin, ReportGeneratorMixin
from .base_analyzer import BaseAnalyzer
from .directory_classifier import DirectoryClassifier
from .token_tracker import TokenTracker

from ..core.constants import (
    NESTED_CALL_GRAPH_FILE,
    MERGED_SYMBOLS_FILE,
    MERGED_DEFINED_CLASSES_FILE
)
from ..core.lang_util.call_graph_util import CallGraph, load_call_graph_from_json
from ..core.lang_util.call_tree_util import CallTreeGenerator
from ..utils.config_util import (
    ConfigValidationError,
    load_and_validate_config,
    get_api_key_from_config,
    get_llm_provider_type
)
from ..utils.log_util import setup_default_logging, get_logger
from ..utils.output_directory_provider import get_output_directory_provider

logger = get_logger(__name__)


class DataFlowAnalyzer(BaseAnalyzer):
    """Analyzer that generates call trees from AST call graphs."""

    def __init__(self):
        super().__init__()
        self.call_tree_generator: Optional[CallTreeGenerator] = None

    def name(self) -> str:
        return "DataFlowAnalyzer"

    def initialize(self, config: Mapping[str, Any]) -> None:
        """Setup and prepare for analysis."""
        super().initialize(config)
        self.call_tree_generator = CallTreeGenerator(
            max_depth=config.get('max_call_depth', 20)
        )

    def analyze_function(self, func_record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """
        Not used for data flow analysis - this analyzer works on the entire call graph.
        """
        return None

    def finalize(self) -> None:
        """Cleanup after analysis."""
        pass


class DataFlowAnalysisRunner(UnifiedIssueFilterMixin, ReportGeneratorMixin, AnalysisRunner):
    """Main runner class for data flow analysis."""

    def __init__(self):
        """Initialize the runner with logging setup."""
        super().__init__()
        self.call_tree_generator: Optional[CallTreeGenerator] = None

    def get_default_data_flow_paths(self, repo_path: str, output_base_dir: str = None) -> dict:
        """
        Get default output paths for data flow analysis.

        Args:
            repo_path: Path to the repository
            output_base_dir: Optional output base directory

        Returns:
            Dictionary containing default paths for data flow analysis
        """
        try:
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            data_flow_dir = os.path.join(artifacts_dir, "data_flow_analysis")
        except RuntimeError:
            # Fallback to parameter-based approach
            if output_base_dir:
                repo_name = os.path.basename(repo_path.rstrip('/'))
                data_flow_dir = os.path.join(
                    os.path.expanduser(output_base_dir),
                    repo_name,
                    "data_flow_analysis"
                )
            else:
                from ..utils.file_util import get_artifacts_temp_subdir_path
                data_flow_dir = get_artifacts_temp_subdir_path(
                    repo_path, "data_flow_analysis", output_base_dir
                )

        return {
            'data_flow_dir': data_flow_dir,
            'call_tree_json': os.path.join(data_flow_dir, "call_tree.json"),
            'call_tree_text': os.path.join(data_flow_dir, "call_tree.txt"),
            'statistics_file': os.path.join(data_flow_dir, "call_graph_statistics.json")
        }

    def _run_directory_classification(self, config: dict) -> None:
        """
        Run DirectoryClassifier to get enhanced exclusions.
        
        This method:
        1. Runs DirectoryClassifier (static + LLM-based) to get enhanced exclude directories
        2. Updates config with the enhanced exclusions
        
        Args:
            config: Configuration dictionary
        """
        repo_path = config['path_to_repo']
        include_directories = config.get('include_directories', [])
        user_exclude_directories = config.get('exclude_directories', [])
        
        self.logger.info("Running DirectoryClassifier to get enhanced exclusions...")
        self.logger.info(f"Repository: {repo_path}")
        self.logger.info(f"User-provided include directories: {include_directories}")
        self.logger.info(f"User-provided exclude directories: {user_exclude_directories}")
        
        try:
            # Run enhanced directory exclusion (static + LLM-based)
            enhanced_exclude_dirs = self.get_enhanced_exclude_directories(
                repo_path=repo_path,
                config=config,
                user_provided_include_list=include_directories,
                user_provided_exclude_list=user_exclude_directories
            )
            
            self.logger.info(f"DirectoryClassifier complete:")
            self.logger.info(f"  User-provided exclusions: {len(user_exclude_directories)}")
            self.logger.info(f"  Enhanced exclusions (static + LLM): {len(enhanced_exclude_dirs)}")
            
            # Update config with enhanced exclusions
            config['exclude_directories'] = enhanced_exclude_dirs
            self.logger.info("Updated config with enhanced exclude directories")
            
        except Exception as e:
            self.logger.warning(f"DirectoryClassifier failed, using user-provided exclusions: {e}")

    def _generate_call_tree(self, config: dict) -> Dict[str, Any]:
        """
        Generate call tree from the AST call graph.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Dictionary containing call tree and metadata
        """
        self.logger.info("Generating call tree from AST call graph...")
        
        # Get paths
        ast_call_graph_dir = config['astCallGraphDir']
        nested_call_graph_path = os.path.join(ast_call_graph_dir, NESTED_CALL_GRAPH_FILE)
        
        # Check if call graph exists
        if not os.path.exists(nested_call_graph_path):
            self.logger.error(f"Call graph file not found: {nested_call_graph_path}")
            return {}
        
        # Initialize call tree generator
        max_depth = config.get('max_call_depth', 20)
        self.call_tree_generator = CallTreeGenerator(max_depth=max_depth)
        
        # Load and generate
        self.call_tree_generator.load_from_json(nested_call_graph_path)
        call_tree = self.call_tree_generator.generate_call_tree()
        
        # Get output paths
        data_flow_paths = self.get_default_data_flow_paths(
            config['path_to_repo'],
            config.get('output_base_dir')
        )
        
        # Create output directory
        os.makedirs(data_flow_paths['data_flow_dir'], exist_ok=True)
        
        # Write outputs
        self.call_tree_generator.write_json(
            data_flow_paths['call_tree_json'],
            pretty=True
        )
        self.logger.info(f"Call tree JSON written to: {data_flow_paths['call_tree_json']}")
        
        show_location = config.get('show_location', True)
        self.call_tree_generator.write_text(
            data_flow_paths['call_tree_text'],
            show_location=show_location
        )
        self.logger.info(f"Call tree text written to: {data_flow_paths['call_tree_text']}")
        
        # Write statistics
        stats = self.call_tree_generator.get_statistics()
        with open(data_flow_paths['statistics_file'], 'w') as f:
            json.dump(stats, f, indent=2)
        self.logger.info(f"Statistics written to: {data_flow_paths['statistics_file']}")
        
        return call_tree

    def run(
        self,
        config_dict: Dict[str, Any],
        repo_path: str,
        out_dir: str,
        force_recreate_ast: bool = False,
        exclude_directories: List[str] = None,
        include_directories: List[str] = None,
        max_call_depth: int = 20,
        show_location: bool = True
    ):
        """
        Main entry point for the Data Flow Analyzer.

        Args:
            config_dict: Configuration dictionary
            repo_path: Path to repository directory
            out_dir: Output directory
            force_recreate_ast: Force recreation of AST call graphs
            exclude_directories: List of directories to exclude
            include_directories: List of directories to include
            max_call_depth: Maximum depth for call tree generation
            show_location: Show file locations in text output
        """
        try:
            # Start sleep prevention
            self._start_sleep_prevention()

            config = config_dict.copy()
            
            # Set repo_path in config
            config['path_to_repo'] = repo_path
            config['max_call_depth'] = max_call_depth
            config['show_location'] = show_location

            # Merge include/exclude directories
            if exclude_directories:
                existing = config.get('exclude_directories', [])
                config['exclude_directories'] = list(set(existing + exclude_directories))
            
            if include_directories:
                existing = config.get('include_directories', [])
                config['include_directories'] = list(set(existing + include_directories))

            # Initialize OutputDirectoryProvider
            output_provider = get_output_directory_provider()
            output_provider.configure(repo_path, out_dir)
            self.logger.info(f"Configured OutputDirectoryProvider")

            # Set AST output directory
            ast_paths = self.get_default_ast_output_paths()
            config['astCallGraphDir'] = ast_paths['code_insights_dir']

            # Step 1: Directory Structure Index
            self.logger.info("\n\n=== DIRECTORY STRUCTURE INDEX ===")
            self._ensure_directory_structure_index(repo_path)

            # Step 2: Directory Classification (LLM-based)
            self.logger.info("\n\n=== DIRECTORY CLASSIFICATION ===")
            self._run_directory_classification(config)

            # Step 3: AST Generation
            self.logger.info("\n\n=== AST CALL GRAPH GENERATION ===")
            
            ast_files_exist = self._check_existing_ast_files(config)
            
            if force_recreate_ast or not ast_files_exist:
                if force_recreate_ast:
                    self.logger.info("Force recreate AST flag is set")
                else:
                    self.logger.info("No existing AST files found")
                
                self._generate_ast_call_graph(config)
            else:
                self.logger.info("Using existing AST files")

            # Step 4: Call Tree Generation
            self.logger.info("\n\n=== CALL TREE GENERATION ===")
            call_tree = self._generate_call_tree(config)
            
            if call_tree:
                metadata = call_tree.get('metadata', {})
                self.logger.info(f"Call tree generation completed!")
                self.logger.info(f"  Total functions: {metadata.get('total_functions', 0)}")
                self.logger.info(f"  Root nodes: {metadata.get('total_root_nodes', 0)}")
                self.logger.info(f"  DAG edges: {metadata.get('dag_edges_count', 0)}")

            self.logger.info("\nData flow analysis pipeline completed successfully!")

        except ConfigValidationError as e:
            self.logger.error(f"Configuration validation failed: {e}")
            sys.exit(1)
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            self._stop_sleep_prevention()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Data Flow Analyzer - Generates call trees from AST call graphs"
    )
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="Path to configuration file"
    )
    parser.add_argument(
        "--repo", "-r",
        required=True,
        help="Path to repository directory"
    )
    parser.add_argument(
        "--out-dir", "-o",
        default=os.path.expanduser("~/llm_artifacts"),
        help="Output directory (default: ~/llm_artifacts)"
    )
    parser.add_argument(
        "--force-recreate-ast",
        action="store_true",
        help="Force recreation of AST call graphs"
    )
    parser.add_argument(
        "--exclude-directories",
        nargs="+",
        help="List of directories to exclude"
    )
    parser.add_argument(
        "--include-directories",
        nargs="+",
        help="List of directories to include"
    )
    parser.add_argument(
        "--max-call-depth",
        type=int,
        default=20,
        help="Maximum depth for call tree generation (default: 20)"
    )
    parser.add_argument(
        "--show-location",
        action="store_true",
        default=True,
        help="Show file locations in text output (default: True)"
    )
    parser.add_argument(
        "--no-show-location",
        action="store_false",
        dest="show_location",
        help="Hide file locations in text output"
    )

    args = parser.parse_args()

    # Setup logging
    setup_default_logging()

    # Load config
    try:
        config = load_and_validate_config(args.config)
    except ConfigValidationError as e:
        logger.error(f"Configuration validation failed: {e}")
        sys.exit(1)

    # Create and run analyzer
    runner = DataFlowAnalysisRunner()
    
    runner.run(
        config_dict=config,
        repo_path=args.repo,
        out_dir=args.out_dir,
        force_recreate_ast=args.force_recreate_ast,
        exclude_directories=args.exclude_directories,
        include_directories=args.include_directories,
        max_call_depth=args.max_call_depth,
        show_location=args.show_location
    )


if __name__ == "__main__":
    main()
```

### Phase 3: Directory Structure

After implementation, the directory structure will be:

```
hindsight/
├── analyzers/
│   ├── __init__.py
│   ├── analysis_runner.py
│   ├── analysis_runner_mixins.py
│   ├── base_analyzer.py
│   ├── code_analyzer.py
│   ├── data_flow_analyzer.py      # NEW
│   ├── directory_classifier.py
│   ├── trace_analyzer.py
│   └── ...
├── core/
│   └── lang_util/
│       ├── __init__.py
│       ├── ast_util.py
│       ├── call_graph_util.py     # NEW (moved from dev/)
│       ├── call_tree_util.py      # NEW (moved from dev/)
│       └── ...
└── docs/
    └── DATA_FLOW_ANALYZER_IMPLEMENTATION_PLAN.md

dev/
└── call_graph_util/
    ├── GraphUtil.py               # UPDATED (imports from hindsight)
    ├── call_graph_stats.py        # UPDATED (imports from hindsight)
    └── call_tree_generator.py     # UPDATED (imports from hindsight)
```

### Phase 4: Implementation Steps

#### Step 1: Create Core Modules in `lang_util`

1. Create `hindsight/core/lang_util/call_graph_util.py`
   - Move `CallGraph` class from `GraphUtil.py`
   - Move `load_call_graph_from_json()` function
   - Move `print_statistics()` function

2. Create `hindsight/core/lang_util/call_tree_util.py`
   - Move call tree generation functions from `call_tree_generator.py`
   - Create `CallTreeGenerator` class as high-level API

#### Step 2: Update `dev/call_graph_util/` Scripts

1. Update `GraphUtil.py` to import from `hindsight.core.lang_util.call_graph_util`
2. Update `call_tree_generator.py` to import from `hindsight.core.lang_util.call_tree_util`
3. Update `call_graph_stats.py` to import from `hindsight.core.lang_util.call_graph_util`
4. Keep script-specific functionality (CLI parsing, sample generation) in `dev/`

#### Step 3: Create Data Flow Analyzer

1. Create `hindsight/analyzers/data_flow_analyzer.py`
   - `DataFlowAnalyzer` class extending `BaseAnalyzer`
   - `DataFlowAnalysisRunner` class extending `AnalysisRunner` with mixins
   - CLI interface with argparse

#### Step 4: Testing

1. Test that `dev/call_graph_util/` scripts still work
2. Test data flow analyzer with sample repositories
3. Verify call tree output matches expected format

### Configuration Example

```json
{
    "project_name": "MyProject",
    "llm_provider_type": "claude",
    "api_key_env_var": "ANTHROPIC_API_KEY",
    "model": "claude-sonnet-4-20250514",
    "exclude_directories": ["build", ".git", "node_modules"],
    "include_directories": ["src"],
    "max_call_depth": 20
}
```

### Usage Examples

```bash
# Basic usage
python -m hindsight.analyzers.data_flow_analyzer \
    --config config.json \
    --repo /path/to/repo

# With custom output directory
python -m hindsight.analyzers.data_flow_analyzer \
    --config config.json \
    --repo /path/to/repo \
    --out-dir ~/my_artifacts

# Force AST regeneration
python -m hindsight.analyzers.data_flow_analyzer \
    --config config.json \
    --repo /path/to/repo \
    --force-recreate-ast

# With directory filters
python -m hindsight.analyzers.data_flow_analyzer \
    --config config.json \
    --repo /path/to/repo \
    --include-directories src lib \
    --exclude-directories test vendor

# Custom call depth
python -m hindsight.analyzers.data_flow_analyzer \
    --config config.json \
    --repo /path/to/repo \
    --max-call-depth 30
```

### Output Files

The analyzer will generate:

1. **`call_tree.json`**: Complete call tree in JSON format
   ```json
   {
     "call_tree": {
       "function": "ROOT",
       "location": [],
       "children": [...]
     },
     "metadata": {
       "total_functions": 1234,
       "total_root_nodes": 56,
       "dag_edges_count": 7890
     }
   }
   ```

2. **`call_tree.txt`**: Human-readable tree format
   ```
   ROOT
   └── main  [src/main.c:10-50]
       ├── init_system  [src/init.c:5-30]
       │   └── load_config  [src/config.c:15-45]
       └── run_loop  [src/loop.c:20-100]
   ```

3. **`call_graph_statistics.json`**: Graph statistics
   ```json
   {
     "num_nodes": 1234,
     "num_edges": 5678,
     "graph_depth": 15,
     "mean_edges_per_node": 4.6,
     "num_leaf_nodes": 456,
     "num_root_nodes": 56,
     "total_paths": 12345
   }
   ```

## Summary

This implementation plan provides a structured approach to:

1. **Reuse existing infrastructure**: Leverages `AnalysisRunner`, `BaseAnalyzer`, directory classification, and AST generation
2. **Modularize call graph utilities**: Moves core logic to `hindsight/core/lang_util/` for reuse
3. **Maintain backward compatibility**: Keeps `dev/call_graph_util/` scripts working
4. **Follow established patterns**: Mirrors the architecture of `code_analyzer.py` and `trace_analyzer.py`

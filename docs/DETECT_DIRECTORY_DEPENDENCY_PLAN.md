# Implementation Plan: detect_directory_dependency.py

## Status: ✅ IMPLEMENTED

## Overview

The script `dev/detect_directory_dependency.py` analyzes a repository and outputs a directory tree where each directory node includes metadata about which other directories it depends on, based on import/include statements in source files.

## Requirements

1. **Input**: `--repo` argument specifying the repository path ✅
2. **Filtering**: Ignore directories with no files having extensions in `ALL_SUPPORTED_EXTENSIONS` ✅
3. **Output**: Directory tree format with dependency metadata per node ✅
4. **Dependency Detection**: Parse import/include directives at the top of files ✅
5. **Dependency Merging**: If `A/B/T` and `A/C/R` are computed as dependencies, merge to just `A` ✅
6. **Ignore directories starting with `.`** ✅
7. **Ignore test directories and protobuf** ✅
8. **`--ignore` argument for additional directories to exclude** ✅

## Architecture

### Key Components

```
detect_directory_dependency.py
├── DirectoryDependencyAnalyzer (main class)
│   ├── scan_directories()           # Find directories with supported files
│   ├── extract_file_dependencies()  # Parse imports/includes per file
│   ├── aggregate_directory_deps()   # Merge file deps to directory level
│   ├── merge_to_top_level()         # A/B/T + A/C/R → A
│   └── format_tree_output()         # Generate tree with metadata
└── main()                           # CLI entry point
```

### Data Structures

```python
@dataclass
class DirectoryNode:
    path: str                          # Relative path from repo root
    dependencies: Set[str]             # Set of top-level dependency directories
    children: List['DirectoryNode']    # Child directories
    has_supported_files: bool          # Whether this dir has supported files
```

## Implementation Details

### 1. Reuse Existing Patterns

The codebase already has excellent patterns to reuse:

#### Import/Include Regex Patterns (from [`scoped_ast_util.py`](hindsight/core/lang_util/scoped_ast_util.py:55))
```python
INCLUDE_RE = re.compile(r'#include\s*[<"]([^>"]+)[>"]')
SWIFT_IMPORT_RE = re.compile(r'import\s+(\w+)')
JAVA_KT_IMPORT_RE = re.compile(r'import\s+([a-zA-Z_][a-zA-Z0-9_.]*)')
GO_IMPORT_RE = re.compile(r'import\s+(?:"([^"]+)"|`([^`]+)`)')
```

#### Supported Extensions (from [`all_supported_extensions.py`](hindsight/core/lang_util/all_supported_extensions.py:10))
```python
ALL_SUPPORTED_EXTENSIONS = [".cpp", ".cc", ".c", ".mm", ".m", ".h",
                             ".swift", ".kt", ".kts", ".java", ".go"]
```

#### Tree Formatting (from [`directory_tree_util.py`](hindsight/utils/directory_tree_util.py:160))
- Use `|-- ` and `` `-- `` connectors
- Use `|   ` and `    ` for indentation

### 2. Dependency Extraction Logic

For each file with a supported extension:

1. **C/C++/Objective-C** (`.c`, `.cpp`, `.cc`, `.h`, `.m`, `.mm`):
   - Parse `#include "path/to/file.h"` and `#include <path/to/file.h>`
   - Resolve to directory containing the included file

2. **Swift** (`.swift`):
   - Parse `import ModuleName`
   - Map module to directory if found in repo

3. **Java/Kotlin** (`.java`, `.kt`, `.kts`):
   - Parse `import com.package.ClassName`
   - Convert package path to directory path

4. **Go** (`.go`):
   - Parse `import "path/to/package"`
   - Map to directory path

### 3. Dependency Merging Algorithm

```python
def merge_to_top_level(dependencies: Set[str], depth: int = 1) -> Set[str]:
    """
    Merge dependencies to top-level directories.
    
    Example:
        Input: {"A/B/T", "A/C/R", "X/Y"}
        Output: {"A", "X"} (with depth=1)
    
    Args:
        dependencies: Set of directory paths
        depth: Number of path components to keep (default: 1 = top-level only)
    
    Returns:
        Set of merged top-level directories
    """
    merged = set()
    for dep in dependencies:
        parts = Path(dep).parts
        if len(parts) >= depth:
            merged.add(str(Path(*parts[:depth])))
        else:
            merged.add(dep)
    return merged
```

### 4. Output Format

```
repo_name/
|-- src/                              [depends on: lib, utils]
|   |-- core/                         [depends on: lib]
|   |   |-- engine.cpp
|   |   `-- engine.h
|   `-- utils/                        [depends on: lib]
|       |-- helper.cpp
|       `-- helper.h
|-- lib/                              [no dependencies]
|   |-- base.cpp
|   `-- base.h
`-- tests/                            [depends on: src, lib]
    `-- test_engine.cpp
```

### 5. File Structure

```python
#!/usr/bin/env python3
"""
Detect directory dependencies in a repository.

Analyzes import/include statements to determine which directories
depend on which other directories.

Usage:
    python -m dev.detect_directory_dependency --repo ~/src/my-project
    python -m dev.detect_directory_dependency --repo ~/src/my-project --depth 2
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Optional

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from hindsight.utils.log_util import setup_default_logging, get_logger

# Pre-compiled regex patterns (reused from scoped_ast_util.py)
INCLUDE_RE = re.compile(r'#include\s*[<"]([^>"]+)[>"]')
SWIFT_IMPORT_RE = re.compile(r'import\s+(\w+)')
JAVA_KT_IMPORT_RE = re.compile(r'import\s+([a-zA-Z_][a-zA-Z0-9_.]*)')
GO_IMPORT_RE = re.compile(r'import\s+(?:"([^"]+)"|`([^`]+)`)')


@dataclass
class DirectoryNode:
    """Represents a directory in the dependency tree."""
    path: str
    dependencies: Set[str] = field(default_factory=set)
    children: List['DirectoryNode'] = field(default_factory=list)
    has_supported_files: bool = False


class DirectoryDependencyAnalyzer:
    """Analyzes directory dependencies based on import/include statements."""
    
    def __init__(self, repo_path: Path, merge_depth: int = 1):
        self.repo_path = repo_path.resolve()
        self.merge_depth = merge_depth
        self.supported_extensions = set(ext.lower() for ext in ALL_SUPPORTED_EXTENSIONS)
        self.file_index: Dict[str, List[Path]] = {}
        self.logger = get_logger(__name__)
    
    def analyze(self) -> DirectoryNode:
        """Main entry point - analyze the repository."""
        # Build file index for dependency resolution
        self._build_file_index()
        
        # Build directory tree with dependencies
        root = self._build_directory_tree(self.repo_path)
        
        return root
    
    def _build_file_index(self) -> None:
        """Build index mapping filenames to paths for dependency resolution."""
        # ... implementation
    
    def _build_directory_tree(self, path: Path) -> Optional[DirectoryNode]:
        """Recursively build directory tree with dependencies."""
        # ... implementation
    
    def _extract_file_dependencies(self, file_path: Path) -> Set[str]:
        """Extract dependencies from a single file."""
        # ... implementation
    
    def _resolve_dependency_to_directory(self, dep: str, source_file: Path) -> Optional[str]:
        """Resolve a dependency reference to a directory path."""
        # ... implementation
    
    def _merge_dependencies(self, dependencies: Set[str]) -> Set[str]:
        """Merge dependencies to top-level directories."""
        # ... implementation
    
    def format_tree(self, node: DirectoryNode, prefix: str = "") -> str:
        """Format the directory tree with dependencies as a string."""
        # ... implementation


def main():
    parser = argparse.ArgumentParser(
        description="Detect directory dependencies in a repository"
    )
    parser.add_argument("--repo", "-r", type=Path, required=True,
                        help="Path to the repository to analyze")
    parser.add_argument("--depth", "-d", type=int, default=1,
                        help="Merge depth for dependencies (default: 1 = top-level)")
    parser.add_argument("--exclude", "-e", nargs="*", default=[".git", "build", "node_modules"],
                        help="Directories to exclude from analysis")
    
    args = parser.parse_args()
    
    setup_default_logging()
    
    analyzer = DirectoryDependencyAnalyzer(args.repo, args.depth)
    tree = analyzer.analyze()
    print(analyzer.format_tree(tree))


if __name__ == "__main__":
    main()
```

## Implementation Steps

### Phase 1: Core Infrastructure
1. [ ] Create `dev/detect_directory_dependency.py` with basic structure
2. [ ] Implement `_build_file_index()` - scan repo and index files by name
3. [ ] Implement directory filtering (only dirs with supported files)

### Phase 2: Dependency Extraction
4. [ ] Implement `_extract_file_dependencies()` for C/C++/Obj-C
5. [ ] Implement `_extract_file_dependencies()` for Swift
6. [ ] Implement `_extract_file_dependencies()` for Java/Kotlin
7. [ ] Implement `_extract_file_dependencies()` for Go

### Phase 3: Dependency Resolution
8. [ ] Implement `_resolve_dependency_to_directory()` - map imports to directories
9. [ ] Implement `_merge_dependencies()` - merge to top-level

### Phase 4: Output Formatting
10. [ ] Implement `_build_directory_tree()` - recursive tree building
11. [ ] Implement `format_tree()` - ASCII tree with metadata

### Phase 5: Testing & Polish
12. [ ] Add unit tests
13. [ ] Test on sample repositories
14. [ ] Add documentation

## Edge Cases to Handle

1. **Circular dependencies**: A depends on B, B depends on A
2. **Self-dependencies**: Directory depends on itself (should be excluded)
3. **External dependencies**: Imports that don't resolve to repo directories
4. **Symlinks**: Handle symbolic links properly
5. **Empty directories**: Skip directories with no supported files
6. **Nested includes**: `#include "../other/file.h"` with relative paths

## Testing Strategy

1. **Unit tests**: Test each extraction method with sample code snippets
2. **Integration tests**: Test on small sample repositories
3. **Real-world tests**: Test on actual codebases like the Hindsight project itself

## Example Usage

```bash
# Basic usage
python -m dev.detect_directory_dependency --repo ~/src/my-project

# With custom merge depth (keep 2 levels)
python -m dev.detect_directory_dependency --repo ~/src/my-project --depth 2

# Exclude additional directories
python -m dev.detect_directory_dependency --repo ~/src/my-project --exclude .git build vendor
```

## Dependencies

- Python 3.8+
- No external dependencies (uses only stdlib + existing hindsight modules)

## Related Files

- [`hindsight/core/lang_util/all_supported_extensions.py`](hindsight/core/lang_util/all_supported_extensions.py) - Extension list
- [`hindsight/core/lang_util/scoped_ast_util.py`](hindsight/core/lang_util/scoped_ast_util.py) - Import parsing patterns
- [`hindsight/utils/directory_tree_util.py`](hindsight/utils/directory_tree_util.py) - Tree formatting
- [`dev/generate_repo_config.py`](dev/generate_repo_config.py) - Example dev script pattern

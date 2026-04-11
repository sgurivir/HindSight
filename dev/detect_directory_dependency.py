#!/usr/bin/env python3
"""
Detect directory dependencies in a repository.

Analyzes import/include statements to determine which directories
depend on which other directories. Outputs a tree format where each
directory node includes metadata about its dependencies.

Usage:
    python -m dev.detect_directory_dependency --repo ~/src/my-project
    python -m dev.detect_directory_dependency --repo ~/src/my-project --depth 2
    python -m dev.detect_directory_dependency --repo ~/src/my-project --ignore vendor cache
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from hindsight.utils.log_util import setup_default_logging, get_logger

# Initialize logger
logger = get_logger(__name__)

# Pre-compiled regex patterns (reused from scoped_ast_util.py)
INCLUDE_RE = re.compile(r'#include\s*[<"]([^>"]+)[>"]')
SWIFT_IMPORT_RE = re.compile(r'import\s+(\w+)')
JAVA_KT_IMPORT_RE = re.compile(r'import\s+([a-zA-Z_][a-zA-Z0-9_.]*)')
GO_IMPORT_RE = re.compile(r'import\s+(?:"([^"]+)"|`([^`]+)`|\(\s*"([^"]+)")')
# Additional pattern for Go multi-line imports
GO_IMPORT_BLOCK_RE = re.compile(r'import\s*\((.*?)\)', re.DOTALL)
GO_IMPORT_LINE_RE = re.compile(r'"([^"]+)"')


@dataclass
class DirectoryNode:
    """Represents a directory in the dependency tree."""
    path: str
    dependencies: Set[str] = field(default_factory=set)
    children: List['DirectoryNode'] = field(default_factory=list)
    has_supported_files: bool = False
    file_count: int = 0


class DirectoryDependencyAnalyzer:
    """Analyzes directory dependencies based on import/include statements."""
    
    # Default directories to exclude (by exact name match)
    DEFAULT_EXCLUDE_DIRS = {
        'build', 'Build', 'DerivedData', 'cmake-build-debug', 'cmake-build-release',  # Build outputs
        'node_modules', 'vendor', 'third_party', 'thirdparty', '3rdparty',  # Dependencies
        '__pycache__',  # Python caches
        'Pods', 'Carthage',  # iOS dependencies
        'venv', 'env',  # Python virtual environments
        'site-packages',  # Python packages
        'Test', 'Tests', 'test', 'tests',  # Test directories
        'protobuf',  # Protobuf generated code
    }
    
    def __init__(self, repo_path: Path, merge_depth: int = 1, exclude_dirs: Optional[Set[str]] = None):
        """
        Initialize the analyzer.
        
        Args:
            repo_path: Path to the repository root
            merge_depth: Number of path components to keep when merging dependencies
            exclude_dirs: Additional directories to exclude from analysis
        """
        self.repo_path = repo_path.resolve()
        self.merge_depth = merge_depth
        self.supported_extensions = set(ext.lower() for ext in ALL_SUPPORTED_EXTENSIONS)
        self.exclude_dirs = self.DEFAULT_EXCLUDE_DIRS.copy()
        if exclude_dirs:
            self.exclude_dirs.update(exclude_dirs)
        
        # File index: maps filename -> list of paths
        self.file_index: Dict[str, List[Path]] = {}
        
        # Directory index: maps directory path -> set of files in it
        self.dir_index: Dict[str, Set[Path]] = {}
        
        # Directory name index: maps directory name -> list of relative paths
        # This helps resolve module imports like "import SafariShared" to "SafariShared/" directory
        self.dir_name_index: Dict[str, List[str]] = {}
        
        # All directories with supported files
        self.valid_directories: Set[str] = set()
    
    def analyze(self) -> DirectoryNode:
        """
        Main entry point - analyze the repository.
        
        Returns:
            DirectoryNode: Root node of the dependency tree
        """
        logger.info(f"Analyzing repository: {self.repo_path}")
        
        # Phase 1: Build file index
        logger.info("Phase 1: Building file index...")
        self._build_file_index()
        logger.info(f"  Found {len(self.file_index)} unique filenames")
        logger.info(f"  Found {len(self.valid_directories)} directories with supported files")
        
        # Phase 2: Build directory tree with dependencies
        logger.info("Phase 2: Building dependency tree...")
        root = self._build_directory_tree(self.repo_path)
        
        if root is None:
            # Create empty root if no supported files found
            root = DirectoryNode(path=self.repo_path.name)
        
        logger.info("Analysis complete!")
        return root
    
    def _build_file_index(self) -> None:
        """Build index mapping filenames to paths for dependency resolution."""
        for path in self.repo_path.rglob("*"):
            # Skip excluded directories
            if self._is_excluded(path):
                continue
            
            if path.is_file():
                # Check if file has supported extension
                if path.suffix.lower() in self.supported_extensions:
                    # Add to file index
                    filename = path.name
                    if filename not in self.file_index:
                        self.file_index[filename] = []
                    self.file_index[filename].append(path)
                    
                    # Add to directory index
                    rel_dir = self._get_relative_dir(path.parent)
                    if rel_dir not in self.dir_index:
                        self.dir_index[rel_dir] = set()
                    self.dir_index[rel_dir].add(path)
                    
                    # Mark directory as valid
                    self.valid_directories.add(rel_dir)
                    
                    # Build directory name index for this directory
                    dir_name = path.parent.name
                    if dir_name and dir_name not in self.dir_name_index:
                        self.dir_name_index[dir_name] = []
                    if dir_name and rel_dir not in self.dir_name_index[dir_name]:
                        self.dir_name_index[dir_name].append(rel_dir)
                    
                    # Also mark all parent directories as valid (they contain valid subdirs)
                    parent = path.parent
                    while parent != self.repo_path:
                        parent_rel = self._get_relative_dir(parent)
                        self.valid_directories.add(parent_rel)
                        # Also add parent to dir_name_index
                        parent_name = parent.name
                        if parent_name:
                            if parent_name not in self.dir_name_index:
                                self.dir_name_index[parent_name] = []
                            if parent_rel not in self.dir_name_index[parent_name]:
                                self.dir_name_index[parent_name].append(parent_rel)
                        parent = parent.parent
    
    def _is_excluded(self, path: Path) -> bool:
        """Check if a path should be excluded."""
        for part in path.parts:
            # Exclude directories starting with '.'
            if part.startswith('.'):
                return True
            # Exclude directories in the exclude set
            if part in self.exclude_dirs:
                return True
        return False
    
    def _get_relative_dir(self, dir_path: Path) -> str:
        """Get directory path relative to repo root."""
        try:
            return str(dir_path.relative_to(self.repo_path))
        except ValueError:
            return str(dir_path)
    
    def _build_directory_tree(self, path: Path, depth: int = 0) -> Optional[DirectoryNode]:
        """
        Recursively build directory tree with dependencies.
        
        Args:
            path: Current directory path
            depth: Current depth in tree
            
        Returns:
            DirectoryNode or None if directory should be skipped
        """
        rel_path = self._get_relative_dir(path)
        
        # Skip if not a valid directory (no supported files anywhere in subtree)
        if rel_path != "." and rel_path not in self.valid_directories:
            return None
        
        # Create node
        node = DirectoryNode(
            path=rel_path if rel_path != "." else self.repo_path.name
        )
        
        # Get files in this directory
        files_in_dir = self.dir_index.get(rel_path, set())
        node.has_supported_files = len(files_in_dir) > 0
        node.file_count = len(files_in_dir)
        
        # Extract dependencies from files in this directory
        raw_dependencies: Set[str] = set()
        for file_path in files_in_dir:
            file_deps = self._extract_file_dependencies(file_path)
            raw_dependencies.update(file_deps)
        
        # Merge dependencies to top-level
        node.dependencies = self._merge_dependencies(raw_dependencies, rel_path)
        
        # Process children
        try:
            children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return node
        
        for child in children:
            if child.is_dir() and not self._is_excluded(child):
                child_node = self._build_directory_tree(child, depth + 1)
                if child_node is not None:
                    node.children.append(child_node)
        
        return node
    
    def _extract_file_dependencies(self, file_path: Path) -> Set[str]:
        """
        Extract dependencies from a single file.
        
        Args:
            file_path: Path to the file to analyze
            
        Returns:
            Set of directory paths that this file depends on
        """
        dependencies: Set[str] = set()
        
        try:
            # Read only the first part of the file (imports are usually at the top)
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read first 200 lines or 10KB, whichever comes first
                content_lines = []
                total_chars = 0
                for i, line in enumerate(f):
                    if i >= 200 or total_chars >= 10000:
                        break
                    content_lines.append(line)
                    total_chars += len(line)
                content = ''.join(content_lines)
        except Exception as e:
            logger.debug(f"Could not read file {file_path}: {e}")
            return dependencies
        
        suffix = file_path.suffix.lower()
        
        # Extract based on file type
        if suffix in ['.c', '.cpp', '.cc', '.cxx', '.h', '.hpp', '.m', '.mm']:
            dependencies.update(self._extract_c_dependencies(content, file_path))
        elif suffix == '.swift':
            dependencies.update(self._extract_swift_dependencies(content, file_path))
        elif suffix in ['.java', '.kt', '.kts']:
            dependencies.update(self._extract_java_kotlin_dependencies(content, file_path))
        elif suffix == '.go':
            dependencies.update(self._extract_go_dependencies(content, file_path))
        
        return dependencies
    
    def _extract_c_dependencies(self, content: str, source_file: Path) -> Set[str]:
        """Extract C/C++/Objective-C dependencies from #include statements."""
        dependencies: Set[str] = set()
        
        includes = INCLUDE_RE.findall(content)
        
        for include in includes:
            resolved = self._resolve_include_to_directory(include, source_file)
            if resolved:
                dependencies.add(resolved)
        
        return dependencies
    
    def _extract_swift_dependencies(self, content: str, source_file: Path) -> Set[str]:
        """Extract Swift dependencies from import statements."""
        dependencies: Set[str] = set()
        
        imports = SWIFT_IMPORT_RE.findall(content)
        source_rel_dir = self._get_relative_dir(source_file.parent)
        
        for import_name in imports:
            # Skip system frameworks
            if import_name in ['Foundation', 'UIKit', 'SwiftUI', 'Combine', 'CoreData',
                              'CoreGraphics', 'CoreLocation', 'MapKit', 'AVFoundation',
                              'XCTest', 'Darwin', 'Dispatch', 'os', 'Swift', 'ObjectiveC',
                              'Security', 'Network', 'Accelerate', 'Metal', 'MetalKit',
                              'SceneKit', 'SpriteKit', 'GameplayKit', 'ARKit', 'RealityKit',
                              'CloudKit', 'StoreKit', 'HealthKit', 'HomeKit', 'WatchKit',
                              'AppKit', 'Cocoa', 'WebKit', 'SafariServices', 'AuthenticationServices',
                              'CryptoKit', 'LocalAuthentication', 'CoreML', 'Vision', 'NaturalLanguage',
                              'CoreBluetooth', 'CoreNFC', 'CoreMotion', 'CoreTelephony',
                              'CoreServices', 'CoreFoundation', 'CoreText', 'CoreImage',
                              'CoreAnimation', 'CoreAudio', 'CoreMedia', 'CoreVideo',
                              'QuartzCore', 'ImageIO', 'PDFKit', 'PhotosUI', 'Photos',
                              'Contacts', 'ContactsUI', 'EventKit', 'EventKitUI',
                              'MessageUI', 'Messages', 'NotificationCenter', 'UserNotifications',
                              'BackgroundTasks', 'CallKit', 'CarPlay', 'ClassKit',
                              'ClockKit', 'Compression', 'Intents', 'IntentsUI', 'LinkPresentation',
                              'MediaPlayer', 'MultipeerConnectivity', 'PassKit', 'PencilKit',
                              'PushKit', 'QuickLook', 'QuickLookThumbnailing', 'ReplayKit',
                              'Social', 'Speech', 'SystemConfiguration', 'UniformTypeIdentifiers',
                              'VideoSubscriberAccount', 'WidgetKit', 'OSLog', 'Observation']:
                continue
            
            found = False
            
            # First, try to find a directory with this name (module/framework)
            if import_name in self.dir_name_index:
                for dir_path in self.dir_name_index[import_name]:
                    if dir_path in self.valid_directories and dir_path != source_rel_dir:
                        dependencies.add(dir_path)
                        found = True
                        break
            
            # If not found as directory, try to find a Swift file with this name
            if not found:
                swift_filename = f"{import_name}.swift"
                candidates = self.file_index.get(swift_filename, [])
                
                for candidate in candidates:
                    rel_dir = self._get_relative_dir(candidate.parent)
                    if rel_dir in self.valid_directories and rel_dir != source_rel_dir:
                        dependencies.add(rel_dir)
                        break
        
        return dependencies
    
    def _extract_java_kotlin_dependencies(self, content: str, source_file: Path) -> Set[str]:
        """Extract Java/Kotlin dependencies from import statements."""
        dependencies: Set[str] = set()
        
        imports = JAVA_KT_IMPORT_RE.findall(content)
        
        for import_stmt in imports:
            # Skip standard library imports
            if import_stmt.startswith(('java.', 'javax.', 'kotlin.', 'kotlinx.',
                                       'android.', 'androidx.', 'com.google.',
                                       'org.junit', 'org.mockito')):
                continue
            
            # Convert package.ClassName to potential file
            parts = import_stmt.split('.')
            if len(parts) > 1:
                class_name = parts[-1]
                
                # Look for Java/Kotlin files with this class name
                for ext in ['.java', '.kt']:
                    class_filename = f"{class_name}{ext}"
                    candidates = self.file_index.get(class_filename, [])
                    
                    for candidate in candidates:
                        rel_dir = self._get_relative_dir(candidate.parent)
                        if rel_dir in self.valid_directories:
                            dependencies.add(rel_dir)
                            break
        
        return dependencies
    
    def _extract_go_dependencies(self, content: str, source_file: Path) -> Set[str]:
        """Extract Go dependencies from import statements."""
        dependencies: Set[str] = set()
        
        # Handle single imports
        single_imports = GO_IMPORT_RE.findall(content)
        for import_tuple in single_imports:
            import_path = import_tuple[0] or import_tuple[1] or import_tuple[2]
            if import_path:
                resolved = self._resolve_go_import(import_path, source_file)
                if resolved:
                    dependencies.add(resolved)
        
        # Handle import blocks
        import_blocks = GO_IMPORT_BLOCK_RE.findall(content)
        for block in import_blocks:
            block_imports = GO_IMPORT_LINE_RE.findall(block)
            for import_path in block_imports:
                resolved = self._resolve_go_import(import_path, source_file)
                if resolved:
                    dependencies.add(resolved)
        
        return dependencies
    
    def _resolve_include_to_directory(self, include: str, source_file: Path) -> Optional[str]:
        """
        Resolve a C/C++ #include to a directory path.
        
        Args:
            include: The include path (e.g., "utils/helper.h" or "helper.h")
            source_file: The file containing the include
            
        Returns:
            Relative directory path or None if not found
        """
        # Try relative to source file first
        source_dir = source_file.parent
        potential_path = source_dir / include
        
        if potential_path.exists():
            rel_dir = self._get_relative_dir(potential_path.parent)
            if rel_dir in self.valid_directories:
                return rel_dir
        
        # Try to find by filename in index
        filename = Path(include).name
        candidates = self.file_index.get(filename, [])
        
        for candidate in candidates:
            rel_dir = self._get_relative_dir(candidate.parent)
            if rel_dir in self.valid_directories:
                return rel_dir
        
        return None
    
    def _resolve_go_import(self, import_path: str, source_file: Path) -> Optional[str]:
        """
        Resolve a Go import to a directory path.
        
        Args:
            import_path: The import path
            source_file: The file containing the import
            
        Returns:
            Relative directory path or None if not found
        """
        # Skip standard library
        if not '/' in import_path or import_path.startswith('golang.org/'):
            return None
        
        # For relative imports
        if import_path.startswith('.'):
            source_dir = source_file.parent
            potential_path = (source_dir / import_path).resolve()
            
            try:
                rel_dir = self._get_relative_dir(potential_path)
                if rel_dir in self.valid_directories:
                    return rel_dir
            except ValueError:
                pass
        
        # Try to match the last part of the import path to a directory
        import_parts = import_path.split('/')
        for i in range(len(import_parts)):
            partial = '/'.join(import_parts[i:])
            if partial in self.valid_directories:
                return partial
        
        return None
    
    def _merge_dependencies(self, dependencies: Set[str], source_dir: str) -> Set[str]:
        """
        Merge dependencies to top-level directories.
        
        Args:
            dependencies: Set of directory paths
            source_dir: The source directory (to exclude self-dependencies)
            
        Returns:
            Set of merged top-level directories
        """
        merged: Set[str] = set()
        
        for dep in dependencies:
            # Skip self-dependencies
            if dep == source_dir or dep == ".":
                continue
            
            # Skip if dependency is a parent of source
            if source_dir.startswith(dep + "/"):
                continue
            
            # Skip if source is a parent of dependency
            if dep.startswith(source_dir + "/"):
                continue
            
            # Merge to specified depth
            parts = Path(dep).parts
            if len(parts) >= self.merge_depth:
                merged_path = str(Path(*parts[:self.merge_depth]))
            else:
                merged_path = dep
            
            # Only add if it's a valid directory
            if merged_path in self.valid_directories or merged_path == dep:
                merged.add(merged_path)
        
        return merged
    
    def format_tree(self, node: DirectoryNode, prefix: str = "", is_last: bool = True) -> str:
        """
        Format the directory tree with dependencies as a string.
        
        Args:
            node: The directory node to format
            prefix: Current line prefix for tree drawing
            is_last: Whether this is the last child in its parent
            
        Returns:
            Formatted tree string
        """
        lines: List[str] = []
        
        # Format current node
        if prefix == "":
            # Root node
            node_line = f"{node.path}/"
        else:
            connector = "`-- " if is_last else "|-- "
            node_line = f"{prefix}{connector}{Path(node.path).name}/"
        
        # Add dependency metadata
        if node.dependencies:
            sorted_deps = sorted(node.dependencies)
            deps_str = ", ".join(sorted_deps)
            node_line += f"  [depends on: {deps_str}]"
        elif node.has_supported_files:
            node_line += "  [no dependencies]"
        
        lines.append(node_line)
        
        # Format children
        child_prefix = prefix + ("    " if is_last else "|   ") if prefix else ""
        
        for i, child in enumerate(node.children):
            child_is_last = (i == len(node.children) - 1)
            child_tree = self.format_tree(child, child_prefix, child_is_last)
            lines.append(child_tree)
        
        return "\n".join(lines)
    
    def get_dependency_summary(self, node: DirectoryNode) -> Dict[str, Set[str]]:
        """
        Get a summary of all dependencies in the tree.
        
        Args:
            node: Root node of the tree
            
        Returns:
            Dictionary mapping directory paths to their dependencies
        """
        summary: Dict[str, Set[str]] = {}
        
        def collect(n: DirectoryNode):
            if n.dependencies:
                summary[n.path] = n.dependencies.copy()
            for child in n.children:
                collect(child)
        
        collect(node)
        return summary


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Detect directory dependencies in a repository",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic usage
    python -m dev.detect_directory_dependency --repo ~/src/my-project
    
    # With custom merge depth (keep 2 levels)
    python -m dev.detect_directory_dependency --repo ~/src/my-project --depth 2
    
    # Ignore additional directories
    python -m dev.detect_directory_dependency --repo ~/src/my-project --ignore vendor cache
    
    # Show summary only
    python -m dev.detect_directory_dependency --repo ~/src/my-project --summary
        """
    )
    
    parser.add_argument(
        "--repo", "-r",
        type=Path,
        required=True,
        help="Path to the repository to analyze"
    )
    
    parser.add_argument(
        "--depth", "-d",
        type=int,
        default=1,
        help="Merge depth for dependencies (default: 1 = top-level only)"
    )
    
    parser.add_argument(
        "--ignore", "-i",
        nargs="*",
        default=[],
        help="Additional directories to ignore/exclude from analysis"
    )
    
    parser.add_argument(
        "--summary", "-s",
        action="store_true",
        help="Show dependency summary instead of tree"
    )
    
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress informational logging"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_default_logging()
    if args.quiet:
        import logging
        logging.getLogger().setLevel(logging.WARNING)
    
    # Validate repo path
    if not args.repo.exists():
        print(f"Error: Repository path does not exist: {args.repo}", file=sys.stderr)
        sys.exit(1)
    
    if not args.repo.is_dir():
        print(f"Error: Repository path is not a directory: {args.repo}", file=sys.stderr)
        sys.exit(1)
    
    # Create analyzer
    exclude_dirs = set(args.ignore) if args.ignore else None
    analyzer = DirectoryDependencyAnalyzer(
        repo_path=args.repo,
        merge_depth=args.depth,
        exclude_dirs=exclude_dirs
    )
    
    # Run analysis
    tree = analyzer.analyze()
    
    # Output results
    if args.summary:
        summary = analyzer.get_dependency_summary(tree)
        print("\nDependency Summary:")
        print("=" * 50)
        for dir_path, deps in sorted(summary.items()):
            print(f"\n{dir_path}/")
            for dep in sorted(deps):
                print(f"  -> {dep}/")
    else:
        print("\n" + analyzer.format_tree(tree))


if __name__ == "__main__":
    main()

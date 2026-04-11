#!/usr/bin/env python3
import argparse
from pathlib import Path

from ..core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS


class DirectoryTreeUtil:
    DEFAULT_EXTS = ALL_SUPPORTED_EXTENSIONS

    @staticmethod
    def _resolve_anywhere(repo_root: Path, rel: str) -> Path:
        """
        Resolve rel to a path inside repo_root:
        1) absolute path exists
        2) repo_root / rel exists
        3) recursively search for unique path whose relative parts end with rel
        """
        candidate = Path(rel).expanduser()

        # 1) absolute path
        if candidate.is_absolute() and candidate.exists():
            return candidate

        # 2) direct child of repo
        joined = (repo_root / rel).resolve()
        try:
            joined.relative_to(repo_root)
            if joined.exists():
                return joined
        except Exception:
            pass

        # 3) recursive tail match
        want_parts = Path(rel).parts
        matches = []
        for p in repo_root.rglob('*'):
            try:
                # Try to get relative path without resolving symbolic links first
                rp = p.relative_to(repo_root)
            except ValueError:
                # If that fails, try with resolved paths
                try:
                    rp = p.resolve().relative_to(repo_root.resolve())
                except Exception:
                    continue
            if rp.parts[-len(want_parts):] == want_parts:
                matches.append(p)

        if not matches:
            return None
        if len(matches) > 1:
            print("Ambiguous path. Multiple matches:")
            for m in sorted(matches):
                try:
                    # Try to get relative path without resolving symbolic links first
                    rel_path = m.relative_to(repo_root)
                    print(f" - {rel_path}")
                except ValueError:
                    # If that fails, try with resolved paths
                    try:
                        rel_path = m.resolve().relative_to(repo_root.resolve())
                        print(f" - {rel_path}")
                    except Exception:
                        print(f" - {m}")
            return None
        return matches[0]

    @staticmethod
    def get_directory_listing(repo_path: str, relative_path: str, recursive: bool = False, max_depth: int = 6) -> str:
        """
        Print ASCII tree of files and subdirectories.
        If a file is given, show its character size.
        This is used by LLM Tools.
        
        Args:
            repo_path: Base repository path
            relative_path: Relative path to list (or file to inspect)
            recursive: If True, list files recursively in tree format. Default is False (single level).
            max_depth: Maximum depth for recursive listing. Default is 6.
            
        Returns:
            str: Formatted directory listing or file info
        """
        repo_root = Path(repo_path).expanduser().resolve()
        if not repo_root.exists() or not repo_root.is_dir():
            return f"Path not found: {repo_path}"

        base_path = DirectoryTreeUtil._resolve_anywhere(repo_root, relative_path)
        if base_path is None:
            return f"Path not found: {relative_path}"

        # Determine relative display path to repo
        try:
            relative_display = str(base_path.relative_to(repo_root))
        except ValueError:
            relative_display = str(base_path.name)

        # --- Handle file case ---
        if base_path.is_file():
            try:
                size = base_path.stat().st_size
                return f"{relative_display} ({size} chars)"
            except OSError:
                return f"{relative_display} (size unavailable)"

        # --- Handle directory case ---
        if recursive:
            return DirectoryTreeUtil._get_recursive_listing(base_path, relative_display, max_depth)
        else:
            return DirectoryTreeUtil._get_single_level_listing(base_path, relative_display, relative_path)

    @staticmethod
    def _get_single_level_listing(base_path: Path, relative_display: str, relative_path: str) -> str:
        """
        Get single-level directory listing (existing behavior).
        
        Args:
            base_path: Resolved path to the directory
            relative_display: Display name for the directory
            relative_path: Original relative path for error messages
            
        Returns:
            str: Formatted single-level directory listing
        """
        exts = {e.lower() for e in DirectoryTreeUtil.DEFAULT_EXTS}
        lines = [f"{relative_display}/"]

        for entry in sorted(base_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.is_dir():
                lines.append(f"|-- {entry.name}/")
            elif entry.is_file() and entry.suffix.lower() in exts:
                try:
                    size = entry.stat().st_size
                    lines.append(f"|-- {entry.name} Size : ({size} chars)")
                except OSError:
                    lines.append(f"|-- {entry.name} (size unavailable)")

        if len(lines) == 1:
            lines.append(f"|-- (no supported files or subdirectories in {relative_path})")

        return "\n".join(lines)

    @staticmethod
    def _get_recursive_listing(base_path: Path, relative_display: str, max_depth: int = 6) -> str:
        """
        Get recursive directory listing in tree format.
        
        Args:
            base_path: Resolved path to the directory
            relative_display: Display name for the directory
            max_depth: Maximum depth to traverse
            
        Returns:
            str: Formatted recursive directory tree
        """
        exts = {e.lower() for e in DirectoryTreeUtil.DEFAULT_EXTS}
        lines = [f"{relative_display}/"]
        
        def _format_tree_recursive(current_path: Path, prefix: str, depth: int) -> None:
            """Recursively format directory tree."""
            if depth > max_depth:
                return
                
            try:
                entries = sorted(current_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except (PermissionError, OSError):
                return
                
            # Filter entries: directories and files with supported extensions
            filtered_entries = []
            for entry in entries:
                if entry.is_dir():
                    filtered_entries.append(entry)
                elif entry.is_file() and entry.suffix.lower() in exts:
                    filtered_entries.append(entry)
            
            for i, entry in enumerate(filtered_entries):
                is_last = (i == len(filtered_entries) - 1)
                connector = "`-- " if is_last else "|-- "
                
                if entry.is_dir():
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    # Recurse into subdirectory
                    new_prefix = prefix + ("    " if is_last else "|   ")
                    _format_tree_recursive(entry, new_prefix, depth + 1)
                else:
                    try:
                        size = entry.stat().st_size
                        lines.append(f"{prefix}{connector}{entry.name} Size : ({size} chars)")
                    except OSError:
                        lines.append(f"{prefix}{connector}{entry.name} (size unavailable)")
        
        _format_tree_recursive(base_path, "", 0)
        
        if len(lines) == 1:
            lines.append("|-- (no supported files or subdirectories)")
        
        return "\n".join(lines)

    @staticmethod
    def get_exclude_directories_for(repo_path: str) -> str:
        # This method appears to be incomplete - keeping as placeholder
        pass




def main():
    parser = argparse.ArgumentParser(description="Print directory tree or file info under a repo")
    parser.add_argument("-r", "--repo_path", required=True, help="Base repository path")
    parser.add_argument("-p", "--relative_path", help="Relative/absolute path or file to inspect")
    parser.add_argument("--analyze-dirs", action="store_true", default=False,
                       help="Analyze repository to get include and exclude directory lists")
    parser.add_argument("--list-dirs", action="store_true", default=False,
                       help="Print dir listing")
    parser.add_argument("-i", "--user_provided_include_list", nargs="*", default=None,
                       help="List of directory names or relative paths to include (e.g., -i core utils)")
    parser.add_argument("-e", "--user_provided_exclude_list", nargs="*", default=None,
                       help="List of directory names to exclude in addition to defaults (e.g., -e temp cache)")

    args = parser.parse_args()
    
    if args.analyze_dirs:
        from .directory_analysis_printer import print_directory_analysis_with_filters
        print_directory_analysis_with_filters(args.repo_path, args.user_provided_include_list, args.user_provided_exclude_list)
    
    if args.list_dirs:
        tree = DirectoryTreeUtil.get_directory_listing(args.repo_path, args.relative_path)
        print(tree)


if __name__ == "__main__":
    main()
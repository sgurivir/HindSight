from __future__ import annotations

import argparse
import os
import pickle
import sys
import json
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Iterable, Set, Optional

from .log_util import get_logger, setup_default_logging
from hindsight.core.lang_util.all_supported_extensions import ALL_SUPPORTED_EXTENSIONS
from hindsight.utils.output_directory_provider import get_output_directory_provider

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Initialize logger
logger = get_logger(__name__)


class FileContentProvider:
    """
    Singleton for resolving and indexing files in a repo.

    IMPORTANT: This class should ONLY be instantiated in AnalysisRunner.py using:
        FileContentProvider.from_repo(repo_path, index_path, ignore_dirs, include_extensions, out_path)
    or:
        FileContentProvider.from_index(index_pickle_or_json_path)

    All other classes should use the public class methods (e.g., resolve_file_path, guess_path, exists, read_text, save_index).
    """

    _instance: Optional["FileContentProvider"] = None

    # -------- Singleton core --------
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._initialized = False
            cls._instance = inst
        return cls._instance

    def __init__(
        self,
        repo_path: Optional[str] = None,
        index_path: Optional[str] = None,
        ignore_dirs: Optional[Iterable[str]] = None,
        include_extensions: Optional[Iterable[str]] = None,
        out_path: Optional[str] = None,
        file_name_mapping_index: Optional[str] = None,
    ):
        if self._initialized:
            return

        # Construction modes:
        #   A) from_repo: repo_path + params
        #   B) from_index: file_name_mapping_index
        self.repo_root: Optional[Path] = Path(repo_path).resolve() if repo_path else None
        self.index_path: Optional[Path] = Path(index_path).resolve() if index_path else None
        self.ignore_dirs: Set[str] = set(ignore_dirs or [])
        self.include_extensions: Set[str] = {e.lower() for e in (include_extensions or [])}
        self.out_path: Optional[Path] = Path(out_path).resolve() if out_path else None

        # name -> list[absolute_path] (allow multiple matches for same filename)
        self.name_to_path_mapping: Dict[str, List[str]] = defaultdict(list)

        # Optional: load mapping immediately if index provided
        if file_name_mapping_index:
            self._load_mapping_from_index(Path(file_name_mapping_index))

        self._initialized = True

    @classmethod
    def from_repo(cls, repo_path: str) -> "FileContentProvider":
        """
        Build/initialize singleton using a repository path.
        Automatically honors ALL_SUPPORTED_EXTENSIONS and builds the file mapping index.
        
        Args:
            repo_path: Path to the repository root
            
        Returns:
            FileContentProvider singleton instance
        """
        # Determine index path automatically
        try:
            output_provider = get_output_directory_provider()
            if output_provider.is_configured():
                artifacts_dir = output_provider.get_repo_artifacts_dir()
                index_path = f"{artifacts_dir}/code_insights/file_mapping.pkl"
            else:
                # Fallback to repo-relative path
                index_path = f"{repo_path}/file_mapping.pkl"
        except Exception:
            # Fallback to repo-relative path
            index_path = f"{repo_path}/file_mapping.pkl"
        
        # Create instance with ALL_SUPPORTED_EXTENSIONS
        inst = cls(
            repo_path=repo_path,
            index_path=index_path,
            ignore_dirs=None,  # Use defaults
            include_extensions=ALL_SUPPORTED_EXTENSIONS,
            out_path=None,
        )
        
        # Always build index and save it
        inst._build_mapping_from_repo()
        inst._save_mapping_to_index(Path(index_path))
        
        return inst

    @classmethod
    def from_index(cls, file_name_mapping_index: str) -> "FileContentProvider":
        """
        Build/initialize singleton by loading an existing index (json/pkl).
        """
        inst = cls(file_name_mapping_index=file_name_mapping_index)
        return inst

    @classmethod
    def get(cls) -> "FileContentProvider":
        if cls._instance is None or not cls._instance._initialized:
            raise RuntimeError("FileContentProvider is not initialized. Call from_repo(...) or from_index(...) first.")
        return cls._instance

    # -------- Public API (all classmethods) --------
    @classmethod
    def resolve_file_path(cls, filename: str, hint_path: Optional[str] = None) -> Optional[str]:
        """Return absolute path if resolvable via index or repo; else None."""
        inst = cls.get()
        return inst._resolve_file_path_impl(filename, hint_path)

    @classmethod
    def guess_path(cls, filename: str, dir_hint: Optional[str] = None) -> Optional[str]:
        """Heuristic: join dir_hint with filename, or search mapping fallbacks."""
        inst = cls.get()
        return inst._guess_path_impl(filename, dir_hint)

    @classmethod
    def exists(cls, relative_or_abs_path: str) -> bool:
        inst = cls.get()
        return inst._exists_impl(relative_or_abs_path)

    @classmethod
    def read_text(cls, relative_or_abs_path: str, encoding: str = "utf-8") -> Optional[str]:
        inst = cls.get()
        return inst._read_text_impl(relative_or_abs_path, encoding)

    @classmethod
    def save_index(cls, dst: str) -> None:
        inst = cls.get()
        inst._save_mapping_to_index(Path(dst))

    @classmethod
    def load_index(cls, src: str) -> None:
        inst = cls.get()
        inst._load_mapping_from_index(Path(src))

    @classmethod
    def all_candidates_for(cls, filename: str) -> List[str]:
        """Return all absolute-path candidates known for a given bare filename."""
        inst = cls.get()
        key = filename.lower()
        return list(inst.name_to_path_mapping.get(key, []))

    # -------- Internal instance impls --------
    def _exists_impl(self, relative_or_abs_path: str) -> bool:
        p = Path(relative_or_abs_path)
        if p.is_absolute():
            return p.exists()
        if self.repo_root:
            return (self.repo_root / relative_or_abs_path).exists()
        return False

    def _read_text_impl(self, relative_or_abs_path: str, encoding: str) -> Optional[str]:
        try:
            p = Path(relative_or_abs_path)
            if not p.is_absolute():
                if not self.repo_root:
                    return None
                p = (self.repo_root / relative_or_abs_path).resolve()
            if not p.exists() or not p.is_file():
                return None
            return p.read_text(encoding=encoding, errors="ignore")
        except Exception:
            return None

    def _resolve_file_path_impl(self, filename: str, hint_path: Optional[str]) -> Optional[str]:
        if not filename:
            return None

        # Direct absolute
        p = Path(filename)
        if p.is_absolute() and p.exists():
            return str(p.resolve())

        # Repo-relative
        if self.repo_root:
            rp = (self.repo_root / filename).resolve()
            if rp.exists():
                return str(rp)

        # From mapping (bare filename)
        key = Path(filename).name.lower()
        candidates = self.name_to_path_mapping.get(key, [])

        # If hint provided, try to disambiguate by directory proximity
        if hint_path and candidates:
            hint_dir = Path(hint_path).parent.resolve() if Path(hint_path).is_absolute() \
                else ((self.repo_root / hint_path).parent.resolve() if self.repo_root else None)
            if hint_dir:
                # pick candidate with shortest relative distance to hint_dir
                best = min(
                    candidates,
                    key=lambda c: self._path_distance(hint_dir, Path(c))
                )
                return best

        # Fallback: first candidate if any
        if candidates:
            return candidates[0]

        # Last chance: repo walk (only if repo_root known)
        if self.repo_root:
            found = self._search_repo_for_name(key)
            if found:
                return found

        return None

    def _guess_path_impl(self, filename: str, dir_hint: Optional[str]) -> Optional[str]:
        fn = Path(filename).name
        # Try join with dir_hint
        if dir_hint:
            d = Path(dir_hint)
            if not d.is_absolute() and self.repo_root:
                d = (self.repo_root / d).resolve()
            candidate = (d / fn)
            if candidate.exists():
                return str(candidate.resolve())

        # Try index
        key = fn.lower()
        if key in self.name_to_path_mapping and self.name_to_path_mapping[key]:
            return self.name_to_path_mapping[key][0]

        # Try repo root
        if self.repo_root:
            candidate = (self.repo_root / fn)
            if candidate.exists():
                return str(candidate.resolve())

        # Last: scan
        if self.repo_root:
            return self._search_repo_for_name(key)

        return None

    def _build_mapping_from_repo(self) -> None:
        """Build name -> [absolute_paths] mapping by walking repo."""
        if not self.repo_root:
            return

        for p in self.repo_root.rglob("*"):
            if not p.is_file():
                continue
            # ignore dirs filter
            try:
                rel = p.relative_to(self.repo_root)
                if any(part in self.ignore_dirs for part in rel.parts):
                    continue
            except Exception:
                pass

            # extension filter (if provided)
            if self.include_extensions:
                ext = p.suffix.lower()
                if ext not in self.include_extensions:
                    continue

            key = p.name.lower()
            self.name_to_path_mapping[key].append(str(p.resolve()))

    def _save_mapping_to_index(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in (".pkl", ".pickle"):
            with path.open("wb") as f:
                pickle.dump(dict(self.name_to_path_mapping), f)
        else:
            with path.open("w", encoding="utf-8") as f:
                json.dump(self.name_to_path_mapping, f, indent=2, ensure_ascii=False)

    def _load_mapping_from_index(self, path: Path) -> None:
        if not path.exists():
            return
        if path.suffix.lower() in (".pkl", ".pickle"):
            with path.open("rb") as f:
                data = pickle.load(f)
        else:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        # normalize to defaultdict(list)
        self.name_to_path_mapping = defaultdict(list, {k: list(v) for k, v in data.items()})

    def _search_repo_for_name(self, lower_name: str) -> Optional[str]:
        """Slow fallback: scan repo for a filename (first hit)."""
        if not self.repo_root:
            return None
        target = lower_name
        for p in self.repo_root.rglob("*"):
            if p.is_file() and p.name.lower() == target:
                abs_p = str(p.resolve())
                self.name_to_path_mapping[target].append(abs_p)
                return abs_p
        return None

    @staticmethod
    def _path_distance(base: Path, candidate_abs: Path) -> int:
        """
        Heuristic: number of parts in relative path (smaller is 'closer').
        If unrelated, return a large number.
        """
        try:
            rel = candidate_abs.relative_to(base)
            return len(rel.parts)
        except Exception:
            # try common parent distance
            try:
                common = os.path.commonpath([str(base), str(candidate_abs)])
                return len(Path(candidate_abs).relative_to(common).parts)
            except Exception:
                return 1_000_000


def main():
    parser = argparse.ArgumentParser(description="Build file cache or lookup file paths by name")

    # Modes (mutually exclusive)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true", help="Build index from a repo")
    mode.add_argument("-n", "--name", type=str, help="Lookup a filename in the index")
    mode.add_argument("-g", "--guess", type=str, help="Guess a path for a filename")

    # Build args
    parser.add_argument("--repo", type=str, help="Path to repo directory (for --build)")
    parser.add_argument("-o", "--output_index", type=str, help="Where to write the index (json/pkl) for --build")

    # Common / lookup args
    parser.add_argument("-p", "--pickled_index", type=str, help="Path to existing index (json/pkl) for --name/--guess")
    parser.add_argument("-r", "--relative", type=str, default="", help="Optional relative dir hint (with --guess)")
    parser.add_argument("--include_extensions", nargs="*", default=[".c", ".cpp", ".cc", ".m", ".h", ".mm", ".swift"],
                        help="Extensions to include when building index")
    parser.add_argument("--json_output", type=str,
                        help="Optional path to dump the index as JSON after build")

    args = parser.parse_args()

    # ---------- Guess mode ----------
    if args.guess:
        if not args.pickled_index:
            parser.error("--pickled_index (-p) is required when using --guess (-g)")
        if not os.path.exists(args.pickled_index):
            logger.error(f"Index not found: {args.pickled_index}")
            return 1

        try:
            FileContentProvider.from_index(args.pickled_index)
            result = FileContentProvider.guess_path(args.guess, args.relative)

            print(f"Guess path for file_name='{args.guess}', dir_hint='{args.relative}':")
            if result:
                print(f"Result: {result}")
                print("✓ File exists" if os.path.exists(result) else "✗ File does not exist (stale index?)")

                # Debug stats
                inst = FileContentProvider.get()
                print(f"\nIndex contains {len(inst.name_to_path_mapping)} unique filenames")
                return 0
            else:
                print("Result: None (file not found)")
                inst = FileContentProvider.get()
                print(f"\nIndex contains {len(inst.name_to_path_mapping)} unique filenames")

                # Similar filenames (case-insensitive substring)
                search_lower = args.guess.lower()
                similar = [k for k in inst.name_to_path_mapping.keys()
                           if search_lower in k.lower() or k.lower() in search_lower]
                if similar:
                    print("\nSimilar filenames:")
                    for s in sorted(similar)[:10]:
                        print(f"  - {s}")
                return 1
        except Exception as e:
            logger.error(f"Error during guess_path: {e}")
            return 1

    # ---------- Lookup mode ----------
    if args.name:
        if not args.pickled_index:
            parser.error("--pickled_index (-p) is required when using --name (-n)")
        if not os.path.exists(args.pickled_index):
            logger.error(f"Index not found: {args.pickled_index}")
            return 1

        try:
            FileContentProvider.from_index(args.pickled_index)
            inst = FileContentProvider.get()
            key = args.name if args.name in inst.name_to_path_mapping else args.name.lower()

            if key in inst.name_to_path_mapping:
                paths = inst.name_to_path_mapping[key]
                print(f"Search results for '{args.name}':")
                print(f"Found {len(paths)} match(es):")
                for i, abs_path in enumerate(paths, 1):
                    print(f"  {i}. {abs_path}")
                return 0
            else:
                print(f"File '{args.name}' not found in index")
                print(f"Index contains {len(inst.name_to_path_mapping)} unique filenames")

                # Similar filenames (case-insensitive substring)
                search_lower = args.name.lower()
                similar = [k for k in inst.name_to_path_mapping.keys()
                           if search_lower in k.lower() or k.lower() in search_lower]
                if similar:
                    print("\nSimilar filenames found:")
                    for s in sorted(similar)[:10]:
                        print(f"  - {s}")
                return 1
        except Exception as e:
            logger.error(f"Error during lookup: {e}")
            return 1

    # ---------- Build mode ----------
    if args.build:
        if not args.repo or not args.output_index:
            parser.error("--repo and --output_index are required for --build")

        try:
            FileContentProvider.from_repo(
                repo_path=args.repo,
                index_path=args.output_index,
                include_extensions=args.include_extensions,
                build_index=True,
            )
            inst = FileContentProvider.get()

            logger.info("Index build completed")
            logger.info(f"Total unique filenames: {len(inst.name_to_path_mapping)}")
            total_files = sum(len(v) for v in inst.name_to_path_mapping.values())
            logger.info(f"Total files indexed: {total_files}")

            # Show duplicates (same filename multiple absolute paths)
            duplicates = {name: v for name, v in inst.name_to_path_mapping.items() if len(v) > 1}
            if duplicates:
                logger.info(f"Filenames with duplicates: {len(duplicates)}")
                for name, paths in list(duplicates.items())[:5]:
                    logger.info(f"  {name}: {len(paths)} instances")

            # Optional JSON dump
            if args.json_output:
                FileContentProvider.save_index(args.json_output)
                logger.info(f"Wrote JSON index to: {args.json_output}")

            return 0
        except Exception as e:
            logger.error(f"Build failed: {e}")
            return 1

    # Shouldn't reach here
    parser.error("Select one mode: --build, --name, or --guess")
    return 1


if __name__ == "__main__":
    # Setup logging for standalone execution
    setup_default_logging()

    sys.exit(main())
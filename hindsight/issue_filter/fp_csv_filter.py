#!/usr/bin/env python3
"""
FP CSV Filter – Level 2 false-positive filtering.

Takes a CSV of false positives produced by an external analytical system
(e.g. Roo) and removes matching issues from the in-memory issue list and
from the JSON analysis files on disk.

Two-pass filtering:
  Pass 1 (Explicit)  – checksum + exact title match; fast, no embeddings.
  Pass 2 (Semantic)  – ChromaDB vector search; catches near-duplicates
                       whose titles differ subtly from the CSV entries.

Completely fault-tolerant: any failure at any level degrades gracefully.
Report generation is never blocked – at worst the report contains more
issues than ideal if the filter partially or fully failed.

Drop directories (under <artifacts_dir>/dropped_issues/):
  external_false_positives/  – Pass 1 explicit drops
  fp_semantic_matches/       – Pass 2 semantic drops
"""

import csv
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

from ..utils.log_util import get_logger

logger = get_logger(__name__)


class FpEntry(NamedTuple):
    """A single false-positive entry parsed from the CSV."""

    checksum: str           # 8-char hex from filename, or "unknown"
    issue_title: str
    issue_description: str


class FpCsvFilter:
    """
    Level 2 FP CSV-driven issue filter.

    Runs after Level 1 (category filter) and Level 1 dedup.  Takes a
    false-positives CSV produced by Roo (or similar) and removes matching
    issues from the in-memory list and updates JSON files on disk.

    Usage::

        filter = FpCsvFilter(
            csv_path="/path/to/false_positives.csv",
            code_analysis_dir="/artifacts/WebKit/results/code_analysis",
            artifacts_dir="/artifacts/WebKit",
        )
        # checksum_lookup maps (function_name, file_path, issue_title)
        # -> 8-char checksum; built from publisher results before dedup.
        remaining_issues = filter.filter_issues(all_issues, checksum_lookup)
        stats = filter.get_stats()
    """

    EXPLICIT_DROP_DIR = "external_false_positives"
    SEMANTIC_DROP_DIR = "fp_semantic_matches"

    # Similarity thresholds for Pass 2.
    # Cross-function matching uses a higher bar to reduce collateral drops.
    SAME_FUNC_THRESHOLD: float = 0.82
    CROSS_FUNC_THRESHOLD: float = 0.92

    def __init__(
        self,
        csv_path: str,
        code_analysis_dir: str,
        artifacts_dir: str,
        same_func_threshold: Optional[float] = None,
        cross_func_threshold: Optional[float] = None,
    ) -> None:
        """
        Args:
            csv_path:             Path to false_positives.csv.
            code_analysis_dir:    Path to results/code_analysis/.
            artifacts_dir:        Repo-level artifacts dir (parent of
                                  dropped_issues/).
            same_func_threshold:  Override for same-function similarity bar.
            cross_func_threshold: Override for cross-function similarity bar.
        """
        self.csv_path = csv_path
        self.code_analysis_dir = Path(code_analysis_dir)
        self.artifacts_dir = Path(artifacts_dir)
        self.same_func_threshold = (
            same_func_threshold
            if same_func_threshold is not None
            else self.SAME_FUNC_THRESHOLD
        )
        self.cross_func_threshold = (
            cross_func_threshold
            if cross_func_threshold is not None
            else self.CROSS_FUNC_THRESHOLD
        )
        self._stats: Dict[str, int] = {
            "csv_entries": 0,
            "pass1_removed": 0,
            "pass2_removed": 0,
            "json_updates_failed": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def filter_issues(
        self,
        issues: List[Dict[str, Any]],
        checksum_lookup: Optional[Dict[Tuple[str, str, str], str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Filter FP issues from the in-memory list and update JSON files.

        Completely fault-tolerant: any unhandled exception returns the
        original list unchanged so report generation is never blocked.

        Args:
            issues:           Flat list of issue dicts (post-dedup).
            checksum_lookup:  Maps ``(function_name, file_path, issue_title)``
                              to the 8-char checksum of the enclosing JSON
                              file.  Built from publisher results *before*
                              dedup so the mapping survives deduplication.

        Returns:
            Filtered list of issue dicts (FPs removed).
        """
        if not issues:
            return issues

        try:
            fp_entries = self._parse_csv()
        except Exception as exc:
            logger.warning(
                "FP CSV Filter: failed to parse '%s', skipping filter: %s",
                self.csv_path,
                exc,
            )
            return issues

        if not fp_entries:
            logger.info("FP CSV Filter: CSV is empty – nothing to filter")
            return issues

        self._stats["csv_entries"] = len(fp_entries)
        lookup = checksum_lookup or {}
        logger.info(
            "FP CSV Filter: loaded %d FP entries from CSV", len(fp_entries)
        )

        # ── Pass 1: explicit checksum + title match ─────────────────────
        try:
            remaining, p1_dropped = self._pass1_explicit(issues, fp_entries, lookup)
            self._stats["pass1_removed"] = len(p1_dropped)
            logger.info(
                "FP CSV Filter Pass 1 (explicit): removed %d issues", len(p1_dropped)
            )
            if p1_dropped:
                self._update_json_files(p1_dropped, self.EXPLICIT_DROP_DIR)
        except Exception as exc:
            logger.warning(
                "FP CSV Filter Pass 1 (explicit) failed, continuing with all "
                "issues: %s",
                exc,
            )
            remaining = issues
            p1_dropped = []

        # ── Pass 2: semantic match via ChromaDB ─────────────────────────
        try:
            remaining, p2_dropped = self._pass2_semantic(
                remaining, fp_entries, lookup
            )
            self._stats["pass2_removed"] = len(p2_dropped)
            logger.info(
                "FP CSV Filter Pass 2 (semantic): removed %d issues", len(p2_dropped)
            )
            if p2_dropped:
                self._update_json_files(p2_dropped, self.SEMANTIC_DROP_DIR)
        except Exception as exc:
            logger.warning(
                "FP CSV Filter Pass 2 (semantic) failed, skipping: %s", exc
            )

        total = self._stats["pass1_removed"] + self._stats["pass2_removed"]
        logger.info(
            "FP CSV Filter: total removed %d issues "
            "(%d explicit, %d semantic)",
            total,
            self._stats["pass1_removed"],
            self._stats["pass2_removed"],
        )
        return remaining

    def get_stats(self) -> Dict[str, int]:
        """Return a copy of the filter statistics."""
        return dict(self._stats)

    # ------------------------------------------------------------------
    # CSV parsing
    # ------------------------------------------------------------------

    def _parse_csv(self) -> List[FpEntry]:
        """
        Parse the false_positives.csv.

        Returns an empty list (rather than raising) if the file is missing
        or contains no valid entries.
        """
        if not os.path.isfile(self.csv_path):
            logger.warning(
                "FP CSV Filter: CSV not found at '%s'", self.csv_path
            )
            return []

        entries: List[FpEntry] = []
        skipped = 0

        with open(self.csv_path, "r", encoding="utf-8", newline="") as fh:
            raw_lines = fh.readlines()

        # Strip comment lines and blank lines before feeding to DictReader
        data_lines = [
            ln
            for ln in raw_lines
            if ln.strip() and not ln.strip().startswith("#")
        ]
        if not data_lines:
            return []

        reader = csv.DictReader(data_lines)
        for row in reader:
            try:
                checksum = (row.get("checksum") or "").strip()
                title = (row.get("issue_title") or "").strip()
                description = (row.get("issue_description") or "").strip()
                if not checksum:
                    skipped += 1
                    continue
                entries.append(
                    FpEntry(
                        checksum=checksum,
                        issue_title=title,
                        issue_description=description,
                    )
                )
            except Exception as exc:
                logger.debug(
                    "FP CSV Filter: skipping malformed CSV row: %s", exc
                )
                skipped += 1

        if skipped:
            logger.warning(
                "FP CSV Filter: skipped %d malformed/empty CSV rows", skipped
            )
        return entries

    # ------------------------------------------------------------------
    # Pass 1 – explicit match
    # ------------------------------------------------------------------

    def _pass1_explicit(
        self,
        issues: List[Dict[str, Any]],
        fp_entries: List[FpEntry],
        checksum_lookup: Dict[Tuple[str, str, str], str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Remove issues that exactly match a (checksum, normalised-title) pair
        from the CSV.

        Entries with ``checksum == 'unknown'`` are skipped here – they are
        handled (or not) by Pass 2.
        """
        fp_set: Set[Tuple[str, str]] = {
            (e.checksum.lower(), e.issue_title.strip().lower())
            for e in fp_entries
            if e.checksum.lower() != "unknown"
        }

        remaining: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []

        for issue in issues:
            title_key = (issue.get("issue") or "").strip().lower()
            func_name = issue.get("function_name", "")
            file_path = issue.get("file_path", "")
            lookup_key = (func_name, file_path, issue.get("issue", ""))
            checksum = checksum_lookup.get(lookup_key, "").lower()

            if checksum and (checksum, title_key) in fp_set:
                dropped.append({**issue, "_fp_checksum": checksum})
            else:
                remaining.append(issue)

        return remaining, dropped

    # ------------------------------------------------------------------
    # Pass 2 – semantic match
    # ------------------------------------------------------------------

    def _pass2_semantic(
        self,
        issues: List[Dict[str, Any]],
        fp_entries: List[FpEntry],
        checksum_lookup: Dict[Tuple[str, str, str], str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Remove issues that are semantically near-duplicates of known FPs.

        Uses an ephemeral ChromaDB populated with FP embeddings so no state
        persists beyond this call.

        Threshold applied:
          - ``CROSS_FUNC_THRESHOLD`` (0.92 default) for all matches because
            FP entries in the CSV do not carry function/file context, making
            same-function detection unreliable.
        """
        from ..dedupers.common.embeddings import EmbeddingGenerator
        from ..dedupers.common.vector_store import VectorStore

        # Build embedding texts from FP entries; skip entries with no text
        valid_fps: List[Tuple[FpEntry, str]] = []
        for entry in fp_entries:
            text = f"{entry.issue_title} {entry.issue_description}".strip()
            if text:
                valid_fps.append((entry, text))

        if not valid_fps:
            return issues, []

        embedding_gen = EmbeddingGenerator.get_instance()
        fp_texts = [t for _, t in valid_fps]
        fp_embeddings = embedding_gen.generate_batch(fp_texts)

        # Populate ephemeral store with FP embeddings
        fp_store = VectorStore(collection_name="fp_csv_filter", ephemeral=True)
        for idx, ((entry, text), embedding) in enumerate(
            zip(valid_fps, fp_embeddings)
        ):
            fp_store.add_document(
                doc_id=f"fp_{idx}",
                text=text,
                embedding=embedding,
                metadata={
                    "checksum": entry.checksum,
                    "issue_title": entry.issue_title[:200],
                },
            )

        remaining: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []

        for issue in issues:
            title = (issue.get("issue") or "").strip()
            desc = (issue.get("description") or "").strip()
            issue_text = f"{title} {desc}".strip()

            if not issue_text:
                remaining.append(issue)
                continue

            try:
                issue_embedding = embedding_gen.generate(issue_text)
                results = fp_store.query(
                    query_embedding=issue_embedding, n_results=1
                )
            except Exception as exc:
                logger.debug(
                    "FP CSV Filter: embedding/query failed for '%s': %s",
                    title[:60],
                    exc,
                )
                remaining.append(issue)
                continue

            if not results:
                remaining.append(issue)
                continue

            # results is List[Tuple[doc_id, metadata, distance]]
            _, _, distance = results[0]
            # Cosine distance ∈ [0, 2]; convert to similarity ∈ [0, 1]
            similarity = max(0.0, 1.0 - distance / 2.0)

            if similarity >= self.cross_func_threshold:
                func_name = issue.get("function_name", "")
                file_path = issue.get("file_path", "")
                lookup_key = (func_name, file_path, issue.get("issue", ""))
                checksum = checksum_lookup.get(lookup_key, "")
                dropped.append(
                    {
                        **issue,
                        "_fp_checksum": checksum,
                        "_fp_similarity": round(similarity, 4),
                    }
                )
                logger.debug(
                    "FP CSV Filter Pass 2: dropped '%s' (similarity=%.3f)",
                    title[:60],
                    similarity,
                )
            else:
                remaining.append(issue)

        try:
            fp_store.close()
        except Exception:
            pass

        return remaining, dropped

    # ------------------------------------------------------------------
    # Disk updates
    # ------------------------------------------------------------------

    def _update_json_files(
        self, dropped_issues: List[Dict[str, Any]], drop_subdir: str
    ) -> None:
        """
        Remove dropped issues from results/code_analysis/ JSON files and
        copy the affected files to dropped_issues/<drop_subdir>/.

        Fault-tolerant per file: a failure on one JSON does not prevent
        processing the others.
        """
        drop_dir = self.artifacts_dir / "dropped_issues" / drop_subdir
        try:
            drop_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(
                "FP CSV Filter: could not create drop dir '%s': %s",
                drop_dir,
                exc,
            )
            self._stats["json_updates_failed"] += len(dropped_issues)
            return

        # Group by checksum so each JSON file is touched at most once
        from collections import defaultdict

        by_checksum: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for issue in dropped_issues:
            cs = (issue.get("_fp_checksum") or "").strip()
            if cs:
                by_checksum[cs].append(issue)
            else:
                logger.debug(
                    "FP CSV Filter: dropped issue has no checksum, "
                    "skipping JSON update for: %s",
                    (issue.get("issue") or "")[:60],
                )

        for checksum, issues_in_file in by_checksum.items():
            titles_to_remove: Set[str] = {
                (iss.get("issue") or "").strip()
                for iss in issues_in_file
            }
            matching_files = list(
                self.code_analysis_dir.glob(f"*_{checksum}_analysis.json")
            )
            if not matching_files:
                logger.debug(
                    "FP CSV Filter: no JSON file found for checksum '%s'",
                    checksum,
                )
                continue
            for json_path in matching_files:
                self._update_single_json(json_path, titles_to_remove, drop_dir)

    def _update_single_json(
        self,
        json_path: Path,
        titles_to_remove: Set[str],
        drop_dir: Path,
    ) -> None:
        """
        Update one JSON analysis file:
          - Archive a copy to drop_dir (for audit).
          - Remove matching issues from results[].
          - Zero results[] if all issues are removed (file stays so the
            checksum-based analysis cache still recognises the function
            as already-analysed and skips it on future full runs).
        """
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            original_results = data.get("results", [])
            if not original_results:
                return

            remaining = [
                r
                for r in original_results
                if (r.get("issue") or "").strip() not in titles_to_remove
            ]
            removed_count = len(original_results) - len(remaining)
            if removed_count == 0:
                return

            # Archive before modifying
            dest = drop_dir / json_path.name
            shutil.copy2(str(json_path), str(dest))

            # Write back (zero out if all issues removed so cache still works)
            data["results"] = remaining
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)

            logger.debug(
                "FP CSV Filter: updated '%s': removed %d, kept %d",
                json_path.name,
                removed_count,
                len(remaining),
            )
        except Exception as exc:
            logger.warning(
                "FP CSV Filter: failed to update '%s': %s",
                json_path.name,
                exc,
            )
            self._stats["json_updates_failed"] += 1

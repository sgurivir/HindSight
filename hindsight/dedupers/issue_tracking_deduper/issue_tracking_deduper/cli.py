"""
Command-line interface for the Issue Deduper tool.

This module provides the CLI commands for:
- ingest: Build vector database from issue markdown files
- dedupe: Find potential duplicates in an HTML report using hybrid matching
- run: Full pipeline (download + ingest + dedupe)

Uses hybrid matching that combines:
- File path matching (40% weight)
- Function name matching (30% weight)
- Semantic similarity (30% weight)
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Dict

from . import __version__
from .config import (
    get_db_path,
    get_threshold,
    get_top_k,
    get_issue_dir,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
    get_hybrid_weights,
    get_hybrid_threshold,
)

# Default keyword for downloading issues
DEFAULT_ISSUE_KEYWORD = "Lomo Perf Found by AI Static Analysis"


def setup_logging(verbose: bool = False) -> logging.Logger:
    """
    Configure logging for the CLI.
    
    Args:
        verbose: If True, set log level to DEBUG; otherwise INFO.
    
    Returns:
        Configured logger instance.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )
    return logging.getLogger("issue_tracking_deduper")


def resolve_report_path(report_path: str) -> Path:
    """
    Resolve a report path, handling file:// URLs.
    
    Args:
        report_path: Path or file:// URL to the report.
    
    Returns:
        Resolved Path object.
    
    Raises:
        ValueError: If the path is invalid or file doesn't exist.
    """
    # Handle file:// URLs
    if report_path.startswith("file://"):
        report_path = report_path[7:]  # Remove "file://" prefix
    
    path = Path(report_path).expanduser().resolve()
    
    if not path.exists():
        raise ValueError(f"Report file not found: {path}")
    
    if not path.is_file():
        raise ValueError(f"Report path is not a file: {path}")
    
    return path


def cmd_ingest(args: argparse.Namespace) -> int:
    """
    Execute the ingest command.
    
    Builds or updates the vector database from issue markdown files.
    
    Args:
        args: Parsed command-line arguments.
    
    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    logger = setup_logging(args.verbose)
    
    issue_dir = Path(args.issue_dir).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve()
    
    logger.info(f"Ingesting issues from: {issue_dir}")
    logger.info(f"Vector database path: {db_path}")
    
    if not issue_dir.exists():
        logger.error(f"Issue directory not found: {issue_dir}")
        return 1
    
    if not issue_dir.is_dir():
        logger.error(f"Issue path is not a directory: {issue_dir}")
        return 1
    
    # Import ingestion module
    try:
        from .vector_db.ingestion import IssueIngester
    except ImportError as e:
        logger.error(f"Failed to import ingestion module: {e}")
        logger.error("Make sure all dependencies are installed: pip install -r requirements.txt")
        return 1
    
    # Perform ingestion
    try:
        ingester = IssueIngester(db_path=db_path)
        total, added, skipped = ingester.ingest_directory(
            issue_dir,
            recursive=True,
            show_progress=not args.verbose  # Hide progress bar in verbose mode
        )
        
        # Print summary
        logger.info("=" * 50)
        logger.info("Ingestion Summary:")
        logger.info(f"  Total files processed: {total}")
        logger.info(f"  Issues added/updated: {added}")
        logger.info(f"  Issues skipped (unchanged): {skipped}")
        logger.info(f"  Total issues in database: {ingester.get_stats()['total_issues']}")
        logger.info("=" * 50)
        
        ingester.close()
        
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1
    
    return 0


def cmd_dedupe(args: argparse.Namespace) -> int:
    """
    Execute the dedupe command.
    
    Finds potential duplicates in an HTML report using hybrid matching
    that combines file path, function name, and semantic similarity.
    
    Args:
        args: Parsed command-line arguments.
    
    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    logger = setup_logging(args.verbose)
    
    try:
        report_path = resolve_report_path(args.report)
    except ValueError as e:
        logger.error(str(e))
        return 1
    
    db_path = Path(args.db_path).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else None
    
    logger.info(f"Processing report: {report_path}")
    logger.info(f"Vector database path: {db_path}")
    logger.info(f"Similarity threshold: {args.threshold}")
    logger.info(f"Top-K results: {args.top_k}")
    
    # Log hybrid weights
    weights = _get_hybrid_weights(args)
    logger.info(f"Hybrid weights: file={weights['file_path']:.0%}, "
               f"func={weights['function_name']:.0%}, "
               f"cosine={weights['cosine_similarity']:.0%}")
    
    if not db_path.exists():
        logger.error(f"Vector database not found: {db_path}")
        logger.error("Run 'ingest' command first to build the database.")
        return 1
    
    if output_path:
        logger.info(f"Output path: {output_path}")
    else:
        # Generate default output path
        output_path = report_path.parent / f"{report_path.stem}_deduped{report_path.suffix}"
        logger.info(f"Output path (auto-generated): {output_path}")
    
    # Import deduplication modules
    try:
        from .vector_db.store import VectorStore
        from .vector_db.embeddings import EmbeddingGenerator
    except ImportError as e:
        logger.error(f"Failed to import deduplication modules: {e}")
        logger.error("Make sure all dependencies are installed: pip install -r requirements.txt")
        return 1
    
    # Perform deduplication
    try:
        # Initialize components
        vector_store = VectorStore(db_path=db_path)
        embedding_generator = EmbeddingGenerator()
        
        # Check if database has any issues
        issue_count = vector_store.count()
        if issue_count == 0:
            logger.error("Vector database is empty. Run 'ingest' command first.")
            return 1
        
        logger.info(f"Vector database contains {issue_count} issues")
        
        # Run hybrid deduplication
        results, summary = _run_hybrid_dedupe(
            report_path=report_path,
            vector_store=vector_store,
            embedding_generator=embedding_generator,
            threshold=args.threshold,
            top_k=args.top_k,
            weights=weights,
            logger=logger
        )
        
        # Print summary
        logger.info("=" * 60)
        logger.info("Deduplication Summary:")
        logger.info(f"  Total issues analyzed: {summary['total_issues']}")
        logger.info(f"  Issues with potential duplicates: {summary['issues_with_matches']}")
        logger.info(f"  Issues without matches: {summary['issues_without_matches']}")
        logger.info(f"  Total matches found: {summary['total_matches']}")
        logger.info(f"    - High confidence: {summary['high_confidence_matches']}")
        logger.info(f"    - Moderate confidence: {summary['moderate_confidence_matches']}")
        logger.info(f"    - Low confidence: {summary['low_confidence_matches']}")
        logger.info("=" * 60)
        
        # Print detailed results if verbose
        if args.verbose:
            _print_detailed_results(results, logger)
        
        # Generate output report
        try:
            from .report.html_generator import AnnotatedReportGenerator
            
            generator = AnnotatedReportGenerator()
            generator.generate(
                original_report_path=report_path,
                dedupe_results=results,
                output_path=output_path
            )
            logger.info(f"\nAnnotated report saved to: {output_path}")
        except ImportError:
            logger.warning("Report generator not yet implemented. Skipping output generation.")
            # Print results to console instead
            _print_console_results(results)
        
        vector_store.close()
        
    except Exception as e:
        logger.error(f"Deduplication failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1
    
    return 0


def _get_hybrid_weights(args: argparse.Namespace) -> Dict[str, float]:
    """
    Get hybrid weights from command-line arguments or defaults.
    
    Args:
        args: Parsed command-line arguments.
    
    Returns:
        Dictionary of weights for hybrid scoring.
    """
    weights = get_hybrid_weights().copy()
    
    # Override with command-line arguments if provided
    if hasattr(args, 'file_weight') and args.file_weight is not None:
        weights['file_path'] = args.file_weight
    if hasattr(args, 'func_weight') and args.func_weight is not None:
        weights['function_name'] = args.func_weight
    if hasattr(args, 'cosine_weight') and args.cosine_weight is not None:
        weights['cosine_similarity'] = args.cosine_weight
    
    # Normalize weights to sum to 1.0
    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        for key in weights:
            weights[key] /= total
    
    return weights


def _run_hybrid_dedupe(
    report_path: Path,
    vector_store,
    embedding_generator,
    threshold: float,
    top_k: int,
    weights: Dict[str, float],
    logger: logging.Logger
) -> tuple:
    """
    Run deduplication using hybrid matching.
    
    Args:
        report_path: Path to the HTML report.
        vector_store: VectorStore instance.
        embedding_generator: EmbeddingGenerator instance.
        threshold: Minimum hybrid score for matches.
        top_k: Maximum matches per issue.
        weights: Hybrid scoring weights.
        logger: Logger instance.
    
    Returns:
        Tuple of (results dict, summary dict).
    """
    from .deduper.hybrid_matcher import HybridMatcher
    from .parsers import get_default_registry
    
    # Parse the report
    parser_registry = get_default_registry()
    parser = parser_registry.get_parser(report_path)
    if not parser:
        raise ValueError(f"No parser available for report: {report_path}")
    
    logger.info(f"Using parser: {parser.get_format_name()}")
    issues = parser.parse(report_path)
    logger.info(f"Parsed {len(issues)} issues from report")
    
    # Create hybrid matcher
    matcher = HybridMatcher(
        vector_store=vector_store,
        embedding_generator=embedding_generator,
        weights=weights,
        threshold=threshold,
        top_k=top_k
    )
    
    # Find matches for each issue
    results = {}
    for issue in issues:
        matches = matcher.find_matches(issue)
        # Convert HybridMatch to DedupeMatch for compatibility
        dedupe_matches = [m.to_dedupe_match() for m in matches]
        results[issue.id] = {
            "issue": issue,
            "matches": dedupe_matches,
            "hybrid_matches": matches  # Keep original for detailed output
        }
    
    # Generate summary
    summary = _generate_summary(results, threshold, top_k)
    
    return results, summary


def _generate_summary(results: dict, threshold: float, top_k: int) -> dict:
    """
    Generate summary statistics from deduplication results.
    
    Args:
        results: Dictionary of deduplication results.
        threshold: Similarity threshold used.
        top_k: Top-K value used.
    
    Returns:
        Dictionary containing summary statistics.
    """
    total_issues = len(results)
    issues_with_matches = sum(1 for r in results.values() if r["matches"])
    total_matches = sum(len(r["matches"]) for r in results.values())
    
    # Count by confidence level
    high_confidence = 0
    moderate_confidence = 0
    low_confidence = 0
    
    for result in results.values():
        for match in result["matches"]:
            if match.confidence_level == "high":
                high_confidence += 1
            elif match.confidence_level == "moderate":
                moderate_confidence += 1
            else:
                low_confidence += 1
    
    return {
        "total_issues": total_issues,
        "issues_with_matches": issues_with_matches,
        "issues_without_matches": total_issues - issues_with_matches,
        "total_matches": total_matches,
        "high_confidence_matches": high_confidence,
        "moderate_confidence_matches": moderate_confidence,
        "low_confidence_matches": low_confidence,
        "threshold": threshold,
        "top_k": top_k,
    }


def _print_detailed_results(results: dict, logger: logging.Logger) -> None:
    """
    Print detailed results to the logger.
    
    Args:
        results: Dictionary of deduplication results.
        logger: Logger instance.
    """
    logger.info("\nDetailed Results:")
    for issue_id, result in results.items():
        issue = result["issue"]
        matches = result["matches"]
        hybrid_matches = result.get("hybrid_matches", [])
        
        if matches:
            logger.info(f"\n  Issue: {issue.title[:60]}...")
            logger.info(f"    File: {issue.file_path or 'N/A'}")
            logger.info(f"    Function: {issue.function_name or 'N/A'}")
            logger.info(f"    Matches:")
            
            for hm in hybrid_matches:
                logger.info(f"      - {hm.issue_url} (hybrid: {hm.hybrid_percentage}%)")
                logger.info(f"        file: {hm.file_path_percentage}%, "
                           f"func: {hm.function_name_percentage}%, "
                           f"cosine: {hm.cosine_similarity_percentage}%")
                if hm.match_reasons:
                    for reason in hm.match_reasons:
                        logger.info(f"        {reason}")


def _print_console_results(results: dict) -> None:
    """
    Print results to console when report generator is not available.
    
    Args:
        results: Dictionary of deduplication results.
    """
    print("\n" + "=" * 60)
    print("DEDUPLICATION RESULTS (hybrid matching)")
    print("=" * 60)
    
    for issue_id, result in results.items():
        issue = result["issue"]
        matches = result["matches"]
        hybrid_matches = result.get("hybrid_matches", [])
        
        if matches:
            print(f"\n📋 Issue: {issue.title[:70]}...")
            print(f"   File: {issue.file_path or 'N/A'}")
            print(f"   Function: {issue.function_name or 'N/A'}")
            print(f"   Severity: {issue.severity or 'N/A'}")
            print(f"   Potential duplicates:")
            
            for hm in hybrid_matches:
                confidence_emoji = _get_confidence_emoji(hm.confidence_level)
                print(f"     {confidence_emoji} {hm.issue_url} (hybrid: {hm.hybrid_percentage}%)")
                print(f"        📁 file: {hm.file_path_percentage}% | "
                      f"🔧 func: {hm.function_name_percentage}% | "
                      f"📝 cosine: {hm.cosine_similarity_percentage}%")
                if hm.match_reasons:
                    for reason in hm.match_reasons:
                        print(f"        {reason}")
                print(f"        {hm.issue_title[:60]}...")
    
    print("\n" + "=" * 60)


def _get_confidence_emoji(confidence_level: str) -> str:
    """Get emoji for confidence level."""
    if confidence_level in ("high", "very_high"):
        return "🔴"
    elif confidence_level == "moderate":
        return "🟡"
    else:
        return "🟢"


def download_issues(issue_dir: Path, keyword: str, logger: logging.Logger) -> int:
    """
    Download issues matching the keyword to the issue directory.
    
    Only downloads issues that don't already exist in the directory.
    
    Args:
        issue_dir: Directory to save issue markdown files.
        keyword: Keyword to search for issues.
        logger: Logger instance.
    
    Returns:
        Number of issues downloaded, or -1 on error.
    """
    try:
        # Import issue_helper from parent directory
        import sys
        issue_scripts_dir = Path(__file__).parent.parent
        if str(issue_scripts_dir) not in sys.path:
            sys.path.insert(0, str(issue_scripts_dir))
        
        from issue_helper import IssueDownloader
    except ImportError as e:
        logger.error(f"Failed to import IssueDownloader: {e}")
        logger.error("Make sure radarclient is installed: pip install -i https://pypi.apple.com/simple radarclient")
        return -1
    
    try:
        logger.info(f"Downloading issues with keyword: '{keyword}'")
        logger.info(f"Output directory: {issue_dir}")
        
        # Create downloader
        downloader = IssueDownloader(
            output_dir=str(issue_dir),
            client_name='IssueDeduper'
        )
        
        # Download issues (only those not already downloaded)
        downloaded = downloader.download_issues_by_keyword(
            keyword=keyword,
            rate_limit_delay=0.1
        )
        
        logger.info(f"Downloaded {len(downloaded)} new issues")
        return len(downloaded)
        
    except Exception as e:
        logger.error(f"Issue download failed: {e}")
        return -1


def cmd_run(args: argparse.Namespace) -> int:
    """
    Execute the full pipeline (download + ingest + dedupe).
    
    Args:
        args: Parsed command-line arguments.
    
    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    logger = setup_logging(args.verbose)
    
    logger.info("Running full pipeline: download + ingest + dedupe")
    
    issue_dir = Path(args.issue_dir).expanduser().resolve()
    
    # Ensure issue directory exists
    issue_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Download issues (only those not already downloaded)
    logger.info("=" * 60)
    logger.info("Step 1: Downloading issues")
    logger.info("=" * 60)
    
    download_result = download_issues(
        issue_dir=issue_dir,
        keyword=getattr(args, 'issue_keyword', DEFAULT_ISSUE_KEYWORD),
        logger=logger
    )
    
    if download_result < 0:
        logger.warning("Issue download failed, continuing with existing issues...")
    elif download_result == 0:
        logger.info("No new issues to download (all already exist)")
    else:
        logger.info(f"Downloaded {download_result} new issues")
    
    # Step 2: Run ingest
    logger.info("=" * 60)
    logger.info("Step 2: Ingesting issues into vector database")
    logger.info("=" * 60)
    
    ingest_result = cmd_ingest(args)
    if ingest_result != 0:
        return ingest_result
    
    # Step 3: Run dedupe
    logger.info("=" * 60)
    logger.info("Step 3: Finding potential duplicates")
    logger.info("=" * 60)
    
    return cmd_dedupe(args)


def create_parser() -> argparse.ArgumentParser:
    """
    Create the argument parser for the CLI.
    
    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="issue_tracking_deduper",
        description="Identify potential duplicate issues from LLM static analyzer reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
    Examples:
      # Ingest issues into vector database
      python -m issue_tracking_deduper ingest --issue-dir ~/issues_on_file
    
      # Find duplicates in an HTML report
      python -m issue_tracking_deduper dedupe --report file:///path/to/report.html
    
      # Run full pipeline (download + ingest + dedupe)
      python -m issue_tracking_deduper run --issue-dir ~/issues_on_file --report /path/to/report.html
    
      # Run with custom issue keyword
      python -m issue_tracking_deduper run --issue-dir ~/issues_on_file --report /path/to/report.html --issue-keyword "memory leak"
            """,
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    
    # Create subparsers for commands
    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        required=True,
        help="Available commands",
    )
    
    # Common arguments
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output (debug logging)",
    )
    common_parser.add_argument(
        "--db-path",
        type=str,
        default=str(get_db_path()),
        help=f"Path to vector database (default: {get_db_path()})",
    )
    
    # Ingest command
    ingest_parser = subparsers.add_parser(
        "ingest",
        parents=[common_parser],
        help="Ingest issue markdown files into vector database",
        description="Build or update the vector database from issue markdown files.",
    )
    ingest_parser.add_argument(
        "--issue-dir",
        type=str,
        default=str(get_issue_dir()),
        help=f"Directory containing issue markdown files (default: {get_issue_dir()})",
    )
    ingest_parser.set_defaults(func=cmd_ingest)
    
    # Dedupe command
    dedupe_parser = subparsers.add_parser(
        "dedupe",
        parents=[common_parser],
        help="Find potential duplicates in an HTML report",
        description="Parse an HTML report and find potential duplicate issues.",
    )
    dedupe_parser.add_argument(
        "--report",
        type=str,
        required=True,
        help="Path or file:// URL to the HTML report",
    )
    dedupe_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output path for annotated report (default: <input>_deduped.html)",
    )
    dedupe_parser.add_argument(
        "--threshold",
        type=float,
        default=get_threshold(),
        help=f"Similarity threshold (0.0-1.0, default: {get_threshold()})",
    )
    dedupe_parser.add_argument(
        "--top-k",
        type=int,
        default=get_top_k(),
        help=f"Maximum number of matches to show per issue (default: {get_top_k()})",
    )
    dedupe_parser.set_defaults(func=cmd_dedupe)
    
    # Run command (full pipeline)
    run_parser = subparsers.add_parser(
        "run",
        parents=[common_parser],
        help="Run full pipeline (download + ingest + dedupe)",
        description="Download issues, ingest them, and find duplicates in an HTML report.",
    )
    run_parser.add_argument(
        "--issue-dir",
        type=str,
        default=str(get_issue_dir()),
        help=f"Directory containing issue markdown files (default: {get_issue_dir()})",
    )
    run_parser.add_argument(
        "--report",
        type=str,
        required=True,
        help="Path or file:// URL to the HTML report",
    )
    run_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output path for annotated report (default: <input>_deduped.html)",
    )
    run_parser.add_argument(
        "--threshold",
        type=float,
        default=get_threshold(),
        help=f"Similarity threshold (0.0-1.0, default: {get_threshold()})",
    )
    run_parser.add_argument(
        "--top-k",
        type=int,
        default=get_top_k(),
        help=f"Maximum number of matches to show per issue (default: {get_top_k()})",
    )
    run_parser.add_argument(
        "--issue-keyword",
        type=str,
        default=DEFAULT_ISSUE_KEYWORD,
        help=f"Keyword to search for issues to download (default: '{DEFAULT_ISSUE_KEYWORD}')",
    )
    run_parser.set_defaults(func=cmd_run)
    
    return parser


def main(argv: Optional[list] = None) -> int:
    """
    Main entry point for the CLI.
    
    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).
    
    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = create_parser()
    args = parser.parse_args(argv)
    
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 130
    except Exception as e:
        logger = logging.getLogger("issue_tracking_deduper")
        logger.error(f"Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

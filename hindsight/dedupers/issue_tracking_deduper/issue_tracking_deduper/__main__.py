"""
Entry point for running issue_tracking_deduper as a module.

Usage:
    python -m issue_tracking_deduper <command> [options]

Commands:
    ingest  - Ingest issue markdown files into vector database
    dedupe  - Find potential duplicates in an HTML report
    run     - Run full pipeline (ingest + dedupe)

Examples:
    python -m issue_tracking_deduper ingest --issue-dir ~/issues_on_file
    python -m issue_tracking_deduper dedupe --report file:///path/to/report.html
    python -m issue_tracking_deduper run --issue-dir ~/issues_on_file --report /path/to/report.html
"""

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())

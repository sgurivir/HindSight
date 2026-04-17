# False Positive Management Feature Design

## Overview

This document describes the design for two related features:
1. **Print Issue Directories**: Display the directory path where all issues are stored after code analysis
2. **Move False Positives Script**: A utility script to move identified false positive issues to a separate directory

## Background

### Current System Architecture

The Code Analyzer produces analysis results stored in the following structure:

```
~/llm_artifacts/<repo_name>/
├── results/
│   ├── code_analysis/           # Individual issue JSON files
│   │   ├── <function>_<file>_<checksum>_analysis.json
│   │   ├── ...
│   │   └── repo_analysis_organized_issues.txt
│   ├── html_reports/            # Generated HTML reports
│   │   └── repo_analysis_<repo>_<timestamp>.html
│   └── errors/                  # Error logs
└── dropped_issues/              # Issues dropped by filtering system
    ├── level1_category_filter/  # Dropped by Level 1 (Category Filter)
    ├── level2_llm_filter/       # Dropped by Level 2 (LLM Filter)
    └── level3_response_challenger/  # Dropped by Level 3 (Response Challenger)
```

### Issue JSON File Structure

Each analysis result file follows this schema:

```json
{
  "file_path": "Source/bmalloc/mimalloc/mimalloc/src/prim/windows/prim.c",
  "function": "win_virtual_alloc_prim",
  "checksum": "880f92c17aeb2e892e1c78132e082a70",
  "results": [
    {
      "issue": "Description of the issue",
      "severity": "low|medium|high|critical",
      "category": "logicBug|memoryLeak|...",
      "file_path": "Source/...",
      "function_name": "function_name",
      "line_number": "123-456",
      "description": "Detailed description",
      "suggestion": "How to fix",
      "external_references": [],
      "evidence": ""
    }
  ],
  "analysis_timestamp": "2026-03-27T15:23:28.496649"
}
```

---

## Feature 1: Print Issue Directories

### Purpose

After code analysis completes, print the full path to the directory containing all issue files. This allows users to:
- Easily locate the analysis results
- Use the path as input to other tools (like the false positive management script)
- Verify where results are being stored

### Implementation Location

Modify the report generation or analysis completion phase to print the directory path.

### Proposed Output Format

```
================================================================================
ANALYSIS COMPLETE
================================================================================
Issues Directory: /Users/sgurivireddy/llm_artifacts/WebKit/results/code_analysis
HTML Report: /Users/sgurivireddy/llm_artifacts/WebKit/results/html_reports/repo_analysis_Webkit_20260327_082356.html
Total Issues Found: 42
================================================================================
```

### Implementation Details

**File to Modify**: [`hindsight/report/report_service.py`](hindsight/report/report_service.py) or the main analysis runner

**Changes Required**:
1. After report generation, print the `code_analysis` directory path
2. Print the HTML report path
3. Print summary statistics

**Code Snippet** (conceptual):

```python
def print_analysis_summary(repo_name: str, output_dir: str, issue_count: int, html_report_path: str):
    """Print summary of analysis results with directory locations."""
    code_analysis_dir = os.path.join(output_dir, repo_name, "results", "code_analysis")
    
    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)
    print(f"Issues Directory: {code_analysis_dir}")
    print(f"HTML Report: {html_report_path}")
    print(f"Total Issues Found: {issue_count}")
    print("=" * 80)
```

---

## Feature 2: Move False Positives Script

### Purpose

Create a utility script that:
1. Takes the results directory path and a text file listing false positive issues
2. Moves the identified false positive issue files to a `dropped_issues` subdirectory
3. Preserves the original files for audit purposes

### False Positives Input File Format

The external system produces a **CSV file** with the following columns for easy identification of false positives:

```csv
checksum,issue_title,issue_description
880f92c1,"GetLastError() called twice","GetLastError() called twice, potentially returning different values"
0c0fd6e7,"Potential integer overflow","Unsigned subtraction may wrap around causing unexpected behavior"
8d775c23,"Missing null check","Pointer dereference without null validation"
```

**CSV Format Specification:**
- **Header row**: `checksum,issue_title,issue_description`
- **checksum**: The 8-character checksum from the analysis filename (e.g., `880f92c1` from `win_virtual_alloc_prim_prim.c_880f92c1_analysis.json`)
- **issue_title**: Brief title of the issue (for human readability)
- **issue_description**: Full description of the issue (for verification)

**Notes:**
- The `issue_title` and `issue_description` columns are for human readability and verification
- Only the `checksum` column is used for matching files
- Standard CSV escaping rules apply (quotes around fields containing commas)
- Lines starting with `#` are treated as comments and ignored

### Script Location

`dev/move_false_positives.py`

### Script Interface

```bash
python dev/move_false_positives.py \
    --results-dir /Users/sgurivireddy/llm_artifacts/WebKit/results/code_analysis \
    --false-positives-file /path/to/false_positives.txt \
    [--dry-run]  # Optional: preview changes without moving files
```

### Directory Structure After Running

The script uses the **existing `dropped_issues` directory** that is already used by the 3-level filtering system. This keeps all dropped issues (from any source) in one location:

```
~/llm_artifacts/<repo_name>/
├── results/
│   ├── code_analysis/               # Remaining valid issues
│   │   ├── valid_issue_1_analysis.json
│   │   └── ...
│   └── html_reports/
└── dropped_issues/                  # All dropped issues (existing directory)
    ├── level1_category_filter/      # Issues dropped by Level 1 filter
    ├── level2_llm_filter/           # Issues dropped by Level 2 filter
    ├── level3_response_challenger/  # Issues dropped by Level 3 filter
    └── external_false_positives/    # NEW: Issues identified by external system
        ├── false_positive_1_analysis.json
        ├── false_positive_2_analysis.json
        └── move_log_<timestamp>.json  # Log of what was moved
```

This approach:
- Reuses the existing `dropped_issues` directory structure
- Creates a new subdirectory `external_false_positives` for issues identified by the external system
- Keeps all dropped issues organized by their source

### Implementation Design

```python
#!/usr/bin/env python3
"""
Move False Positives Script

Moves identified false positive issues from the code_analysis directory
to a dropped_issues directory for audit and review purposes.

Usage:
    python dev/move_false_positives.py \
        --results-dir /path/to/results/code_analysis \
        --false-positives-file /path/to/false_positives.txt \
        [--dry-run]
"""

import os
import sys
import csv
import json
import shutil
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, NamedTuple


class FalsePositiveEntry(NamedTuple):
    """Represents a single false positive entry from the CSV file."""
    checksum: str
    issue_title: str
    issue_description: str


class FalsePositiveMover:
    """Handles moving false positive issues to a separate directory."""
    
    def __init__(self, results_dir: str, dry_run: bool = False):
        """
        Initialize the mover.
        
        Args:
            results_dir: Path to the code_analysis directory
            dry_run: If True, only preview changes without moving files
        """
        self.results_dir = Path(results_dir)
        self.dry_run = dry_run
        
        # dropped_issues is at the repo artifacts level (sibling to results/)
        # Structure: ~/llm_artifacts/<repo>/dropped_issues/external_false_positives/
        self.dropped_dir = self.results_dir.parent.parent / "dropped_issues" / "external_false_positives"
        
        self.moved_files: List[Dict] = []
        self.not_found_files: List[FalsePositiveEntry] = []
        self.errors: List[Dict] = []
    
    def parse_false_positives_file(self, fp_file: str) -> List[FalsePositiveEntry]:
        """
        Parse the false positives CSV file and return list of entries.
        
        CSV Format:
            checksum,issue_title,issue_description
            880f92c1,"GetLastError() called twice","GetLastError() called twice, potentially..."
        
        Args:
            fp_file: Path to the false positives CSV file
            
        Returns:
            List of FalsePositiveEntry objects
        """
        entries = []
        
        with open(fp_file, 'r', encoding='utf-8', newline='') as f:
            # Skip comment lines at the beginning
            lines = f.readlines()
            
        # Filter out comment lines and empty lines
        data_lines = [line for line in lines if line.strip() and not line.strip().startswith('#')]
        
        if not data_lines:
            return entries
        
        # Parse CSV from filtered lines
        reader = csv.DictReader(data_lines)
        
        for row in reader:
            checksum = row.get('checksum', '').strip()
            issue_title = row.get('issue_title', '').strip()
            issue_description = row.get('issue_description', '').strip()
            
            if checksum:  # Only add if checksum is present
                entries.append(FalsePositiveEntry(
                    checksum=checksum,
                    issue_title=issue_title,
                    issue_description=issue_description
                ))
        
        return entries
    
    def find_matching_files(self, entries: List[FalsePositiveEntry]) -> Dict[FalsePositiveEntry, Path]:
        """
        Find files in results_dir that match the given false positive entries.
        
        Args:
            entries: List of FalsePositiveEntry objects from CSV file
            
        Returns:
            Dict mapping FalsePositiveEntry to file path
        """
        matches = {}
        
        # Get all analysis files
        analysis_files = list(self.results_dir.glob("*_analysis.json"))
        
        for entry in entries:
            found = False
            checksum = entry.checksum
            
            for file_path in analysis_files:
                filename = file_path.name
                
                # Match by checksum (8 characters before _analysis.json)
                # The checksum appears in the filename as: function_file_CHECKSUM_analysis.json
                if f"_{checksum}_analysis.json" in filename:
                    matches[entry] = file_path
                    found = True
                    break
            
            if not found:
                self.not_found_files.append(entry)
        
        return matches
    
    def move_files(self, matches: Dict[FalsePositiveEntry, Path]) -> Tuple[int, int]:
        """
        Move matched files to dropped_issues directory.
        
        Args:
            matches: Dict mapping FalsePositiveEntry to file path
        
        Returns:
            Tuple of (moved_count, error_count)
        """
        if not self.dry_run:
            self.dropped_dir.mkdir(parents=True, exist_ok=True)
        
        moved_count = 0
        error_count = 0
        
        for entry, source_path in matches.items():
            dest_path = self.dropped_dir / source_path.name
            
            try:
                if self.dry_run:
                    print(f"[DRY RUN] Would move: {source_path.name}")
                    print(f"          Issue: {entry.issue_title}")
                else:
                    shutil.move(str(source_path), str(dest_path))
                    print(f"Moved: {source_path.name}")
                    print(f"       Issue: {entry.issue_title}")
                
                self.moved_files.append({
                    'checksum': entry.checksum,
                    'issue_title': entry.issue_title,
                    'issue_description': entry.issue_description,
                    'source': str(source_path),
                    'destination': str(dest_path),
                    'timestamp': datetime.now().isoformat()
                })
                moved_count += 1
                
            except Exception as e:
                self.errors.append({
                    'checksum': entry.checksum,
                    'issue_title': entry.issue_title,
                    'source': str(source_path),
                    'error': str(e)
                })
                error_count += 1
                print(f"Error moving {source_path.name}: {e}")
        
        return moved_count, error_count
    
    def write_move_log(self):
        """Write a log file documenting what was moved."""
        if self.dry_run:
            return
        
        log_file = self.dropped_dir / f"move_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        # Convert FalsePositiveEntry objects to dicts for JSON serialization
        not_found_dicts = [
            {
                'checksum': entry.checksum,
                'issue_title': entry.issue_title,
                'issue_description': entry.issue_description
            }
            for entry in self.not_found_files
        ]
        
        log_data = {
            'timestamp': datetime.now().isoformat(),
            'results_dir': str(self.results_dir),
            'dropped_dir': str(self.dropped_dir),
            'moved_files': self.moved_files,
            'not_found': not_found_dicts,
            'errors': self.errors,
            'summary': {
                'total_moved': len(self.moved_files),
                'not_found': len(self.not_found_files),
                'errors': len(self.errors)
            }
        }
        
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2)
        
        print(f"\nMove log written to: {log_file}")
    
    def print_summary(self):
        """Print summary of the operation."""
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Files moved:     {len(self.moved_files)}")
        print(f"Not found:       {len(self.not_found_files)}")
        print(f"Errors:          {len(self.errors)}")
        
        if self.not_found_files:
            print("\nNot found entries:")
            for entry in self.not_found_files[:10]:  # Show first 10
                print(f"  - {entry.checksum}: {entry.issue_title}")
            if len(self.not_found_files) > 10:
                print(f"  ... and {len(self.not_found_files) - 10} more")
        
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Move false positive issues to dropped_issues directory"
    )
    parser.add_argument(
        '--results-dir', '-r',
        required=True,
        help='Path to the code_analysis directory containing issue JSON files'
    )
    parser.add_argument(
        '--false-positives-file', '-f',
        required=True,
        help='Path to CSV file listing false positives (format: checksum,issue_title,issue_description)'
    )
    parser.add_argument(
        '--dry-run', '-n',
        action='store_true',
        help='Preview changes without actually moving files'
    )
    
    args = parser.parse_args()
    
    # Validate inputs
    if not os.path.isdir(args.results_dir):
        print(f"Error: Results directory not found: {args.results_dir}")
        sys.exit(1)
    
    if not os.path.isfile(args.false_positives_file):
        print(f"Error: False positives file not found: {args.false_positives_file}")
        sys.exit(1)
    
    # Run the mover
    mover = FalsePositiveMover(args.results_dir, dry_run=args.dry_run)
    
    print(f"Results directory: {args.results_dir}")
    print(f"False positives file: {args.false_positives_file}")
    print(f"Dry run: {args.dry_run}")
    print()
    
    # Parse false positives file
    identifiers = mover.parse_false_positives_file(args.false_positives_file)
    print(f"Found {len(identifiers)} identifiers in false positives file")
    
    # Find matching files
    matches = mover.find_matching_files(identifiers)
    print(f"Matched {len(matches)} files in results directory")
    
    # Move files
    if matches:
        moved, errors = mover.move_files(matches)
    
    # Write log and summary
    mover.write_move_log()
    mover.print_summary()


if __name__ == "__main__":
    main()
```

---

## Workflow Integration

### Complete Workflow

1. **Run Code Analysis**
   ```bash
   python -m hindsight.code_analyzer --config config.json
   ```

2. **Analysis Completes - Directory Printed**
   ```
   ================================================================================
   ANALYSIS COMPLETE
   ================================================================================
   Issues Directory: /Users/sgurivireddy/llm_artifacts/WebKit/results/code_analysis
   HTML Report: /Users/sgurivireddy/llm_artifacts/WebKit/results/html_reports/repo_analysis_Webkit_20260327_082356.html
   Total Issues Found: 42
   ================================================================================
   ```

3. **Review HTML Report**
   - Open the HTML report in browser
   - Review issues manually or use external false positive detection system

4. **Roo (or External System) Identifies False Positives**
   - Use the prompt at [`dev/prompts/prompt_for_roo_to_analyze_si.txt`](dev/prompts/prompt_for_roo_to_analyze_si.txt) to have Roo analyze the issues
   - Roo produces TWO output files:
     - **HTML report**: Full analysis with Valid/False Positive determination
     - **`false_positives.csv`**: CSV file listing ONLY false positives for automated processing
   - CSV format:
     ```csv
     checksum,issue_title,issue_description
     880f92c1,"GetLastError() called twice","GetLastError() called twice, potentially returning different values"
     0c0fd6e7,"Potential integer overflow","Unsigned subtraction may wrap around"
     ```

5. **Move False Positives**
   ```bash
   # Preview first
   python dev/move_false_positives.py \
       -r /Users/sgurivireddy/llm_artifacts/WebKit/results/code_analysis \
       -f /path/to/false_positives.csv \
       --dry-run
   
   # Execute
   python dev/move_false_positives.py \
       -r /Users/sgurivireddy/llm_artifacts/WebKit/results/code_analysis \
       -f /path/to/false_positives.csv
   ```

6. **Regenerate Report (Optional)**
   - If needed, regenerate HTML report from remaining issues
   - The dropped issues are preserved in `dropped_issues/` for audit

---

## Alternative Designs Considered

### Alternative 1: In-Place Marking Instead of Moving

Instead of moving files, add a `dropped: true` field to the JSON:

**Pros:**
- No file movement, simpler
- Preserves original location

**Cons:**
- Requires modifying report generator to filter dropped issues
- Harder to audit what was dropped
- Pollutes the main directory

**Decision:** Rejected - Moving provides cleaner separation and easier auditing.

### Alternative 2: Database-Based Tracking

Store dropped status in a SQLite database:

**Pros:**
- More flexible querying
- Can track history of drops/restores

**Cons:**
- Adds complexity
- Requires database management
- Overkill for this use case

**Decision:** Rejected - File-based approach is simpler and sufficient.

### Alternative 3: Symbolic Links

Keep files in place but create symlinks in dropped_issues:

**Pros:**
- No data duplication
- Easy to "restore" by removing symlink

**Cons:**
- Platform compatibility issues (Windows)
- Confusing directory structure

**Decision:** Rejected - Moving is more portable and clearer.

---

## Testing Plan

### Unit Tests

1. **Test CSV parsing**
   - Valid CSV with all columns
   - CSV with quoted fields containing commas
   - Empty lines and comment lines (starting with #)
   - Missing optional columns (issue_title, issue_description)
   - Invalid CSV format handling

2. **Test file matching**
   - Checksum matches existing file
   - Checksum not found in any file
   - Multiple files (edge case - should not happen with unique checksums)

3. **Test file moving**
   - Successful moves
   - Permission errors
   - Destination already exists
   - Source file deleted during operation

### Integration Tests

1. **End-to-end workflow**
   - Create sample analysis files with known checksums
   - Create false positives CSV file
   - Run script
   - Verify files moved correctly
   - Verify log file created with correct structure
   - Verify issue_title and issue_description preserved in log

2. **Dry run mode**
   - Verify no files moved
   - Verify output shows what would be moved with issue titles

---

## Future Enhancements

1. **Restore functionality**: Script to move files back from dropped_issues
2. **Batch processing**: Process multiple repositories at once
3. **Integration with CI/CD**: Automatic false positive detection in pipelines
4. **Web UI**: Interface for reviewing and marking false positives
5. **Machine learning**: Train model on confirmed false positives to auto-detect

---

## Implementation Checklist

- [x] Implement `print_analysis_summary()` in report service (implemented in [`hindsight/analyzers/code_analyzer.py`](../hindsight/analyzers/code_analyzer.py:1840))
- [x] Create `dev/move_false_positives.py` script (implemented)
- [x] Add unit tests for CSV parsing (implemented in [`hindsight/tests/dev/test_move_false_positives.py`](../hindsight/tests/dev/test_move_false_positives.py))
- [x] Add unit tests for file matching (implemented in [`hindsight/tests/dev/test_move_false_positives.py`](../hindsight/tests/dev/test_move_false_positives.py))
- [x] Add integration tests (implemented in [`hindsight/tests/dev/test_move_false_positives.py`](../hindsight/tests/dev/test_move_false_positives.py))
- [x] Update documentation (this document)
- [x] Add example `false_positives.csv` file to docs (created at [`docs/examples/false_positives.csv`](../docs/examples/false_positives.csv))
- [x] Document CSV format specification for external systems (documented in this file and in the example CSV)

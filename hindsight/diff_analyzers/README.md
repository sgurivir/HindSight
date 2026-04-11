# Git Diff Analyzer

This module provides functionality to analyze code changes between git commits using Hindsight code analysis.

## Usage

### As a Python Module

```bash
python3 -m hindsight.diff_analyzers.git_diff_analyzer --help
```

### Direct Script Execution

```bash
python3 hindsight/diff_analyzers/git_diff_analyzer.py --help
```

## Examples

### Analyze changes between two specific commits:

```bash
python3 -m hindsight.diff_analyzers.git_diff_analyzer \
  --repo_url https://github.com/user/repo.git \
  --config config.json \
  --repo_checkout_dir /tmp/repo \
  --diff_out_dir /tmp/diff \
  --c1 abc123 \
  --c2 def456
```

### Analyze changes between a commit and its parent:

```bash
python3 -m hindsight.diff_analyzers.git_diff_analyzer \
  --repo_url https://github.com/user/repo.git \
  --config config.json \
  --repo_checkout_dir /tmp/repo \
  --diff_out_dir /tmp/diff \
  --c1 abc123
```

## Arguments

- `--repo_url`: URL of the git repository to analyze
- `--config`: Path to JSON configuration file (similar format as CodeAnalysisRunner)
- `--repo_checkout_dir`: Directory to checkout the repository
- `--diff_out_dir`: Output directory for diff analysis results
- `--c1`: First commit hash
- `--c2`: Second commit hash (optional - if not provided, will use parent of c1)
- `-v, --verbose`: Enable verbose logging

## Output

The script generates:
1. Code analysis results for the old commit in `diff_out_dir/A/`
2. Individual HTML report for the old commit (baseline) in `diff_out_dir/A/html_reports/`
3. Code analysis results for the new commit in `diff_out_dir/B/`
4. Individual HTML report for the new commit (current) in `diff_out_dir/B/html_reports/`
5. A diff HTML report as `hindsight_diff_<timestamp>.html` in `diff_out_dir/`

The individual reports show all issues found in each commit, while the diff report shows only the new issues introduced in the current commit compared to the baseline.

## Configuration

The configuration file should follow the same format as used by CodeAnalysisRunner. See `hindsight/example_configs/repo_analysis/` for examples.
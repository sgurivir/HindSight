# `--generate-from-text-file` Implementation Plan

## Goal

Add a CLI flag to `hindsight.analyzers.code_analyzer` that consumes the text
produced by the HTML report's "Copy All" button and re-renders an HTML report
with the same look and per-issue metadata (file path, function name, line
number, severity, category) as the original.

This is a pure re-rendering tool. It does **not** call the LLM, read AST, or
touch the artifacts cache.

Keep code modular and avoid duplication of code wherever applicable, for original report generator and the code executed when this argument is passed. 

## Prerequisite (already done — Part A)

The "Copy All" and per-issue copy buttons in
[hindsight/report/report_generator.py](../hindsight/report/report_generator.py)
and
[hindsight/report/enhanced_report_generator.py](../hindsight/report/enhanced_report_generator.py)
now emit a metadata block between `Title:` and `Impact:`:

```
Title:
{issue}

Severity: {severity}
Category: {category}
File: {file_path}
Function: {function_name}
Lines: {line_number}

Impact:
{description}

Evidence:
{evidence}            ← optional, omitted when absent

Potential Solution:
{suggestion}
======================
```

`enhanced_report_generator.py` additionally emits trace-only sections
(`Normalized Cost:` appended to the Title line, `Original Trace Callstack:`
appended after `Potential Solution:`) when those fields are populated.

## Non-goals

- **No backward compatibility with old "Copy All" output.** Text files
  produced before Part A landed are not supported. Users who need to
  re-render an older report should rerun `--generate-report-from-existing-issues`
  against the original artifacts directory.
- No support for editing the text file's metadata to redirect issues to
  different files; the parser is faithful to whatever the user pastes.

## Part B work items

### 1. Parser module — `hindsight/report/text_file_issue_parser.py`

Single public function:

```python
def parse_issues_text_file(path: str) -> list[dict]:
    ...
```

Implementation:

1. Read the file as UTF-8.
2. Split on the line `======================`. Strip surrounding whitespace
   from each block. Discard empty blocks.
3. For each block, extract sections via a single regex pass that matches
   labeled headers at the start of a line: `Title:`, `Severity:`,
   `Category:`, `File:`, `Function:`, `Lines:`, `Impact:`, `Evidence:`
   (optional), `Potential Solution:`. Use a sentinel-based parser
   (split at known headers, then trim) so multi-line bodies are preserved
   verbatim.
4. Build a dict matching the schema consumed by
   `generate_html_report` (see [report_generator.py:25](../hindsight/report/report_generator.py#L25)
   `read_llm_output_files` for the canonical shape):

   ```python
   {
       "issue":              <Title block>,
       "description":        <Impact block>,        # HTML uses description for "Impact"
       "evidence":           <Evidence block>,      # omit key if absent
       "suggestion":         <Potential Solution block>,
       "severity":           <Severity value, lowercased>,
       "category":           <Category value>,
       "file_path":          <File value>,
       "original_file_path": <File value>,          # directory tree reads either
       "function_name":      <Function value>,
       "line_number":        <Lines value>,
       "external_references": [],
   }
   ```

5. Validation: if any required header is missing in a block, raise
   `ValueError` with the block index and the missing field. The user gets a
   clear error rather than a silently degraded report.

### 2. CLI wiring — `hindsight/analyzers/code_analyzer.py`

Add three arguments next to `--generate-report-from-existing-issues`
(around [code_analyzer.py:3116](../hindsight/analyzers/code_analyzer.py#L3116)):

```python
parser.add_argument(
    "--generate-from-text-file",
    metavar="PATH",
    help="Path to a text file produced by the report's 'Copy All' button. "
         "Re-renders an HTML report from the text without running analysis. "
         "Mutually exclusive with --generate-report-from-existing-issues.",
)
parser.add_argument(
    "--text-file-project-name",
    metavar="NAME",
    help="Optional repository label for the regenerated report header. "
         "If omitted, derived from the input filename.",
)
parser.add_argument(
    "--text-file-output",
    metavar="PATH",
    help="Optional output HTML path. Default: ~/llm_artifacts/from_text/"
         "repo_analysis_<project>_<timestamp>_from_text.html",
)
```

Make `--config` and `--repo` not `required=True` when
`--generate-from-text-file` is provided. Two ways: (a) flip
`required=False` and validate manually, or (b) split into subcommands. Pick
(a) — minimum disruption to the existing CLI.

Branch *before* the existing `args.generate_report_from_existing_issues`
block, immediately after `args = parser.parse_args()`:

```python
if args.generate_from_text_file:
    if args.generate_report_from_existing_issues:
        logger.error("--generate-from-text-file and --generate-report-from-existing-issues are mutually exclusive")
        sys.exit(1)
    if not os.path.isfile(args.generate_from_text_file):
        logger.error("Input text file not found: %s", args.generate_from_text_file)
        sys.exit(1)

    from ..report.text_file_issue_parser import parse_issues_text_file
    from ..report.report_generator import generate_html_report

    issues = parse_issues_text_file(args.generate_from_text_file)
    project_name = args.text_file_project_name or _derive_project_name(args.generate_from_text_file)
    out_path = args.text_file_output or _default_text_file_output(project_name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    report_path = generate_html_report(
        issues,
        output_file=out_path,
        project_name=project_name,
        analysis_type="Code Analysis",
    )
    logger.info("HTML report written to: %s", report_path)
    sys.exit(0)
```

Helpers (`_derive_project_name`, `_default_text_file_output`) live in
`code_analyzer.py` next to the new branch.

### 3. Output naming

Match the existing convention from
[code_analyzer.py:_generate_report](../hindsight/analyzers/code_analyzer.py#L1813):

```
~/llm_artifacts/from_text/repo_analysis_<ProjectName>_<YYYYMMDD_HHMMSS>_from_text.html
```

`from_text/` keeps regenerated reports out of the artifacts tree of any
specific repo (since this flow does not require `--repo`).

### 4. Tests — `hindsight/tests/report/test_text_file_issue_parser.py`

- **Round-trip from synthetic data.** Build a small Python helper that
  formats a list of issue dicts using the same template as the JS Copy All.
  Format → parse → assert dict-equality on all 10 fields.
- **Optional Evidence.** Issue without `Evidence:` block parses with no
  `evidence` key.
- **Real fixture.** Add `tests/report/fixtures/copy_all_sample.txt`
  (3 entries, copied from a fresh report after Part A). Assert count and
  spot-check fields on the first and last entries.
- **Malformed input.** A block missing `Severity:` raises `ValueError` with
  block index + field name.

### 5. Manual verification (done after implementation)

1. Re-run code_analyzer with `--generate-report-from-existing-issues` to
   produce a fresh HTML with the new Copy All buttons.
2. Open the HTML, click Copy All, paste into a `.txt` file.
3. Run `python3 -m hindsight.analyzers.code_analyzer
   --generate-from-text-file <path>.txt --text-file-project-name
   "Conversation Intelligence"`.
4. Open the regenerated HTML and confirm: directory sidebar shows the same
   tree, severity counts match, file/function/lines render on each card,
   Copy All round-trips back to identical text.

## File touch list

| Path | Change |
|------|--------|
| `hindsight/report/text_file_issue_parser.py` | New |
| `hindsight/analyzers/code_analyzer.py` | +3 CLI args, +1 branch, +2 helpers (~50 lines) |
| `hindsight/tests/report/test_text_file_issue_parser.py` | New |
| `hindsight/tests/report/fixtures/copy_all_sample.txt` | New |

## Estimated size

Parser ~80 LoC, CLI wiring ~50 LoC, tests ~100 LoC. No changes to the HTML
generator itself.

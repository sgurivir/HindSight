# Dropped Issues HTML Report Implementation Plan

## Overview

This document outlines the implementation plan for automatically generating an HTML report of dropped issues alongside the regular analysis report. The dropped issues report will show all issues that were filtered out during the 3-level filtering process, with metadata indicating which stage dropped each issue.

## Current Architecture

### 3-Level Filtering System

The current filtering system in [`unified_issue_filter.py`](../hindsight/issue_filter/unified_issue_filter.py) implements:

1. **Level 1 - Category Filter**: Hard filter based on allowed categories (logicBug, performance)
2. **Level 2 - LLM Filter**: Intelligent filter for trivial/obvious issues
3. **Level 3 - Response Challenger**: Senior Software Engineer LLM review

### Current Dropped Issues Storage

Dropped issues are currently stored in:
- **Level 3 only**: `/llm_artifacts/{repo_name}/dropped_issues/level3_response_challenger/`
- Format: Individual JSON files per dropped issue
- Contains: `filter_level`, `filter_type`, `reason`, `original_issue`

### Current Report Generation

The main report is generated in [`code_analyzer.py`](../hindsight/analyzers/code_analyzer.py) at line 1527 via [`_generate_report()`](../hindsight/analyzers/code_analyzer.py:1527) which calls [`generate_html_report()`](../hindsight/report/report_generator.py:192).

## Implementation Plan

### Phase 1: Enhance Dropped Issues Tracking

#### 1.1 Modify CategoryBasedFilter (Level 1)

**File**: [`hindsight/issue_filter/category_filter.py`](../hindsight/issue_filter/category_filter.py)

**Changes**:
- Add `dropped_issues_dir` parameter to constructor
- Add `_save_dropped_issue()` method similar to response_challenger.py
- Track dropped issues with metadata:
  ```python
  {
      "timestamp": "ISO timestamp",
      "filter_level": "Level 1 - Category Filter",
      "filter_type": "Category-based filtering",
      "reason": "Category '{category}' not in allowed list: {allowed_categories}",
      "original_issue": {...}
  }
  ```

#### 1.2 Modify LLMBasedFilter (Level 2)

**File**: [`hindsight/issue_filter/llm_filter.py`](../hindsight/issue_filter/llm_filter.py)

**Changes**:
- Ensure dropped issues are saved with consistent format
- Add filter level metadata to dropped issues

#### 1.3 Standardize Dropped Issue Format

Create a common schema for all dropped issues:

```python
{
    "timestamp": "2026-03-13T10:30:00.000Z",
    "filter_level": "Level 1|Level 2|Level 3",
    "filter_type": "Category Filter|LLM Filter|Response Challenger",
    "reason": "Detailed reason for dropping",
    "original_issue": {
        "issue": "...",
        "category": "...",
        "severity": "...",
        "file_path": "...",
        "function_name": "...",
        "description": "...",
        "suggestion": "..."
    }
}
```

### Phase 2: Create Dropped Issues Report Generator

#### 2.1 New Function: `generate_dropped_issues_html_report()`

**File**: [`hindsight/report/report_generator.py`](../hindsight/report/report_generator.py)

**New Function**:
```python
def generate_dropped_issues_html_report(
    dropped_issues: List[Dict[str, Any]],
    output_file: str = None,
    project_name: str = None,
    analysis_type: str = "Dropped Issues Report"
) -> str:
    """
    Generate HTML report for dropped issues with amber/orange color theme.
    
    Args:
        dropped_issues: List of dropped issue dictionaries with filter metadata
        output_file: Output file path
        project_name: Project name for the report
        analysis_type: Type of analysis (default: "Dropped Issues Report")
    
    Returns:
        Path to generated HTML report
    """
```

**Key Differences from Main Report**:
1. **Color Theme**: Amber/orange instead of blue
   - Primary color: `#f59e0b` (amber-500)
   - Background: `#fffbeb` (amber-50)
   - Accent: `#d97706` (amber-600)
   
2. **Additional Metadata Display**:
   - Filter Level badge (Level 1, Level 2, Level 3)
   - Filter Type
   - Reason for dropping
   
3. **Sidebar Filtering**:
   - Filter by Level (Level 1, Level 2, Level 3)
   - Filter by Category
   - Filter by Severity

#### 2.2 HTML Template Changes

**New CSS Variables for Amber Theme**:
```css
:root {
    --dropped-primary: #f59e0b;
    --dropped-secondary: #d97706;
    --dropped-bg: #fffbeb;
    --dropped-border: #fcd34d;
    --dropped-text: #92400e;
}

.dropped-badge {
    background: var(--dropped-bg);
    color: var(--dropped-text);
    border: 1px solid var(--dropped-border);
}

.level-1 { background: #fee2e2; color: #991b1b; }
.level-2 { background: #fef3c7; color: #92400e; }
.level-3 { background: #dbeafe; color: #1e40af; }
```

**New Issue Card Structure**:
```html
<section class="issue dropped-issue">
    <header class="issue__head">
        <h2 class="issue__file">{filename}</h2>
        <div class="badges">
            <span class="badge badge--level level-{n}">Level {n}</span>
            <span class="badge badge--severity">{severity}</span>
        </div>
    </header>
    
    <div class="issue__meta">
        <dl class="kv">
            <div><dt>Filter Type</dt><dd>{filter_type}</dd></div>
            <div><dt>Function</dt><dd><code>{function_name}</code></dd></div>
            <div><dt>Category</dt><dd>{category}</dd></div>
        </dl>
    </div>
    
    <article class="issue__body">
        <h3 class="issue__title">Original Issue</h3>
        <p>{issue}</p>
        
        <details class="callout callout--reason" open>
            <summary>Reason for Dropping</summary>
            <p>{reason}</p>
        </details>
        
        <details class="callout callout--suggestion">
            <summary>Original Suggestion</summary>
            <p>{suggestion}</p>
        </details>
    </article>
</section>
```

### Phase 3: Integrate with Code Analyzer

#### 3.1 Modify `_generate_report()` Method

**File**: [`hindsight/analyzers/code_analyzer.py`](../hindsight/analyzers/code_analyzer.py)

**Location**: Line 1527

**Changes**:
```python
def _generate_report(self, config: dict) -> tuple:
    """Generate HTML reports from analysis results."""
    # ... existing code for main report ...
    
    # Generate dropped issues report
    dropped_report_result = self._generate_dropped_issues_report(config)
    
    return (main_report_success, main_report_file, 
            dropped_report_result[0], dropped_report_result[1])
```

#### 3.2 New Method: `_generate_dropped_issues_report()`

```python
def _generate_dropped_issues_report(self, config: dict) -> tuple:
    """Generate HTML report for dropped issues."""
    from ..report.report_generator import generate_dropped_issues_html_report
    
    # Collect dropped issues from all levels
    dropped_issues = self._collect_dropped_issues(config)
    
    if not dropped_issues:
        self.logger.info("No dropped issues to report")
        return (False, None)
    
    # Generate report filename
    project_name = config.get('project_name', '')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_filename = f"dropped_issues_{project_name}_{timestamp}.html"
    
    reports_dir = self.get_reports_directory()
    report_file_path = os.path.join(reports_dir, report_filename)
    
    # Generate the report
    report_file = generate_dropped_issues_html_report(
        dropped_issues,
        output_file=report_file_path,
        project_name=project_name
    )
    
    self.logger.info(f"Dropped issues report generated: {report_file}")
    return (True, report_file)
```

#### 3.3 New Method: `_collect_dropped_issues()`

```python
def _collect_dropped_issues(self, config: dict) -> List[Dict[str, Any]]:
    """Collect all dropped issues from all filter levels."""
    dropped_issues = []
    
    # Get dropped issues directory
    output_provider = get_output_directory_provider()
    artifacts_dir = output_provider.get_repo_artifacts_dir()
    dropped_issues_base = os.path.join(artifacts_dir, "dropped_issues")
    
    # Collect from each level
    level_dirs = {
        "level1_category_filter": "Level 1",
        "level2_llm_filter": "Level 2", 
        "level3_response_challenger": "Level 3"
    }
    
    for dir_name, level_name in level_dirs.items():
        level_dir = os.path.join(dropped_issues_base, dir_name)
        if os.path.exists(level_dir):
            for filename in os.listdir(level_dir):
                if filename.endswith('.json'):
                    filepath = os.path.join(level_dir, filename)
                    try:
                        with open(filepath, 'r') as f:
                            issue_data = json.load(f)
                            # Ensure filter_level is set
                            if 'filter_level' not in issue_data:
                                issue_data['filter_level'] = level_name
                            dropped_issues.append(issue_data)
                    except Exception as e:
                        self.logger.warning(f"Failed to read dropped issue: {e}")
    
    return dropped_issues
```

### Phase 4: Update UnifiedIssueFilter

#### 4.1 Add Dropped Issues Directory Setup

**File**: [`hindsight/issue_filter/unified_issue_filter.py`](../hindsight/issue_filter/unified_issue_filter.py)

**Changes**:
```python
def __init__(self, api_key: str, config: dict, ...):
    # ... existing code ...
    
    # Setup dropped issues directories for all levels
    self._setup_dropped_issues_directories()
    
    # Pass dropped_issues_dir to Level 1 filter
    self.category_filter = CategoryBasedFilter(
        additional_allowed_categories,
        dropped_issues_dir=self._get_level_dropped_dir("level1_category_filter")
    )
    
    # Pass dropped_issues_dir to Level 2 filter
    if enable_llm_filtering and api_key:
        self.llm_filter = LLMBasedFilter(
            api_key, config,
            dropped_issues_dir=self._get_level_dropped_dir("level2_llm_filter"),
            file_content_provider
        )
```

### Phase 5: Testing

#### 5.1 Unit Tests

**New Test File**: `hindsight/tests/report/test_dropped_issues_report.py`

```python
def test_generate_dropped_issues_html_report():
    """Test dropped issues HTML report generation."""
    
def test_dropped_issues_color_theme():
    """Test that dropped issues report uses amber color theme."""
    
def test_filter_level_badges():
    """Test that filter level badges are correctly displayed."""
    
def test_sidebar_level_filtering():
    """Test sidebar filtering by filter level."""
```

#### 5.2 Integration Tests

```python
def test_code_analyzer_generates_both_reports():
    """Test that code analyzer generates both main and dropped issues reports."""
    
def test_dropped_issues_collected_from_all_levels():
    """Test that dropped issues are collected from all filter levels."""
```

## File Changes Summary

| File | Changes |
|------|---------|
| `hindsight/issue_filter/category_filter.py` | Add dropped issues saving |
| `hindsight/issue_filter/llm_filter.py` | Standardize dropped issues format |
| `hindsight/issue_filter/unified_issue_filter.py` | Setup dropped issues directories for all levels |
| `hindsight/report/report_generator.py` | Add `generate_dropped_issues_html_report()` function |
| `hindsight/analyzers/code_analyzer.py` | Add `_generate_dropped_issues_report()` and `_collect_dropped_issues()` methods |
| `hindsight/analyzers/trace_analyzer.py` | Similar changes for trace analysis |

## Color Theme Comparison

| Element | Main Report (Blue) | Dropped Report (Amber) |
|---------|-------------------|------------------------|
| Primary | `#007bff` | `#f59e0b` |
| Secondary | `#0056b3` | `#d97706` |
| Background | `#f5f5f7` | `#fffbeb` |
| Border | `#e5e5ea` | `#fcd34d` |
| Text | `#1d1d1f` | `#92400e` |

## Implementation Order

1. **Phase 1**: Enhance dropped issues tracking (2-3 hours)
   - Modify category_filter.py
   - Modify llm_filter.py
   - Standardize format

2. **Phase 2**: Create dropped issues report generator (3-4 hours)
   - New function in report_generator.py
   - Amber color theme CSS
   - Filter level badges

3. **Phase 3**: Integrate with code analyzer (2-3 hours)
   - Modify _generate_report()
   - Add _generate_dropped_issues_report()
   - Add _collect_dropped_issues()

4. **Phase 4**: Update UnifiedIssueFilter (1-2 hours)
   - Setup directories for all levels
   - Pass directories to filters

5. **Phase 5**: Testing (2-3 hours)
   - Unit tests
   - Integration tests

**Total Estimated Time**: 10-15 hours

## Future Enhancements

1. **Export to CSV**: Add option to export dropped issues to CSV format
2. **Comparison View**: Side-by-side view of kept vs dropped issues
3. **Trend Analysis**: Track dropped issues over time across multiple runs
4. **Re-evaluation**: Allow users to mark dropped issues for re-evaluation

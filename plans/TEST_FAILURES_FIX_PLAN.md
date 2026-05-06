# Test Failures Fix Plan

## Summary
14 tests are failing across 5 categories. This document outlines the fixes needed.

## Category 1: Knowledge Store Path Test (1 test)
**Status**: Already fixed - test renamed from `test_contains_llm_artifacts` to `test_path_uses_repo_artifacts_dir`

### Test
- `hindsight/tests/core/knowledge/test_knowledge_store.py::TestKnowledgeStoreDbPath::test_contains_llm_artifacts`

### Issue
Test expected `llm_artifacts` in path but code now uses `knowledge_base` directory.

### Fix
Test has been renamed to `test_path_uses_repo_artifacts_dir` and now checks for `knowledge_base`.

---

## Category 2: Fallback Behavior Tests (7 tests)
**Status**: Tests need updating to match new fallback behavior

### Tests
1. `test_context_collection_analyzer.py::test_extract_json_returns_none_for_wrong_dict`
2. `test_diff_analysis_analyzer.py::test_extract_json_returns_none_for_dict_only`
3. `test_diff_context_analyzer.py::test_extract_json_returns_none_for_wrong_dict`
4. `test_diff_context_analyzer.py::test_validate_json_rejects_dict_without_changed_functions`
5. `test_json_extraction.py::test_context_collection_rejects_issue_array`
6. `test_json_extraction.py::test_diff_context_rejects_issue_array`
7. `test_json_extraction.py::test_diff_analysis_rejects_diff_context_bundle`

### Issue
The analyzers now have fallback behavior that accepts dicts without expected keys (with warnings) instead of returning `None`. This is intentional for robustness.

### Fix
Update tests to expect the new fallback behavior:
- Tests expecting `None` should now expect the fallback dict/array to be returned
- Tests for `validate_json` should be updated if validation logic changed

---

## Category 3: Return Type Issues (4 tests)
**Status**: Tests are hitting cached results instead of mocked analyzer

### Tests
1. `test_diff_analysis.py::test_returns_valid_bundle_on_success` - expects dict, gets string
2. `test_diff_analysis.py::test_returns_none_on_invalid_json` - expects None, gets string
3. `test_diff_analysis.py::test_returns_issues_list_on_success` - expects list, gets string
4. `test_diff_analysis.py::test_returns_empty_list_when_no_issues` - expects [], gets '[]'

### Root Cause
The tests are finding cached bundles from previous test runs at `/tmp/artifacts/diff_context_bundles/`. The cache lookup happens before the mocked analyzer is called.

### Fix
Add `os.path.exists` mock to return `False` so the cache is bypassed:
```python
@patch('os.path.exists', return_value=False)
```

Or use unique function names per test to avoid cache collisions.

---

## Category 4: Stage B Tools Test (1 test)
**Status**: Test expectation doesn't match intentional design

### Test
- `test_tools.py::test_stage_b_contains_allowed_tools`

### Issue
Test expects `store_knowledge` in Stage B tools, but it's intentionally excluded.

### Fix
Update test to expect `store_knowledge` NOT in Stage B tools (it's only in Stage A).

---

## Category 5: Cache Loading Test (1 test)
**Status**: Test is hitting cache instead of testing retry behavior

### Test
- `test_code_analysis.py::test_returns_none_on_invalid_json_after_retry`

### Issue
Test expects `None` when LLM returns invalid JSON, but the code finds an existing cached bundle and returns that instead.

### Fix
Mock `os.path.exists` to return `False` for the cache path, or use a unique checksum.

---

## Implementation Order

1. **Category 3 & 5**: Fix cache bypass in tests (add `os.path.exists` mock)
2. **Category 4**: Update Stage B tools test expectation
3. **Category 2**: Update fallback behavior tests
4. **Category 1**: Already fixed (verify)

## Files to Modify

1. `hindsight/tests/core/llm/test_diff_analysis.py` - Add cache bypass mocks
2. `hindsight/tests/core/llm/test_code_analysis.py` - Add cache bypass mock
3. `hindsight/tests/core/llm/tools/test_tools.py` - Update Stage B expectation
4. `hindsight/tests/core/llm/iterative/test_context_collection_analyzer.py` - Update fallback tests
5. `hindsight/tests/core/llm/iterative/test_diff_analysis_analyzer.py` - Update fallback tests
6. `hindsight/tests/core/llm/iterative/test_diff_context_analyzer.py` - Update fallback tests
7. `hindsight/tests/core/llm/iterative/test_json_extraction.py` - Update fallback tests

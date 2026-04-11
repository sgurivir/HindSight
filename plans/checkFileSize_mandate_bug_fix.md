# Bug Fix Plan: checkFileSize Mandate Gap

## Bug Summary

**Bug ID**: Bug 1  
**Title**: checkFileSize mandate doesn't cover getFileContentByLines  
**Location**: System prompt in [`hindsight/core/prompts/systemPrompt.md`](../hindsight/core/prompts/systemPrompt.md:211)

### Problem Description

The current mandate in the system prompt states:
> "ALWAYS use checkFileSize BEFORE readFile"

However, this mandate is **silent on `getFileContentByLines`**. The LLM exploits this gap by using `getFileContentByLines` without first checking file size, which causes repeated out-of-bounds failures when the LLM requests line ranges that exceed the actual file length.

### Additional Requirement

The user also wants `checkFileSize` to return both **file size** and **number of lines** in its response.

---

## Analysis Findings

### Current State

1. **Mandate Location**: [`systemPrompt.md`](../hindsight/core/prompts/systemPrompt.md:211) line 211
   ```
   1. **ALWAYS use `checkFileSize` BEFORE `readFile`** to determine if file is within size limits
   ```

2. **checkFileSize Implementation**: [`file_tools.py`](../hindsight/core/llm/tools/file_tools.py:262-415)
   - **Good news**: The implementation already returns `line_count` in the response (line 374)
   - Returns a JSON object with: `file_available`, `file_path`, `size_bytes`, `size_characters`, `line_count`, `within_size_limit`, `recommended_for_readFile`, `size_limits`

3. **Documentation in systemPrompt.md**: Lines 313-328 show the expected output format, but the example doesn't prominently feature `line_count` as a key field for preventing out-of-bounds errors.

4. **Tool Definition**: [`tool_definitions.py`](../hindsight/core/llm/tools/tool_definitions.py:60-75) describes checkFileSize but doesn't mention line count in the description.

---

## Implementation Plan

### Task 1: Update the Mandate in systemPrompt.md

**File**: `hindsight/core/prompts/systemPrompt.md`

**Change Location**: Line 211

**Current Text**:
```markdown
1. **ALWAYS use `checkFileSize` BEFORE `readFile`** to determine if file is within size limits
```

**New Text**:
```markdown
1. **ALWAYS use `checkFileSize` BEFORE `readFile` or `getFileContentByLines`** to determine if file is within size limits and get the total line count
```

### Task 2: Update the Tool Priority Section

**File**: `hindsight/core/prompts/systemPrompt.md`

**Change Location**: Lines 210-213

**Current Text**:
```markdown
**CRITICAL TOOL USAGE PRIORITY:**
1. **ALWAYS use `checkFileSize` BEFORE `readFile`** to determine if file is within size limits
2. Use `getSummaryOfFile` to quickly understand a file's purpose and context before deeper analysis
3. Use `runTerminalCmd` for exploration and searching when the above tools are insufficient
```

**New Text**:
```markdown
**CRITICAL TOOL USAGE PRIORITY:**
1. **ALWAYS use `checkFileSize` BEFORE `readFile` or `getFileContentByLines`** to determine if file is within size limits and get the total line count (prevents out-of-bounds errors)
2. Use `getSummaryOfFile` to quickly understand a file's purpose and context before deeper analysis
3. Use `runTerminalCmd` for exploration and searching when the above tools are insufficient
```

### Task 3: Update checkFileSize Documentation Section

**File**: `hindsight/core/prompts/systemPrompt.md`

**Change Location**: Lines 299-336 (checkFileSize Tool section)

Update the description and example to emphasize line count usage:

**Current Description** (line 300-301):
```markdown
**Purpose**: Check if a file exists and get its size information to determine if it's safe to use readFile
**Usage**: **ALWAYS USE BEFORE readFile** to prevent "file too large" errors and choose appropriate tools
```

**New Description**:
```markdown
**Purpose**: Check if a file exists and get its size and line count information. Returns total line count to prevent out-of-bounds errors with getFileContentByLines.
**Usage**: **ALWAYS USE BEFORE readFile or getFileContentByLines** to prevent "file too large" errors, choose appropriate tools, and know the valid line range for the file.
```

**Update Example Output** (lines 313-328) to highlight `line_count`:

```json
{
  "file_available": true,
  "file_path": "app/src/main/java/org/thoughtcrime/securesms/util/ViewUtil.java",
  "size_bytes": 12340,
  "size_characters": 12234,
  "line_count": 434,
  "within_size_limit": true,
  "recommended_for_readFile": true,
  "size_limits": {
    "max_characters": 16000,
    "max_bytes": 1048576
  },
  "warning": null
}
```

Add a note after the example:
```markdown
**IMPORTANT**: Use the `line_count` field to ensure your `startLine` and `endLine` parameters for `getFileContentByLines` are within valid bounds (1 to line_count).
```

### Task 4: Update Decision Making Section

**File**: `hindsight/core/prompts/systemPrompt.md`

**Change Location**: Lines 331-335

**Current Text**:
```markdown
**Decision Making Based on checkFileSize Results**:
- **recommended_for_readFile: true** → Safe to use readFile
- **recommended_for_readFile: false** → Use getSummaryOfFile or getFileContentByLines instead
- **file_available: false** → File not found, check path or search for similar files
```

**New Text**:
```markdown
**Decision Making Based on checkFileSize Results**:
- **recommended_for_readFile: true** → Safe to use readFile
- **recommended_for_readFile: false** → Use getSummaryOfFile or getFileContentByLines instead
- **file_available: false** → File not found, check path or search for similar files
- **line_count** → Use this to validate line ranges before calling getFileContentByLines (startLine and endLine must be ≤ line_count)
```

### Task 5: Update Critical Reminder

**File**: `hindsight/core/prompts/systemPrompt.md`

**Change Location**: Line 336

**Current Text**:
```markdown
**CRITICAL**: Always use checkFileSize before readFile when file size is unknown. This prevents analysis interruption due to size limits.
```

**New Text**:
```markdown
**CRITICAL**: Always use checkFileSize before readFile or getFileContentByLines when file size is unknown. This prevents analysis interruption due to size limits and out-of-bounds line number errors.
```

### Task 6: Update Tool Definition Description

**File**: `hindsight/core/llm/tools/tool_definitions.py`

**Change Location**: Lines 60-75

**Current Description**:
```python
"checkFileSize": {
    "description": "Check if a file exists and get its size information. Use this before readFile to determine if the file is within size limits.",
```

**New Description**:
```python
"checkFileSize": {
    "description": "Check if a file exists and get its size and line count information. Use this before readFile or getFileContentByLines to determine if the file is within size limits and to get the total line count for valid line range validation.",
```

---

## Verification Checklist

After implementation, verify:

- [ ] **systemPrompt.md**: Mandate on line 211 includes `getFileContentByLines`
- [ ] **systemPrompt.md**: checkFileSize tool section mentions line count prominently
- [ ] **systemPrompt.md**: Example output shows `line_count` field
- [ ] **systemPrompt.md**: Decision making section includes guidance on using `line_count`
- [ ] **systemPrompt.md**: Critical reminder mentions both `readFile` and `getFileContentByLines`
- [ ] **analysisTools.md**: Mandate includes `getFileContentByLines`
- [ ] **analysisTools.md**: checkFileSize documentation updated
- [ ] **functionDiffAnalysisPrompt.md**: Mandate includes `getFileContentByLines`
- [ ] **diffAnalysisPrompt.md**: Decision tree mentions `line_count`
- [ ] **systemPromptTrace.md**: readFile reference updated
- [ ] **detailedAnalysisProcess.md**: Critical reminder updated
- [ ] **tool_definitions.py**: checkFileSize description updated

---

## Files to Modify

| File | Line(s) | Changes |
|------|---------|---------|
| `hindsight/core/prompts/systemPrompt.md` | 211, 249, 300-301, 331-336 | Update mandate, documentation, examples |
| `hindsight/core/prompts/analysisTools.md` | 58, 84-85, 97-100, 149 | Update mandate and checkFileSize documentation |
| `hindsight/core/prompts/functionDiffAnalysisPrompt.md` | 193, 214-215, 270 | Update mandate and checkFileSize documentation |
| `hindsight/core/prompts/diffAnalysisPrompt.md` | 228-231 | Update decision tree to mention line_count |
| `hindsight/core/prompts/systemPromptTrace.md` | 116, 123 | Update readFile reference to include checkFileSize mandate |
| `hindsight/core/prompts/detailedAnalysisProcess.md` | 55 | Update critical reminder |
| `hindsight/core/llm/tools/tool_definitions.py` | 61 | Update checkFileSize description |

### Summary of Changes Per File

#### 1. systemPrompt.md (Primary - Most Comprehensive)
- **Line 211**: Change mandate from `readFile` only to `readFile or getFileContentByLines`
- **Line 249**: Update critical reminder to include `getFileContentByLines`
- **Lines 300-301**: Update checkFileSize purpose/usage description
- **Lines 331-336**: Add `line_count` to decision making guidance

#### 2. analysisTools.md
- **Line 58**: Update mandate to include `getFileContentByLines`
- **Lines 84-85**: Update checkFileSize purpose/usage
- **Lines 97-100**: Add `line_count` to decision making
- **Line 149**: Update readFile critical reminder

#### 3. functionDiffAnalysisPrompt.md
- **Line 193**: Update mandate to include `getFileContentByLines`
- **Lines 214-215**: Update checkFileSize purpose/usage
- **Line 270**: Update readFile critical reminder

#### 4. diffAnalysisPrompt.md
- **Lines 228-231**: Update decision tree to mention using `line_count` for `getFileContentByLines`

#### 5. systemPromptTrace.md
- **Line 116**: Update readFile reference to mention checkFileSize mandate
- **Line 123**: Already mentions checkFileSize, may need enhancement

#### 6. detailedAnalysisProcess.md
- **Line 55**: Update critical reminder to include `getFileContentByLines`

#### 7. tool_definitions.py
- **Line 61**: Update checkFileSize description to mention `getFileContentByLines` and line count

---

## No Code Changes Required

The `checkFileSize` implementation in [`file_tools.py`](../hindsight/core/llm/tools/file_tools.py:262-415) **already returns `line_count`** in its response (line 374). No changes to the Python implementation are needed.

```python
result = {
    "file_available": True,
    "file_path": display_path,
    "size_bytes": file_size_bytes,
    "size_characters": char_count,
    "line_count": line_count if line_count > 0 else None,  # Already present!
    ...
}
```

---

## Risk Assessment

**Risk Level**: Low

- Changes are documentation/prompt only
- No functional code changes required
- The `line_count` field is already being returned by the tool
- Changes improve LLM behavior without breaking existing functionality

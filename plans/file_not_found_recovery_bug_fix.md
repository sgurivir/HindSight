# Bug Fix Plan: File-Not-Found Recovery Instruction

## Problem

LLM retried the same nonexistent file path (`DataCollector+FetchData.swift`) 5 times at different line ranges.

## Solution

Add guidance to use `list_files` on the parent directory when a file is not found.

## Files to Modify

### Files with existing `file_available: false` bullet (update existing text)

| File | Line | Current Text | New Text |
|------|------|--------------|----------|
| [`systemPrompt.md`](../hindsight/core/prompts/systemPrompt.md:336) | 336 | `File not found, check path or search for similar files` | `File not found, use list_files on the parent directory to discover actual filenames` |
| [`analysisTools.md`](../hindsight/core/prompts/analysisTools.md:102) | 102 | `File not found, check path or search for similar files` | `File not found, use list_files on the parent directory to discover actual filenames` |

### Files with checkFileSize but no `file_available: false` guidance (add new bullet)

| File | Location | Action |
|------|----------|--------|
| [`functionDiffAnalysisPrompt.md`](../hindsight/core/prompts/functionDiffAnalysisPrompt.md:226) | After line 226 (IMPORTANT note) | Add: `If checkFileSize returns file_available: false, use list_files on the parent directory to discover actual filenames.` |
| [`diffAnalysisPrompt.md`](../hindsight/core/prompts/diffAnalysisPrompt.md:225) | After line 225 (CRITICAL note) | Add: `If a file is not found, use list_files on the parent directory to discover actual filenames.` |
| [`systemPromptTrace.md`](../hindsight/core/prompts/systemPromptTrace.md:126) | After line 126 (checkFileSize bullet) | Add: `If a file is not found, use list_files on the parent directory to discover actual filenames.` |
| [`detailedAnalysisProcess.md`](../hindsight/core/prompts/detailedAnalysisProcess.md:55) | After line 55 (CRITICAL note) | Add: `If a file is not found, use list_files on the parent directory to discover actual filenames.` |

## Implementation Summary

- **2 files**: Update existing `file_available: false` bullet point
- **4 files**: Add a single line about using `list_files` when file not found

Total: 6 files, minimal changes (one line each)

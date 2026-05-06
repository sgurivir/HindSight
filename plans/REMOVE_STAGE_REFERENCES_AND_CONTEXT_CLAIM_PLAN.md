# Plan: Remove Stage References and "Context Bundle Contains All Code" Claim

## Problem Statement

The LLM prompts in the code analysis pipeline contain two issues:

1. **"The context bundle below contains all the code you need"** - This claim is misleading because:
   - The LLM should be allowed to query more context if needed
   - The context bundle may not always be complete
   - This statement discourages the LLM from using available tools to gather additional context

2. **Stage references (Stage 4a, Stage 4b, Stage Da, Stage Db, etc.)** - These are confusing because:
   - Each LLM call uses a completely different context window
   - The LLM has no memory of previous stages
   - References to "Stage 4a under-collected context" make no sense to the LLM
   - Stage numbers are internal implementation details that shouldn't leak into prompts

---

## Files to Modify

### Category 1: Prompt Files (Sent to LLM - HIGH PRIORITY)

These files contain text that is directly sent to the LLM and must be fixed.

#### 1.1 [`hindsight/core/prompts/analysisProcess.md`](hindsight/core/prompts/analysisProcess.md)

| Line | Current Text | New Text |
|------|--------------|----------|
| 1 | `# Stage 4b — Analysis (CodeAnalysis Pipeline)` | `# Code Analysis` |
| 5 | `You are a senior software engineer performing a deep code review. The context bundle below contains all the code you need. Your job is to identify real, confirmed bugs and performance issues in the primary function — nothing else.` | `You are a senior software engineer performing a deep code review. Your job is to identify real, confirmed bugs and performance issues in the primary function — nothing else. Use the provided context bundle as your starting point, and use the available tools to gather additional context if needed.` |
| 32-34 | `## AVAILABLE TOOLS (Stage 4b — Reduced Set)\n\nStage 4b has a deliberately restricted tool set. If you find yourself reaching for unavailable tools frequently, this is a signal that Stage 4a under-collected context — note this in your output.` | `## AVAILABLE TOOLS\n\nYou have access to a focused set of tools for analysis. If you need additional context not present in the bundle, use the available tools to gather it.` |
| 171 | `This persists the analysis so Stage 4a can skip redundant collection on future runs.` | `This persists the analysis for future reference.` |
| 226-228 | `## SIGNALS THAT STAGE 4a UNDER-COLLECTED\n\nIf you find yourself needing to use \`getImplementation\` or \`runTerminalCmd\` more than once during Stage 4b, add a note in the \`description\` of your first issue (or in a sentinel issue with \`category: "logicBug"\` and \`issue: "Stage 4a context gap"\`) indicating which functions or types were missing from the context bundle. This helps improve the collection stage.` | `## CONTEXT GAPS\n\nIf you find yourself needing to use \`getImplementation\` or \`runTerminalCmd\` more than once, add a note in the \`description\` of your first issue indicating which functions or types were missing from the context bundle. This helps improve future context collection.` |

#### 1.2 [`hindsight/core/prompts/contextCollectionProcess.md`](hindsight/core/prompts/contextCollectionProcess.md)

| Line | Current Text | New Text |
|------|--------------|----------|
| 1 | `# Stage 4a — Context Collection (CodeAnalysis Pipeline)` | `# Context Collection` |

#### 1.3 [`hindsight/core/prompts/diffAnalysisProcess.md`](hindsight/core/prompts/diffAnalysisProcess.md)

| Line | Current Text | New Text |
|------|--------------|----------|
| 1 | `# Stage Db — Diff Analysis (DiffAnalysis Pipeline)` | `# Diff Analysis` |
| 5 | `You are a senior software engineer performing a deep code review focused on **diff-introduced regressions**. The diff context bundle below contains all the code you need. Your job is to identify real, confirmed bugs and performance issues that were introduced or made worse by the changes in this diff — nothing else.` | `You are a senior software engineer performing a deep code review focused on **diff-introduced regressions**. Your job is to identify real, confirmed bugs and performance issues that were introduced or made worse by the changes in this diff — nothing else. Use the provided diff context bundle as your starting point, and use the available tools to gather additional context if needed.` |
| 252-254 | `## SIGNALS THAT STAGE Da UNDER-COLLECTED\n\nIf you find yourself needing to use \`getImplementation\` or \`runTerminalCmd\` more than once during Stage Db, add a note in the \`description\` of your first issue (or as a sentinel issue with \`issue: "Stage Da context gap"\`) indicating which functions or types were missing from the context bundle. This helps improve the collection stage.` | `## CONTEXT GAPS\n\nIf you find yourself needing to use \`getImplementation\` or \`runTerminalCmd\` more than once, add a note in the \`description\` of your first issue indicating which functions or types were missing from the context bundle. This helps improve future context collection.` |

#### 1.4 [`hindsight/core/prompts/diffContextCollectionProcess.md`](hindsight/core/prompts/diffContextCollectionProcess.md)

| Line | Current Text | New Text |
|------|--------------|----------|
| 1 | `# Stage Da — Context Collection (DiffAnalysis Pipeline)` | `# Diff Context Collection` |

---

### Category 2: Python Files Building Prompts (Sent to LLM - HIGH PRIORITY)

These files build prompts that are sent to the LLM.

#### 2.1 [`hindsight/core/llm/code_analysis.py`](hindsight/core/llm/code_analysis.py)

| Line | Current Text | New Text |
|------|--------------|----------|
| 1000 | `user_prompt += "The following context bundle contains all code needed for your analysis. Line numbers in source fields are original source-file line numbers — use them directly in your output.\n\n"` | `user_prompt += "The following context bundle contains the collected code context for your analysis. Line numbers in source fields are original source-file line numbers — use them directly in your output. If you need additional context, use the available tools.\n\n"` |

#### 2.2 [`hindsight/core/llm/diff_analysis.py`](hindsight/core/llm/diff_analysis.py)

| Line | Current Text | New Text |
|------|--------------|----------|
| 956 | `user_message += "The following diff context bundle contains all code needed for your analysis.\n\n"` | `user_message += "The following diff context bundle contains the collected code context for your analysis. If you need additional context, use the available tools.\n\n"` |

---

### Category 3: Python Files with Internal Comments/Logs (LOW PRIORITY)

These files contain stage references in comments, docstrings, or log messages that are NOT sent to the LLM. These are internal implementation details and can be kept as-is or updated for consistency.

#### 3.1 [`hindsight/core/prompts/prompt_builder.py`](hindsight/core/prompts/prompt_builder.py)

| Line | Type | Current Text | Recommendation |
|------|------|--------------|----------------|
| 892 | docstring | `Build system and user prompts for Stage 4a context collection.` | Optional: Change to `Build system and user prompts for context collection.` |
| 906 | comment | `# Load Stage 4a process prompt` | Optional: Change to `# Load context collection process prompt` |
| 945 | log | `logger.info(f"Built Stage 4a prompts - ...")` | Optional: Change to `logger.info(f"Built context collection prompts - ...")` |
| 961 | docstring | `Build system and user prompts for Stage 4b analysis from a context bundle.` | Optional: Change to `Build system and user prompts for analysis from a context bundle.` |
| 964 | docstring | `context_bundle: Context bundle dict from Stage 4a` | Optional: Change to `context_bundle: Context bundle dict from context collection` |
| 971 | comment | `# Load Stage 4b analysis process prompt` | Optional: Change to `# Load analysis process prompt` |
| 1006 | log | `logger.info(f"Built Stage 4b prompts - ...")` | Optional: Change to `logger.info(f"Built analysis prompts - ...")` |

#### 3.2 [`hindsight/core/llm/code_analysis.py`](hindsight/core/llm/code_analysis.py)

| Line | Type | Current Text | Recommendation |
|------|------|--------------|----------------|
| 565 | comment | `# Stage 4a re-runs and produces a proper bundle.` | Optional: Keep or change to `# Context collection re-runs...` |
| 572 | log | `f"(missing 'primary_function'). Deleting and re-running Stage 4a."` | Optional: Change to `...re-running context collection.` |

#### 3.3 [`hindsight/core/llm/llm.py`](hindsight/core/llm/llm.py)

| Line | Type | Current Text | Recommendation |
|------|------|--------------|----------------|
| 735-739 | docstring | Stage references in deprecated method docstring | Optional: Update for consistency |

#### 3.4 [`hindsight/core/llm/iterative/*.py`](hindsight/core/llm/iterative/)

Multiple files contain stage references in docstrings and comments. These are internal documentation and can be optionally updated for consistency:
- `base_iterative_analyzer.py` (lines 11-15)
- `context_collection_analyzer.py` (lines 2-3, 25-26)
- `code_analysis_analyzer.py` (lines 2-3, 25-26)
- `diff_context_analyzer.py` (lines 2-3, 25-26)
- `diff_analysis_analyzer.py` (lines 2-3, 25-26)
- `__init__.py` (lines 10-14)

---

## Summary of Required Changes

### Must Fix (Sent to LLM)
| File | Issue |
|------|-------|
| `analysisProcess.md` | Stage 4b references, "contains all the code you need" |
| `contextCollectionProcess.md` | Stage 4a reference |
| `diffAnalysisProcess.md` | Stage Db references, "contains all the code you need" |
| `diffContextCollectionProcess.md` | Stage Da reference |
| `code_analysis.py` (line 1000) | "contains all code needed" |
| `diff_analysis.py` (line 956) | "contains all code needed" |

### Optional (Internal Comments/Logs)
| File | Issue |
|------|-------|
| `prompt_builder.py` | Stage references in docstrings, comments, logs |
| `code_analysis.py` | Stage references in comments, logs |
| `llm.py` | Stage references in deprecated method docstring |
| `iterative/*.py` | Stage references in docstrings |

---

## Key Principle

Each LLM call operates in a completely separate context window with no memory of previous steps. Stage numbers are internal implementation details that should not appear in prompts sent to the LLM.

---

## Testing Plan

1. Run the code analyzer on a sample project
2. Verify the logged prompts no longer contain stage references
3. Verify the logged prompts no longer claim the context bundle contains "all" code
4. Verify the LLM still produces valid analysis output
5. Monitor tool usage to ensure it doesn't increase excessively

---

## Implementation Order

1. **Phase 1 (Required)**: Modify prompt files sent to LLM
   - `analysisProcess.md`
   - `contextCollectionProcess.md`
   - `diffAnalysisProcess.md`
   - `diffContextCollectionProcess.md`
   - `code_analysis.py` (line 1000)
   - `diff_analysis.py` (line 956)

2. **Phase 2 (Optional)**: Update internal comments/logs for consistency
   - `prompt_builder.py`
   - `code_analysis.py` (comments/logs)
   - `llm.py`
   - `iterative/*.py`

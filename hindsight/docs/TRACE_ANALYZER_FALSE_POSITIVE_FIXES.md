# Trace Analyzer False Positive Fixes

## Background

The trace analyzer generated an invalid bug report for `CLMotionNotifier.h:SiloDispatcher::dispatchData`. It flagged two `memcpy` operations (stack copy + block capture) as "redundant" and proposed eliminating one via `__block` storage or capturing from `self`. Both suggestions are incorrect: the stack copy exists because the source `void*` is invalidated after the synchronous call returns, and capturing `this`/`self` would introduce use-after-free since the dispatcher can be deleted while the block is enqueued. The comments directly above the function explain this design.

This document describes four changes to prevent this class of false positive.

---

## 1. Collect Safety/Design Comments Above Functions in Stage A

### Problem

Stage A context collection (`traceContextCollectionProcess.md`) instructs the LLM to collect "full verbatim source code" of the leaf function. In practice, it collects only the function body. The 30-line block comment (lines 177-209 of `CLMotionNotifier.h`) explaining the lifetime safety contract was not included in the context bundle passed to Stage B. Without this comment, Stage B had no signal that the copy pattern was intentional and safety-critical.

### Proposed Change

In `traceContextCollectionProcess.md`, add to the **COLLECTION STRATEGY FOR CALLSTACKS** section, after priority item 1 (Leaf function):

```markdown
   **Include preceding documentation comments**: When collecting a function's source,
   also include any comment block (block comment or consecutive line comments)
   immediately preceding the function definition or its enclosing class/struct.
   These comments often explain design constraints, lifetime invariants, or
   intentional performance trade-offs that are essential for correct analysis.
```

Additionally, in the code navigation tool guidance, add a note:

```markdown
When using `get_function_body` or `getFileContentByLines`, extend the start line
upward to include any contiguous comment block that immediately precedes the
function or its containing type declaration. A safe heuristic: read 40 lines
above the function start and trim to the first blank line or non-comment code.
```

### Files to Modify

- `hindsight/core/prompts/traceContextCollectionProcess.md` (prompt text)
- Optionally: `hindsight/core/mcp_tools/analysis_server.py` (`get_function_body` tool) to return preceding comments by default

---

## 2. Refine "Copying Where Reference Would Suffice" Guidance

### Problem

The Stage B prompt (`traceAnalysisProcess.md`) includes this in the Question A checklist:

> Inefficient data passing between layers (e.g., copying where a reference would suffice)

This generic guidance directly triggers false positives for patterns where copies are a **correctness requirement**, not a performance choice. Common cases:

- Copying stack data into a block/closure dispatched asynchronously (source is invalidated)
- Copying into a message sent across thread boundaries (no shared ownership)
- Copying trivially-copyable POD data where the copy cost is negligible vs. the indirection cost of a pointer

The LLM sees "copy" + "could use reference" and flags it without evaluating whether the reference would remain valid.

### Proposed Change

Replace the bullet in `traceAnalysisProcess.md` Question A:

**Before:**
```markdown
- Inefficient data passing between layers (e.g., copying where a reference would suffice)
```

**After:**
```markdown
- Inefficient data passing between layers (e.g., large allocations copied repeatedly
  where move semantics or a shared buffer would work). NOTE: Do NOT flag copies that
  exist for lifetime safety — e.g., copying stack data into a block/closure for async
  dispatch, or copying into a message for cross-thread delivery. If a comment or the
  surrounding code indicates the copy prevents use-after-free or dangling references,
  it is intentional and must not be reported as an issue.
```

Add to the **WHAT NOT TO REPORT** section:

```markdown
- Copies of stack-local data into block/closure captures when the block is dispatched
  asynchronously — the copy is required because the stack frame is gone by the time
  the block executes. This applies to ObjC blocks, C++ lambdas, Swift closures, and
  dispatch_async patterns. Even if "two copies" occur (one into a local, one into the
  capture), this is the minimum required by the language runtime and is not optimizable
  without changing the dispatch architecture.
```

### Files to Modify

- `hindsight/core/prompts/traceAnalysisProcess.md`

---

## 3. Remove the < 0.5% Cost Self-Check Rule

### Problem

The Stage B prompt contains this self-check instruction:

> 4. **Measurable impact**: The impact is more than negligible (> 0.5% cost contribution or measurable latency improvement)

However, the cost/percentage data is deliberately stripped from the LLM input by the prompt builder (line 748 of `trace_analysis_prompt_builder.py`: "NOTE: Cost and normalized cost information is excluded from LLM input"). The LLM therefore cannot evaluate this criterion. It either:

1. Ignores the rule (making it dead weight in the prompt), or
2. Hallucinates a cost estimate and applies the rule incorrectly

In this case the trace had 0.464% normalized cost (below 0.5%) but the LLM had no way to know that. The rule creates a false sense of rigor without providing the data needed to apply it.

### Proposed Change

Remove the cost-based self-check from `traceAnalysisProcess.md`. Replace with a qualitative threshold that the LLM can evaluate without numeric cost data:

**Before:**
```markdown
4. **Measurable impact**: The impact is more than negligible (> 0.5% cost contribution or measurable latency improvement)
```

**After:**
```markdown
4. **Measurable impact**: The optimization must yield a meaningful improvement given
   the function's role. Ask: "If I implemented this fix, would a profiler trace show
   a visible difference?" If the answer is "probably not" or "only in synthetic
   micro-benchmarks," remove the issue.
```

### Files to Modify

- `hindsight/core/prompts/traceAnalysisProcess.md`

---

## 4. Add Solution Correctness Validation Stage

### Problem

The current pipeline is:

```
Stage A (collect context) -> Stage B (find issues) -> Relevance Filter -> Report
```

The relevance filter (`traceRelevanceFilterPrompt.md`) checks whether an issue relates to the traced execution path. The challenger (`traceResponseChallenger.md`) checks whether the issue is based on real trace evidence. Neither asks the critical question: **"Is the proposed solution technically correct and safe to implement?"**

This is the primary reason the invalid report survived: the issue IS related to `dispatchData` (passes relevance), and there IS a real copy in the code (passes challenger). But the proposed fix (`__block` storage, capture from `self`) would either not reduce copies or introduce use-after-free.

### Proposed Change

Add a **Stage C: Solution Validation** step after Stage B produces issues but before publishing results. This runs in a fresh context window with only the issue + relevant source code.

Create a new prompt file `traceSolutionValidator.md`:

```markdown
# Stage C - Solution Correctness Validation

## ROLE

You are a senior systems programmer validating whether a proposed optimization
is technically correct. You are given an issue report with a proposed solution
and the relevant source code. Your job is to determine if the solution is
SAFE to implement.

## VALIDATION CRITERIA

For each proposed solution, answer:

1. **Preserves correctness**: Does the fix maintain all existing invariants?
   - Thread safety (no new data races)
   - Lifetime safety (no use-after-free, no dangling references)
   - Memory safety (no leaks, no double-free)
   - Behavioral equivalence (same observable output)

2. **Actually achieves the goal**: Does the fix actually reduce the claimed
   overhead, or does it merely move the cost elsewhere?
   - "Use __block" still heap-allocates on block copy
   - "Capture self" may add retain/release overhead
   - "Use a reference" may dangle if the referent is freed

3. **Language semantics are respected**: Does the fix work within the rules
   of the language (ObjC block capture semantics, C++ value categories,
   Swift closure capture lists)?

## OUTPUT

Return ONLY a JSON object:

{"valid": true/false, "reason": "explanation"}

Return {"valid": false, ...} if the solution would introduce a bug, does not
achieve its claimed benefit, or misunderstands language semantics.
```

Integrate this in `trace_code_analysis.py` between `run_analysis_from_context` (Stage B) and `_save_result`:

```python
# After Stage B returns issues, validate each solution
validated_issues = []
for issue in issues:
    if self._validate_solution(issue, context_bundle):
        validated_issues.append(issue)
issues = validated_issues
```

### Files to Modify

- New file: `hindsight/core/prompts/traceSolutionValidator.md`
- `hindsight/core/trace_util/trace_code_analysis.py` (add `_validate_solution` method, call between Stage B and save)
- `hindsight/core/llm/iterative/` (optionally add `trace_solution_validator.py` if using the iterative framework)

### Cost Consideration

This adds one LLM call per issue (not per trace). Most traces produce 0-3 issues, so the additional cost is small relative to the two-stage pipeline. Issues that fail validation are dropped silently (logged at INFO level). This avoids wasting engineer time on incorrect suggestions while keeping the overall pipeline latency acceptable.

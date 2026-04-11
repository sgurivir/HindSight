# Call Tree Bug Investigation Report

## Issue Summary

The call tree section is not being sent along with code being analyzed in the LLM prompts. The user expects a `// CALL TREE` block to appear after the `// Code to ANALYZE` block.

## Root Cause

**Bug Location:** [`prompt_builder.py`](../core/prompts/prompt_builder.py) - Line 363

**The Bug:**
```python
# Line 361
pruned_code = CodeContextPruner.prune_comments_simple(code_content)

# Line 363 - BUG: pruned_lines is UNDEFINED!
pruned_first_line = pruned_lines[0] if pruned_lines else ""
```

The variable `pruned_lines` is referenced on line 363 but was **never defined**. The code defines `pruned_code` on line 361, but then incorrectly tries to use `pruned_lines` on line 363. This causes a `NameError` exception.

### Why This Breaks Call Tree Generation

1. The `_convert_json_to_comment_format()` method is responsible for:
   - Converting JSON content to comment-based format
   - Adding the call tree section (lines 585-614)

2. When the `NameError` occurs at line 363, the exception is caught by the broad `except (json.JSONDecodeError, Exception) as e:` block at line 618

3. The exception handler:
   - Logs the error at **debug level** (not visible in normal INFO logs)
   - Returns the original JSON content as a fallback

4. This means:
   - The call tree generation code at lines 585-614 is **never reached**
   - The "Call tree generation check" log message at line 586 **never appears**
   - The prompt contains raw JSON instead of formatted content with call tree

## The Fix Required

Change line 363 from:
```python
pruned_first_line = pruned_lines[0] if pruned_lines else ""
```

To:
```python
pruned_lines = pruned_code.split('\n')
pruned_first_line = pruned_lines[0] if pruned_lines else ""
```

---

## Example: Current Prompt (WITHOUT Call Tree - Broken)

This is what the prompt currently looks like (from `conversation_20.md`):

```
## Code Analysis Task

Analyze the primary function provided below. Use any contextual summaries, invoking functions, or additional context for understanding only - report issues exclusively in the primary function.

// Code to ANALYZE

{
  "function": "CompletionUIPresentationController::setContentView:contentSize:includeTopContentInset:includeBottomContentInset:animated:",
  "code": " 296 | - (void)setContentView:(NSView *)contentView contentSize:(CGSize)contentSize includeTopContentInset:(BOOL)includeTopContentInset includeBottomContentInset:(BOOL)includeBottomContentInset animated:(BOOL)animated
 297 | {
 298 | #if ENABLE_FIELD_IN_COMPLETION_WINDOW
 ...
 343 | }",
  "context": {
    "file": "Mac/Safari/Autocomplete/CompletionUIPresentationController.mm",
    "start": 296,
    "end": 343,
    ...
  },
  "functions_invoked": [...]
}

## Output Requirements
...
```

**Notice:** The code is sent as raw JSON, and there is NO call tree section.

---

## Example: Expected Prompt (WITH Call Tree - Fixed)

After fixing the bug, the prompt should look like this:

```
## Code Analysis Task

Analyze the primary function provided below. Use any contextual summaries, invoking functions, or additional context for understanding only - report issues exclusively in the primary function.

// Code to ANALYZE

// Function - CompletionUIPresentationController::setContentView:contentSize:includeTopContentInset:includeBottomContentInset:animated:
// file : Mac/Safari/Autocomplete/CompletionUIPresentationController.mm

 296 | - (void)setContentView:(NSView *)contentView contentSize:(CGSize)contentSize includeTopContentInset:(BOOL)includeTopContentInset includeBottomContentInset:(BOOL)includeBottomContentInset animated:(BOOL)animated
 297 | {
 298 | #if ENABLE_FIELD_IN_COMPLETION_WINDOW
 299 |     if (_completionWindowHasUnifiedField && (includeTopContentInset != _includeTopContentInset || includeBottomContentInset != _includeBottomContentInset)) {
 300 |         _includeTopContentInset = includeTopContentInset;
 301 |         _includeBottomContentInset = includeBottomContentInset;
 302 |         [self _updateInsetConstraints];
 303 |     }
 304 | #endif
 305 | 
 306 |     if (contentView == _contentView)
 307 |         return;
 308 | 
 309 |     if (!animated) {
 310 |         [self _setContentViewWithoutAnimation:contentView contentSize:contentSize includeTopContentInset:includeTopContentInset includeBottomContentInset:includeBottomContentInset];
 311 |         return;
 312 |     }
 313 | 
 314 |     [self _createCompletionWindowIfNeeded];
 315 | 
 316 |     contentView.hidden = YES;
 317 |     contentView.alphaValue = 0;
 318 |     contentView.translatesAutoresizingMaskIntoConstraints = NO;
 319 | 
 320 |     [_completionWindow.contentView addSubview:contentView];
 321 | 
 322 |     NSView *oldContentView = _contentView;
 323 |     _contentView = contentView;
 324 | 
 325 |     [NSAnimationContext runAnimationGroup:^(NSAnimationContext *fadeOutContext) {
 326 |         fadeOutContext.duration = contentViewFadeOutAnimationDuration;
 327 | #ifndef NDEBUG
 328 |         if ([NSEvent modifierFlags] & NSEventModifierFlagShift)
 329 |             fadeOutContext.duration *= debugAnimationTimeDilationFactor;
 330 | #endif
 331 |         fadeOutContext.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionDefault];
 332 |         [oldContentView.animator setAlphaValue:0];
 333 |     } completionHandler:^{
 334 |         [oldContentView removeFromSuperview];
 335 |         [self _resetConstraintsWithContentView:oldContentView];
 336 |     }];
 337 | 
 338 |     dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(contentWindowResizeAnimationDelay * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
 339 |         [self _startWindowResizeAnimationWithContentSize:contentSize includeTopContentInset:includeTopContentInset includeBottomContentInset:includeBottomContentInset];
 340 |     });
 341 | }

// == Additional context when analyzing above function
// == CompletionUIPresentationController::setContentView:contentSize:includeTopContentInset:includeBottomContentInset:animated:() invokes the following function(s)

//====================================================

// Function - _createCompletionWindowIfNeeded
// file : Mac/Safari/Autocomplete/CompletionUIPresentationController.mm

 398 | - (void)_createCompletionWindowIfNeeded
 399 | {
 400 |     if (_completionWindow)
 401 |         return;
 402 | 
 403 |     _completionWindow = [[self.class.completionWindowClass alloc] initWithContentRect:CGRectZero styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
 ...
 416 | }

//====================================================

// Function - _setContentViewWithoutAnimation:contentSize:includeTopContentInset:includeBottomContentInset:
// file : Mac/Safari/Autocomplete/CompletionUIPresentationController.mm

 345 | - (void)_setContentViewWithoutAnimation:(NSView *)contentView contentSize:(NSSize)contentSize includeTopContentInset:(BOOL)includeTopContentInset includeBottomContentInset:(BOOL)includeBottomContentInset
 346 | {
 ...
 369 | }

//====================================================

// Function - _startWindowResizeAnimationWithContentSize:includeTopContentInset:includeBottomContentInset:
// file : Mac/Safari/Autocomplete/CompletionUIPresentationController.mm

 252 | - (void)_startWindowResizeAnimationWithContentSize:(CGSize)contentSize includeTopContentInset:(BOOL)includeTopContentInset includeBottomContentInset:(BOOL)includeBottomContentInset
 253 | {
 ...
 274 | }

// === CALL TREE CONTEXT ===
// This shows the function's position in the call hierarchy
// Use getFileContentByLines tool to read code at any location shown below
//
// CALLERS (who calls this function):
//     BrowserWindowController::showCompletionUI()  {Mac/Safari/BrowserWindowController.mm:1200-1250}
//         └── [TARGET] CompletionUIPresentationController::setContentView:contentSize:includeTopContentInset:includeBottomContentInset:animated:()
//
// CALLEES (what this function calls):
// [TARGET] CompletionUIPresentationController::setContentView:contentSize:includeTopContentInset:includeBottomContentInset:animated:()  {Mac/Safari/Autocomplete/CompletionUIPresentationController.mm:296-343}
// ├── _createCompletionWindowIfNeeded()  {Mac/Safari/Autocomplete/CompletionUIPresentationController.mm:398-416}
// ├── _setContentViewWithoutAnimation:contentSize:includeTopContentInset:includeBottomContentInset:()  {Mac/Safari/Autocomplete/CompletionUIPresentationController.mm:345-369}
// │   └── _createCompletionWindowIfNeeded()  {Mac/Safari/Autocomplete/CompletionUIPresentationController.mm:398-416}
// ├── _startWindowResizeAnimationWithContentSize:includeTopContentInset:includeBottomContentInset:()  {Mac/Safari/Autocomplete/CompletionUIPresentationController.mm:252-274}
// │   ├── _updateWindowSizeAndPositionDuringAnimation()  {Mac/Safari/Autocomplete/CompletionUIPresentationController.mm:180-200}
// │   └── _startContentViewFadeInAnimation()  {Mac/Safari/Autocomplete/CompletionUIPresentationController.mm:210-240}
// └── _resetConstraintsWithContentView:()  {Mac/Safari/Autocomplete/CompletionUIPresentationController.mm:420-440}

## Output Requirements
...
```

---

## Key Differences

| Aspect | Current (Broken) | Expected (Fixed) |
|--------|------------------|------------------|
| Code Format | Raw JSON | Comment-based with line numbers |
| Call Tree | **MISSING** | Present after code section |
| Invoked Functions | In JSON structure | Formatted as separate code blocks |
| File Paths | In JSON | As comments above code |

## Evidence from Logs

The log file shows:
- `prompt_builder.py:717` - "Extracted file path from JSON" ✓ (appears)
- `prompt_builder.py:755` - "No summary context to insert" ✓ (appears)
- `prompt_builder.py:586` - "Call tree generation check" ✗ (NEVER appears)

This confirms the code crashes before reaching the call tree generation block.

## Configuration

The call tree feature is properly configured:
- `CALL_TREE_ENABLED = True` in [`constants.py`](../core/constants.py)
- `CALL_TREE_MAX_ANCESTOR_DEPTH = 1`
- `CALL_TREE_MAX_DESCENDANT_DEPTH = 3`
- `CALL_TREE_MAX_CHILDREN_PER_NODE = 5`
- `CALL_TREE_MAX_TOKENS = 2000`

The `merged_call_graph.json` is being loaded successfully (confirmed in logs).

## Files Involved

1. [`hindsight/core/prompts/prompt_builder.py`](../core/prompts/prompt_builder.py) - Contains the bug at line 363
2. [`hindsight/core/lang_util/call_tree_section_generator.py`](../core/lang_util/call_tree_section_generator.py) - Generates the call tree section
3. [`hindsight/core/constants.py`](../core/constants.py) - Contains call tree configuration constants

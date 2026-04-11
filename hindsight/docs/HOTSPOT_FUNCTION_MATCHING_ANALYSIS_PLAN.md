# Hotspot Function Matching Analysis Plan

## Executive Summary

Analysis of the hotspot function matching results from `~/Desktop/log.txt` reveals that **30.6% of trace functions (2172 out of 7101)** are not being matched to AST-generated functions in `merged_functions.json`. This document categorizes the patterns of missing matches, identifies root causes (bug vs fundamental limitation), and proposes loose pattern matching strategies to improve coverage.

## Current Matching Statistics

| Metric | Count | Percentage |
|--------|-------|------------|
| Total hotspot functions | 7,101 | 100% |
| Matched functions | 4,929 | 69.4% |
| Unmatched functions | 2,172 | 30.6% |

## Pattern Categories of Unmatched Functions

### Category 1: C++ Template Instantiations (FUNDAMENTAL LIMITATION)

**Pattern Description:**
Template instantiations in traces contain fully specialized type information that doesn't exist in source AST.

**Examples from log:**
```
std::__1::shared_ptr<CLConnectionMessage> std::__1::allocate_shared[abi:ne200100]<CLConnectionMessage, std::__1::allocator<CLConnectionMessage>>()
std::__1::__shared_ptr_emplace<CLConnectionMessage, std::__1::allocator<CLConnectionMessage>>::__shared_ptr_emplace[abi:ne200100]<>()
std::__1::construct_at[abi:ne200100]<CLConnectionMessage, CLConnectionMessage>()
std::__1::function<void (CLDaemonStatus const&)>::operator()(CLDaemonStatus const&) const
std::__1::__function::__func<CLDaemonStatus::registerForDaemonStatusNotifications(...)
```

**Root Cause:** FUNDAMENTAL LIMITATION
- Templates are instantiated at compile time with specific types
- Source AST only contains template definitions, not instantiations
- The `[abi:ne200100]` suffix indicates ABI-specific mangling
- STL templates (`std::__1::`) are from system headers, not analyzed repo

**Impact:** HIGH - This is the most common category of unmatched functions

**Recommendation:**
- Do NOT attempt to match STL template instantiations
- Filter out functions with `std::__1::` prefix during aggregation
- Consider adding `std::` to the library filter exclusion list

---

### Category 2: Lambda Functions and Blocks (FUNDAMENTAL LIMITATION)

**Pattern Description:**
Compiler-generated closures, blocks, and thunks have synthesized names that don't correspond to source code.

**Examples from log:**
```
invocation function for block in CLTilesManager::updateTileLocationRelevancy(...)
closure #1 in CLAONSenseKappaConfigService.init(...)
thunk for @escaping @callee_unowned @convention(block)...
partial apply for closure #1 in CLAONSenseKappaConfigService.init(...)
```

**Root Cause:** FUNDAMENTAL LIMITATION
- Blocks and closures are anonymous by nature
- Compiler generates unique names at compile time
- Swift closures use `closure #N` naming convention
- Objective-C blocks use `invocation function for block in` prefix

**Impact:** MEDIUM - Common in modern codebases using functional patterns

**Recommendation:**
- Extract the containing function name from block/closure names
- Map `invocation function for block in X::method()` → `X::method()`
- Map `closure #N in X.method()` → `X.method()`
- This provides partial attribution to the parent function

---

### Category 3: Objective-C Method Naming Convention (POTENTIAL BUG)

**Pattern Description:**
Objective-C methods in traces use bracket notation while AST may use different representation.

**Examples from log:**
```
-[CLContextManagerWaterSubmersion sourceUpdated:]
-[CLFenceHandoffConnectionManager sendMessage:]
-[CLDurianSettings objectForKey:defaultValue:nilOrTypeOfClass:]
+[CLLocationManager _shouldDisplayHeadingCalibration]
```

**Root Cause:** POTENTIAL BUG in matching logic
- Trace format: `-[ClassName methodName:]` or `+[ClassName methodName:]`
- AST format may be: `ClassName::methodName:` or just `methodName:`
- The `extract_keywords_from_function_name()` function in [`hotspot_function_aggregator.py`](dev/hotspots/hotspot_function_aggregator.py:575) handles Objective-C parsing but may have edge cases

**Impact:** MEDIUM - Affects all Objective-C code

**Investigation Needed:**
1. Check how [`CASTUtil.format_function_name()`](hindsight/core/lang_util/cast_util.py:557) formats Objective-C methods
2. Verify if `OBJC_INSTANCE_METHOD_DECL` and `OBJC_CLASS_METHOD_DECL` are being captured
3. Compare trace format vs AST format for the same method

**Recommendation:**
- Normalize both trace and AST function names to a common format
- Strip `-[` and `+[` prefixes and `]` suffix from trace names
- Ensure AST extraction includes the class name prefix

---

### Category 4: Bounce/Callback Functions (POTENTIAL BUG)

**Pattern Description:**
Static member function callbacks with `_bounce` suffix appear in traces but may not be in AST.

**Examples from log:**
```
CLMotionActivitySubscription::onMotionActivityNotification_bounce(...)
CLStreamingAwareLocationProvider::onStepCountNotification_bounce(...)
CLDaemonStatus::onPowerNotification_bounce(...)
CLLocationManager::onLocationNotification_bounce(...)
```

**Root Cause:** POTENTIAL BUG or NAMING CONVENTION
- These may be generated trampolines or static callback wrappers
- The `_bounce` suffix suggests a pattern for C-style callback registration
- AST may have the function without `_bounce` suffix

**Impact:** LOW-MEDIUM - Specific to callback-heavy code

**Investigation Needed:**
1. Search for `_bounce` pattern in source code
2. Determine if these are macros, templates, or actual function definitions
3. Check if AST is capturing these definitions

**Recommendation:**
- Add loose matching: try matching without `_bounce` suffix
- If `X::method_bounce()` not found, try matching `X::method()`

---

### Category 5: Destructor/Constructor Variants (POTENTIAL BUG)

**Pattern Description:**
Destructors and constructors may have different mangled representations.

**Examples from log:**
```
CLSqliteFinalizingStatement::~CLSqliteFinalizingStatement()
CLAutoOSTransaction::~CLAutoOSTransaction()
CLLocationManagerRoutine::~CLLocationManagerRoutine()
```

**Root Cause:** POTENTIAL BUG in name normalization
- Destructors use `~ClassName()` syntax
- AST should capture these via `CXX_DESTRUCTOR` cursor kind
- Matching may fail due to parameter list differences

**Impact:** LOW - Destructors are typically simple

**Investigation Needed:**
1. Verify `CXX_DESTRUCTOR` is in `ALLOWED_FUNCTION_KINDS` (confirmed in [`cast_util.py:29`](hindsight/core/lang_util/cast_util.py:29))
2. Check if destructor names are being formatted consistently

**Recommendation:**
- Ensure destructor matching ignores parameter lists
- `~ClassName()` should match `~ClassName(void)` or `~ClassName()`

---

### Category 6: External Library Functions (FUNDAMENTAL LIMITATION)

**Pattern Description:**
Functions from frameworks not in the analyzed repository.

**Examples from log:**
```
CMMotionFeaturesWatch::getPercentile(...)
CMFourierTransformGeneric::forward(...)
CMIirFilter::update(...)
CMMotionFeaturesWatch::getMedian(...)
```

**Root Cause:** FUNDAMENTAL LIMITATION
- These are from CoreMotion framework (`CM` prefix)
- The analyzed repo is CoreLocation, not CoreMotion
- Library filter includes `CoreMotion` but source isn't available

**Impact:** MEDIUM - Depends on framework dependencies

**Recommendation:**
- Accept that external framework functions won't match
- Consider removing `CoreMotion` from filter if source isn't available
- Document which libraries have source vs which are binary-only

---

### Category 7: Protocol Buffer Generated Code (FUNDAMENTAL LIMITATION)

**Pattern Description:**
Auto-generated protobuf code has synthesized function names.

**Examples from log:**
```
proto::gpsd::Indication::MergePartialFromCodedStream(...)
CLP::LogEntry::PrivateData::MeasurementReportCallbackContents::~MeasurementReportCallbackContents()
google::protobuf::internal::RepeatedPtrFieldBase::Reserve(...)
```

**Root Cause:** FUNDAMENTAL LIMITATION
- Protobuf generates C++ code from `.proto` files
- Generated code may be in build directory (excluded)
- `google::protobuf::` namespace is external library

**Impact:** LOW-MEDIUM - Depends on protobuf usage

**Recommendation:**
- Include generated protobuf directories in analysis if available
- Filter out `google::protobuf::` namespace as external
- Consider analyzing `.proto` files separately

---

### Category 8: Swift Interop and Compiler-Generated (FUNDAMENTAL LIMITATION)

**Pattern Description:**
Swift-specific constructs and compiler-generated code.

**Examples from log:**
```
<compiler-generated>
thunk for @escaping @callee_unowned @convention(block)...
outlined init with copy of CLAONSenseKappaConfigService.State
```

**Root Cause:** FUNDAMENTAL LIMITATION
- Swift compiler generates thunks for Objective-C interop
- `<compiler-generated>` indicates synthesized code
- `outlined` functions are compiler optimizations

**Impact:** LOW - Typically not performance-critical

**Recommendation:**
- Filter out `<compiler-generated>` entries
- Filter out functions starting with `outlined`
- Map thunks to their underlying Swift functions if possible

---

## Root Cause Summary

| Category | Root Cause | Fixable? | Priority |
|----------|------------|----------|----------|
| C++ Template Instantiations | Fundamental Limitation | No | Filter out |
| Lambda/Blocks | Fundamental Limitation | Partial | Map to parent |
| Objective-C Methods | Potential Bug | Yes | High |
| Bounce Callbacks | Potential Bug | Yes | Medium |
| Destructors | Potential Bug | Yes | Low |
| External Libraries | Fundamental Limitation | No | Filter out |
| Protobuf Generated | Fundamental Limitation | Partial | Include gen dirs |
| Swift Interop | Fundamental Limitation | No | Filter out |

---

## Loose Pattern Matching Strategies

### Strategy 1: Keyword-Based Matching (Current Implementation)

The current implementation in [`find_best_matching_function()`](dev/hotspots/hotspot_function_aggregator.py:829) uses keyword extraction and matching:

```python
def find_best_matching_function(hotspot_function_name, hotspot_file_name, ...):
    # 1. Filter by file name - get all functions in same file
    candidate_functions = file_to_functions.get(hotspot_file_name, [])
    
    # 2. Extract keywords from hotspot function name
    hotspot_keywords = set(extract_keywords_from_function_name(...))
    
    # 3. Count keyword matches for each candidate
    for func_name in candidate_functions:
        func_keywords = set(func_data.get("keywords", []))
        match_count = len(hotspot_keywords & func_keywords)
```

**Limitation:** Requires exact file name match first, which fails for:
- Functions in headers vs implementation files
- Inlined functions
- Template instantiations

### Strategy 2: Fuzzy Class/Method Name Matching (PROPOSED)

**Algorithm:**
1. Extract class name and method name from trace function
2. Search AST for functions with matching class AND method name
3. Score by parameter type similarity

**Implementation Sketch:**
```python
def extract_class_method(function_name):
    """Extract (class_name, method_name) from various formats."""
    # Handle C++: ClassName::methodName(...)
    if '::' in function_name:
        parts = function_name.split('::')
        return (parts[-2], parts[-1].split('(')[0])
    
    # Handle Objective-C: -[ClassName methodName:]
    if function_name.startswith('-[') or function_name.startswith('+['):
        match = re.match(r'[-+]\[(\w+)\s+(\w+)', function_name)
        if match:
            return (match.group(1), match.group(2))
    
    return (None, function_name.split('(')[0])

def fuzzy_match_function(trace_func, ast_functions):
    trace_class, trace_method = extract_class_method(trace_func)
    
    candidates = []
    for ast_func in ast_functions:
        ast_class, ast_method = extract_class_method(ast_func)
        
        # Exact method name match
        if trace_method == ast_method:
            # Bonus for class name match
            score = 100 if trace_class == ast_class else 50
            candidates.append((ast_func, score))
    
    return max(candidates, key=lambda x: x[1]) if candidates else None
```

### Strategy 3: Suffix Stripping (PROPOSED)

**Algorithm:**
Remove common suffixes and try matching:
- `_bounce` → try without suffix
- `[abi:...]` → strip ABI tags
- Template parameters → strip `<...>`

**Implementation Sketch:**
```python
def normalize_function_name(name):
    """Normalize function name for loose matching."""
    # Strip ABI tags
    name = re.sub(r'\[abi:\w+\]', '', name)
    
    # Strip template parameters
    name = strip_template_params(name)
    
    # Strip _bounce suffix
    if name.endswith('_bounce'):
        name = name[:-7]
    
    # Normalize whitespace
    name = ' '.join(name.split())
    
    return name
```

### Strategy 4: Parent Function Attribution (PROPOSED)

**Algorithm:**
For blocks/closures, attribute cost to parent function.

**Implementation Sketch:**
```python
def extract_parent_function(closure_name):
    """Extract parent function from closure/block name."""
    # Handle: "invocation function for block in X::method(...)"
    match = re.search(r'block in (\S+::\S+)\(', closure_name)
    if match:
        return match.group(1)
    
    # Handle: "closure #N in X.method(...)"
    match = re.search(r'closure #\d+ in (\S+\.\S+)\(', closure_name)
    if match:
        return match.group(1).replace('.', '::')
    
    return None
```

### Strategy 5: File-Independent Matching (PROPOSED)

**Algorithm:**
When file-based matching fails, search across all files.

**Implementation Sketch:**
```python
def find_function_any_file(function_name, func_impl_dict, filter_libraries):
    """Search for function across all files."""
    keywords = extract_keywords_from_function_name(function_name, filter_libraries)
    
    best_match = None
    best_score = 0
    
    for ast_func, data in func_impl_dict.items():
        ast_keywords = set(data.get("keywords", []))
        score = len(set(keywords) & ast_keywords)
        
        # Require minimum keyword overlap
        if score >= 2 and score > best_score:
            best_match = ast_func
            best_score = score
    
    return best_match if best_score >= 2 else None
```

---

## Recommended Implementation Priority

### Phase 1: Quick Wins (Bug Fixes)

1. **Fix Objective-C method matching**
   - Normalize `-[Class method:]` format in both trace and AST
   - Ensure class name is included in AST function names

2. **Add suffix stripping**
   - Strip `_bounce` suffix before matching
   - Strip ABI tags `[abi:...]`

3. **Improve destructor matching**
   - Ignore parameter list for destructors
   - Match `~Class()` to `~Class(void)`

### Phase 2: Enhanced Matching

4. **Implement parent function attribution**
   - Map blocks/closures to parent functions
   - Aggregate costs to parent

5. **Add file-independent fallback**
   - When file-based matching fails, search all files
   - Require minimum keyword overlap (2+)

### Phase 3: Filtering Improvements

6. **Filter out unmatchable patterns**
   - `std::__1::` (STL templates)
   - `google::protobuf::` (external library)
   - `<compiler-generated>`
   - Functions starting with `outlined`

7. **Update library filters**
   - Remove `CoreMotion` if source not available
   - Add explicit external library exclusion list

---

## Metrics for Success

After implementing the recommended changes, target metrics:

| Metric | Current | Target |
|--------|---------|--------|
| Match rate | 69.4% | 85%+ |
| False positives | Unknown | <5% |
| Unmatched (fundamental) | ~20% | ~15% |
| Unmatched (fixable) | ~10% | <5% |

---

## Appendix: Code References

### Key Files

| File | Purpose |
|------|---------|
| [`dev/hotspots/hotspot_function_aggregator.py`](dev/hotspots/hotspot_function_aggregator.py) | Hotspot aggregation and matching |
| [`hindsight/core/lang_util/cast_util.py`](hindsight/core/lang_util/cast_util.py) | C/C++/Objective-C AST extraction |
| [`hindsight/core/lang_util/swift_ast_util.py`](hindsight/core/lang_util/swift_ast_util.py) | Swift AST extraction |
| [`hindsight/core/lang_util/ast_util.py`](hindsight/core/lang_util/ast_util.py) | Unified AST orchestration |
| [`hindsight/core/lang_util/ast_merger.py`](hindsight/core/lang_util/ast_merger.py) | Merging AST from multiple languages |

### Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `find_best_matching_function()` | [`hotspot_function_aggregator.py:829`](dev/hotspots/hotspot_function_aggregator.py:829) | Main matching logic |
| `extract_keywords_from_function_name()` | [`hotspot_function_aggregator.py:575`](dev/hotspots/hotspot_function_aggregator.py:575) | Keyword extraction |
| `CASTUtil.format_function_name()` | [`cast_util.py:557`](hindsight/core/lang_util/cast_util.py:557) | AST function name formatting |
| `CASTUtil.get_fully_qualified_name()` | [`cast_util.py:503`](hindsight/core/lang_util/cast_util.py:503) | Qualified name generation |

---

## Conclusion

The 30.6% unmatched function rate is primarily due to fundamental limitations (template instantiations, compiler-generated code, external libraries) rather than bugs. However, there are several opportunities to improve matching:

1. **Bug fixes** for Objective-C methods, bounce callbacks, and destructors could recover ~5-10% of unmatched functions
2. **Loose matching strategies** (parent attribution, file-independent search) could recover another ~5%
3. **Better filtering** of unmatchable patterns would improve the accuracy of the match rate metric

The recommended approach is to implement Phase 1 (bug fixes) first, measure the improvement, then proceed with Phase 2 and 3 based on results.

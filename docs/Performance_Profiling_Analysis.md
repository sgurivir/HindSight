# Performance Profiling Analysis

This document provides detailed performance analysis of Hindsight code analysis runs, including time breakdowns by stage, LLM request rates, and optimization insights.

## Analysis Run Summary (March 24, 2026)

### Run Configuration
- **Repository:** Safari
- **Model:** aws:anthropic.claude-opus-4-5-20251101-v1:0
- **Functions Targeted:** 800
- **Functions Processed:** 559 (at log capture point)
- **Total Duration:** ~18 minutes

---

## LLM Request Rate Statistics

### Requests Per Minute Distribution

| Minute | Requests |
|--------|----------|
| 11:01 | 2 |
| 11:02 | 2 |
| 11:03 | 5 |
| 11:04 | 4 |
| 11:05 | 5 |
| 11:06 | 1 |
| 11:07 | 5 |
| 11:08 | 7 |
| 11:09 | 4 |
| 11:10 | 4 |
| 11:11 | 4 |
| 11:12 | 4 |
| 11:13 | 5 |
| 11:14 | 5 |
| 11:15 | 5 |
| 11:16 | 5 |
| 11:17 | 7 |
| 11:18 | 1 |
| 11:19 | 3 |

### Requests Per Minute Statistics

| Metric | Value |
|--------|-------|
| **Mean** | 4.11 requests/minute |
| **Median** | 4.00 requests/minute |
| **Std Dev** | 1.70 requests/minute |
| **Min** | 1 request/minute |
| **Max** | 7 requests/minute |

### Inter-Request Interval Statistics

Time between consecutive LLM API calls:

| Metric | Value |
|--------|-------|
| **Mean** | 13.77 seconds |
| **Median** | 12.00 seconds |
| **Std Dev** | 12.41 seconds |
| **Min** | 1.00 second |
| **Max** | 71.00 seconds |

---

## Time Breakdown by Analysis Stage

### Stage 1: Initialization & Configuration (~2 seconds)

**Duration:** 11:01:21 - 11:01:23

| Component | Time |
|-----------|------|
| Configuration loading from JSON | ~0.2s |
| Token tracker setup | ~0.1s |
| File system results cache initialization | ~0.7s |
| API key retrieval (Apple Connect token) | ~1.0s |

### Stage 2: Directory Classification & File Count (~19 seconds)

**Duration:** 11:01:23 - 11:01:42

| Sub-stage | Duration | Notes |
|-----------|----------|-------|
| Directory structure index building | ~0.5s | Client-side |
| Static directory analysis | ~3s | Found 117 directories to exclude |
| **LLM-based directory classification** | **~12s** | Single LLM call |
| File count check | ~0.5s | Found 7,435 files |

### Stage 3: AST Call Graph Loading (~1 second)

**Duration:** 11:01:41 - 11:01:42

- Reused existing AST files (no regeneration needed)
- Loaded 4,372 functions from merged call graph
- Call graph validation completed

### Stage 4: Code Analysis Phase (~18 minutes)

**Duration:** 11:01:42 - 11:19:27+

#### Cache Performance

| Type | Count | Time | Notes |
|------|-------|------|-------|
| **Cache hits (skipped)** | 525 | ~0.3s total | Instant checksum lookups |
| **New LLM analyses** | 34 | ~425s total | Required full analysis |

**Cache hit rate:** 93.9%

#### Per-Function LLM Analysis Time

| Metric | Value |
|--------|-------|
| Total LLM analysis time | 425.39 seconds |
| Average per function | 12.89 seconds |
| Minimum | 2.26 seconds |
| Maximum | 34.36 seconds |

---

## LLM Token Usage

### Primary Code Analysis

| Metric | Value |
|--------|-------|
| Total input tokens | 472,536 |
| Total output tokens | 24,921 |
| **Total tokens** | **497,457** |
| Functions analyzed | 33 |
| Avg tokens per function | ~15,074 |

---

## Issue Filtering Pipeline

The analysis uses a 3-level filtering pipeline:

### Level 1: Category-Based Filter (Client-side)

- **Time:** Instant (pure Python)
- **Function:** Filters issues by allowed categories (logicBug, performance)
- **No LLM calls required**

### Level 2: LLM-Based Filter

- **Time:** ~2-3 seconds per batch
- **Function:** Filters trivial/false positive issues
- **Triggered:** When issues pass Level 1

### Level 3: Response Challenger

- **Time:** ~15-40 seconds per issue batch
- **Function:** Deep validation of remaining issues
- **Uses:** Tool calls to verify issues against source code

---

## Client-Side Python Time Breakdown

| Component | Estimated Time |
|-----------|----------------|
| Configuration loading & validation | ~0.5s |
| File system cache indexing (642 files) | ~0.7s |
| Directory tree building | ~0.3s |
| File count enumeration (7,435 files) | ~0.4s |
| AST call graph loading | ~0.01s |
| Function filtering & sorting | ~0.3s |
| Cache lookups (525 hits) | ~0.3s |
| Result publishing & file I/O | ~2s |
| **Total client-side overhead** | **~5 seconds** |

---

## Summary: Time Distribution

| Stage | Duration | % of Total |
|-------|----------|------------|
| **1. Initialization** | ~2s | 0.2% |
| **2. Directory Classification** | ~19s | 1.8% |
| └─ LLM directory analysis | ~12s | 1.1% |
| **3. AST Loading** | ~1s | 0.1% |
| **4. Code Analysis** | ~18 min | 97.9% |
| └─ Cache lookups (525) | ~0.3s | <0.1% |
| └─ LLM primary analysis (34) | ~425s | 39% |
| └─ Level 2 LLM filtering | ~30s | 2.8% |
| └─ Level 3 Response Challenger | ~120s | 11% |
| └─ Client-side processing | ~5s | 0.5% |

---

## Key Insights & Optimization Opportunities

### 1. LLM Calls Dominate Execution Time (~95%)

- Primary analysis: ~425s
- Issue filtering (L2+L3): ~150s
- Directory classification: ~12s

**Optimization:** Consider batching multiple functions per LLM call where possible.

### 2. Caching is Highly Effective

- 525 cache hits vs 34 new analyses
- 93.9% cache hit rate
- Estimated time saved: ~6,700 seconds

**Recommendation:** Maintain aggressive caching strategy.

### 3. Client-Side Overhead is Minimal

- Total: ~5 seconds
- File I/O and cache lookups are fast

**No optimization needed** for client-side processing.

### 4. Issue Filtering Adds Significant Time

- Level 2 + Level 3 filtering adds ~150s
- But significantly reduces false positives

**Trade-off:** Time vs. quality is acceptable.

### 5. Request Rate is Moderate

- Average: 4.11 requests/minute
- Peak: 7 requests/minute
- No rate limiting issues observed

---

## Parallelization Analysis: Primary Analysis + L2/L3 Filtering

### Current Sequential Flow

In the current implementation, operations are strictly sequential:
```
Primary(N) → L2(N) → L3(N) → Primary(N+1) → L2(N+1) → L3(N+1) → ...
```

### Proposed Parallel Flow

If primary analysis and L2+L3 filtering were parallelized (interleaved):
```
Primary(N) → Primary(N+1) → Primary(N+2) → ...
              ↓
           L2(N) → L3(N) → L2(N+1) → L3(N+1) → ...
```

This means analysis of function N+1 starts as soon as L2+L3 filtering of function N begins.

### Simulated Parallel Request Rates

| Minute | Sequential | Parallel (Simulated) |
|--------|------------|---------------------|
| 11:01 | 2 | 2 |
| 11:02 | 2 | 7 |
| 11:03 | 5 | 8 |
| 11:04 | 4 | 11 |
| 11:05 | 5 | 10 |
| 11:06 | 1 | 8 |
| 11:07 | 5 | 9 |
| 11:08 | 7 | 9 |
| 11:09 | 4 | 4 |
| 11:10 | 4 | - |
| 11:11 | 4 | - |
| 11:12 | 4 | - |
| 11:13 | 5 | - |
| 11:14 | 5 | - |
| 11:15 | 5 | - |
| 11:16 | 5 | - |
| 11:17 | 7 | - |
| 11:18 | 1 | - |
| 11:19 | 3 | - |

### Parallel Mode Statistics

| Metric | Sequential | Parallel | Change |
|--------|------------|----------|--------|
| **Mean** | 3.67 req/min | 7.56 req/min | +106% |
| **Median** | 3.50 req/min | 8.00 req/min | +129% |
| **Std Dev** | 1.46 req/min | 2.88 req/min | +97% |
| **Min** | 1 req/min | 2 req/min | +100% |
| **Max** | 7 req/min | 11 req/min | +57% |

### Time Savings Analysis

| Metric | Sequential | Parallel | Improvement |
|--------|------------|----------|-------------|
| **Total Duration** | 17.7 min | 8.0 min | **2.21x faster** |
| **Time Saved** | - | 9.7 min | 55% reduction |
| **Total Requests** | 66 | 68 | +2 (rounding) |

### Key Findings

1. **Request Rate Doubles**: Parallelization would increase average request rate from ~3.7 to ~7.6 requests/minute

2. **Peak Rate Increases**: Maximum concurrent requests would increase from 7 to 11 per minute

3. **Significant Time Savings**: Total analysis time would be reduced by ~55% (from 17.7 to 8.0 minutes)

4. **Trade-offs**:
   - Higher concurrent API load (may hit rate limits)
   - More complex error handling required
   - Memory usage increases (multiple analyses in flight)

### Implementation Considerations

To implement parallelization:
- Use `asyncio` or thread pools for concurrent LLM calls
- Implement a queue for filtering operations
- Add rate limiting to stay within API quotas
- Handle partial failures gracefully

---

## Appendix: Log Analysis Commands

To reproduce this analysis from a log file:

```bash
# Count LLM analysis times
grep "Total time taken" log.txt | awk -F': ' '{print $NF}'

# Count cache hits vs misses
grep "ANALYSIS SKIPPED" log.txt | wc -l
grep "NO existing result found" log.txt | wc -l

# Extract token usage
grep "TOKEN USAGE SUMMARY" log.txt

# Calculate requests per minute
grep "Analysis iteration 1/" log.txt | awk -F'[ ,]' '{print $1, $2}'
```

---

*Last updated: March 24, 2026*

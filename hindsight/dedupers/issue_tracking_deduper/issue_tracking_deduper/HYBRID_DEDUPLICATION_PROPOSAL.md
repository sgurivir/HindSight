# Hybrid Deduplication Proposal: Combining Cosine Similarity with Cluster-Based Matching

## Executive Summary

This proposal outlines an enhanced deduplication strategy for the issue_tracking_deduper tool that combines the existing cosine similarity approach with cluster-based deduplication using file paths and function names. The key insight is that **file_path provides the strongest signal** for identifying duplicate bug reports, followed by **function_name**, with semantic similarity serving as a complementary signal.

## Problem Statement

The current issue_tracking_deduper uses cosine similarity based on vector embeddings to find potential duplicate issues. While this approach captures semantic similarity well, it may miss duplicates that:

1. **Share the same file location** but have different textual descriptions
2. **Affect the same function** but are described differently
3. **Are semantically similar** but occur in completely different code locations (false positives)

## Proposed Solution: Multi-Signal Hybrid Scoring

### Signal Hierarchy (by strength)

| Signal | Weight | Rationale |
|--------|--------|-----------|
| **File Path Match** | 0.40 | Strongest indicator - same file = likely same bug |
| **Function Name Match** | 0.30 | Strong indicator - same function = related issues |
| **Cosine Similarity** | 0.30 | Semantic similarity for description matching |

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Hybrid Deduplication Pipeline                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │   Parse      │───▶│   Cluster    │───▶│   Hybrid     │───▶│  Ranked   │ │
│  │   Issues     │    │   by File    │    │   Scoring    │    │  Results  │ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └───────────┘ │
│         │                   │                   │                   │       │
│         ▼                   ▼                   ▼                   ▼       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │ Extract:     │    │ Group by:    │    │ Combine:     │    │ Sort by:  │ │
│  │ - file_path  │    │ - file_name  │    │ - File score │    │ - Hybrid  │ │
│  │ - func_name  │    │ - directory  │    │ - Func score │    │   score   │ │
│  │ - title/desc │    │ - func_name  │    │ - Cosine sim │    │           │ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └───────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Detailed Design

### 1. Enhanced Data Models

#### 1.1 Update `Issue` class in [`issue.py`](issue_tracking_deduper/deduper/issue.py:16)

```python
@dataclass
class Issue:
    # Existing fields...
    id: str
    title: str
    description: str
    file_path: Optional[str] = None
    function_name: Optional[str] = None
    # ... other fields
    
    # NEW: Computed properties for clustering
    @property
    def file_name(self) -> Optional[str]:
        """Extract just the filename from file_path."""
        if self.file_path:
            return Path(self.file_path).name
        return None
    
    @property
    def directory_path(self) -> Optional[str]:
        """Extract the directory path without filename."""
        if self.file_path:
            return str(Path(self.file_path).parent)
        return None
    
    @property
    def normalized_function_name(self) -> Optional[str]:
        """Normalize function name for matching (lowercase, strip prefixes)."""
        if self.function_name:
            # Remove common prefixes like '-[', '+[' for Objective-C
            name = self.function_name.strip()
            name = re.sub(r'^[-+]\[', '', name)
            name = re.sub(r'\]$', '', name)
            return name.lower()
        return None
```

#### 1.2 Update `IssueEntry` class in [`issue.py`](issue_tracking_deduper/deduper/issue.py:64)

```python
@dataclass
class IssueEntry:
    # Existing fields...
    issue_id: str
    title: str
    description: str
    component: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    file_path: str = ""  # Already exists
    
    # NEW: Add function_name field
    function_name: Optional[str] = None
    
    # NEW: Computed properties (same as Issue)
    @property
    def file_name(self) -> Optional[str]:
        """Extract just the filename from file_path."""
        if self.file_path:
            return Path(self.file_path).name
        return None
```

#### 1.3 New `HybridMatch` class

```python
@dataclass
class HybridMatch:
    """
    Represents a match with hybrid scoring from multiple signals.
    """
    issue_id: str
    issue_title: str
    issue_url: str
    issue_description: str
    
    # Individual scores (0.0 - 1.0)
    file_path_score: float = 0.0
    function_name_score: float = 0.0
    cosine_similarity_score: float = 0.0
    
    # Combined hybrid score
    hybrid_score: float = 0.0
    
    # Match details for transparency
    match_reasons: List[str] = field(default_factory=list)
    
    @property
    def confidence_level(self) -> str:
        """Return confidence level based on hybrid score."""
        if self.hybrid_score > 0.85:
            return "very_high"
        elif self.hybrid_score > 0.70:
            return "high"
        elif self.hybrid_score > 0.55:
            return "moderate"
        else:
            return "low"
```

### 2. File Path Matching Strategy

#### 2.1 File Path Similarity Scoring

```python
class FilePathMatcher:
    """
    Computes similarity scores based on file paths.
    """
    
    @staticmethod
    def compute_score(issue_path: str, candidate_path: str) -> float:
        """
        Compute file path similarity score (0.0 - 1.0).
        
        Scoring rules:
        - Exact match: 1.0
        - Same filename, different directory: 0.8
        - Same directory, different filename: 0.4
        - Partial path overlap: 0.2 - 0.6 (based on overlap)
        - No match: 0.0
        """
        if not issue_path or not candidate_path:
            return 0.0
        
        issue_p = Path(issue_path)
        candidate_p = Path(candidate_path)
        
        # Exact match
        if issue_path == candidate_path:
            return 1.0
        
        # Same filename
        if issue_p.name == candidate_p.name:
            # Check directory similarity
            issue_parts = issue_p.parts[:-1]
            candidate_parts = candidate_p.parts[:-1]
            
            # Calculate directory overlap
            common = len(set(issue_parts) & set(candidate_parts))
            total = max(len(issue_parts), len(candidate_parts), 1)
            dir_similarity = common / total
            
            return 0.8 + (0.2 * dir_similarity)
        
        # Same directory
        if issue_p.parent == candidate_p.parent:
            return 0.4
        
        # Partial path overlap
        issue_parts = set(issue_p.parts)
        candidate_parts = set(candidate_p.parts)
        common = len(issue_parts & candidate_parts)
        total = max(len(issue_parts), len(candidate_parts), 1)
        
        return 0.2 * (common / total)
```

#### 2.2 File Name Extraction from Issue Descriptions

Many issue descriptions contain file paths in their text. We need to extract these:

```python
class FilePathExtractor:
    """
    Extracts file paths from issue descriptions.
    """
    
    # Common file extensions in bug reports
    FILE_EXTENSIONS = [
        '.mm', '.m', '.swift', '.cpp', '.c', '.h', '.hpp',
        '.py', '.js', '.ts', '.java', '.kt', '.rb'
    ]
    
    # Regex patterns for file paths
    PATH_PATTERNS = [
        r'File:\s*([^\s\n]+)',
        r'in\s+([^\s]+\.(mm|m|swift|cpp|c|h|py|js))',
        r'([A-Za-z][A-Za-z0-9_/]+\.(mm|m|swift|cpp|c|h|py|js))',
    ]
    
    @classmethod
    def extract_file_paths(cls, text: str) -> List[str]:
        """Extract all file paths from text."""
        paths = []
        for pattern in cls.PATH_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                if any(match.endswith(ext) for ext in cls.FILE_EXTENSIONS):
                    paths.append(match)
        return list(set(paths))
```

### 3. Function Name Matching Strategy

#### 3.1 Function Name Similarity Scoring

```python
class FunctionNameMatcher:
    """
    Computes similarity scores based on function names.
    """
    
    @staticmethod
    def normalize(func_name: str) -> str:
        """Normalize function name for comparison."""
        if not func_name:
            return ""
        
        # Remove Objective-C method prefixes
        name = re.sub(r'^[-+]\[', '', func_name)
        name = re.sub(r'\]$', '', name)
        
        # Remove common suffixes
        name = re.sub(r'WithOptions?:?$', '', name)
        name = re.sub(r'Handler$', '', name)
        
        # Convert to lowercase
        return name.lower().strip()
    
    @staticmethod
    def compute_score(issue_func: str, candidate_func: str) -> float:
        """
        Compute function name similarity score (0.0 - 1.0).
        
        Scoring rules:
        - Exact match (normalized): 1.0
        - Substring match: 0.7
        - Token overlap (camelCase split): 0.3 - 0.6
        - No match: 0.0
        """
        if not issue_func or not candidate_func:
            return 0.0
        
        norm_issue = FunctionNameMatcher.normalize(issue_func)
        norm_candidate = FunctionNameMatcher.normalize(candidate_func)
        
        # Exact match
        if norm_issue == norm_candidate:
            return 1.0
        
        # Substring match
        if norm_issue in norm_candidate or norm_candidate in norm_issue:
            return 0.7
        
        # Token overlap (split camelCase)
        issue_tokens = set(re.findall(r'[a-z]+', norm_issue))
        candidate_tokens = set(re.findall(r'[a-z]+', norm_candidate))
        
        if not issue_tokens or not candidate_tokens:
            return 0.0
        
        common = len(issue_tokens & candidate_tokens)
        total = max(len(issue_tokens), len(candidate_tokens))
        
        return 0.3 + (0.3 * common / total)
```

#### 3.2 Function Name Extraction from Issue Descriptions

```python
class FunctionNameExtractor:
    """
    Extracts function names from issue descriptions.
    """
    
    FUNCTION_PATTERNS = [
        r'Function:\s*([^\s\n]+)',
        r'in\s+function\s+([^\s\n(]+)',
        r'method\s+([^\s\n(]+)',
        r'-\[([^\]]+)\]',  # Objective-C instance method
        r'\+\[([^\]]+)\]',  # Objective-C class method
        r'func\s+([a-zA-Z_][a-zA-Z0-9_]*)',  # Swift function
    ]
    
    @classmethod
    def extract_function_names(cls, text: str) -> List[str]:
        """Extract all function names from text."""
        functions = []
        for pattern in cls.FUNCTION_PATTERNS:
            matches = re.findall(pattern, text)
            functions.extend(matches)
        return list(set(functions))
```

### 4. Hybrid Scoring Algorithm

#### 4.1 HybridMatcher Class

```python
class HybridMatcher:
    """
    Combines multiple signals for hybrid deduplication scoring.
    """
    
    # Default weights (configurable)
    DEFAULT_WEIGHTS = {
        'file_path': 0.40,
        'function_name': 0.30,
        'cosine_similarity': 0.30,
    }
    
    def __init__(
        self,
        vector_store: VectorStore,
        embedding_generator: EmbeddingGenerator,
        weights: Optional[Dict[str, float]] = None,
        threshold: float = 0.50,
        top_k: int = 10
    ):
        self.vector_store = vector_store
        self.embedding_generator = embedding_generator
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.threshold = threshold
        self.top_k = top_k
        
        self.file_matcher = FilePathMatcher()
        self.func_matcher = FunctionNameMatcher()
        self.file_extractor = FilePathExtractor()
        self.func_extractor = FunctionNameExtractor()
    
    def find_matches(self, issue: Issue) -> List[HybridMatch]:
        """
        Find potential duplicates using hybrid scoring.
        
        Strategy:
        1. Query vector DB for top-N semantically similar issues
        2. For each candidate, compute file_path and function_name scores
        3. Combine scores using weighted average
        4. Filter by threshold and sort by hybrid score
        """
        # Step 1: Get semantic candidates (cast wider net)
        query_text = issue.to_embedding_text()
        query_embedding = self.embedding_generator.generate(query_text)
        
        # Get more candidates than top_k to allow re-ranking
        candidates = self.vector_store.query(
            query_embedding=query_embedding,
            n_results=self.top_k * 3  # Get 3x candidates for re-ranking
        )
        
        # Step 2: Compute hybrid scores for each candidate
        matches = []
        for doc_id, metadata, distance in candidates:
            # Get issue details
            candidate_description = self._get_issue_description(doc_id)
            
            # Compute individual scores
            cosine_score = 1 - (distance / 2)  # Convert distance to similarity
            
            file_score = self._compute_file_score(issue, metadata, candidate_description)
            func_score = self._compute_function_score(issue, metadata, candidate_description)
            
            # Compute weighted hybrid score
            hybrid_score = (
                self.weights['file_path'] * file_score +
                self.weights['function_name'] * func_score +
                self.weights['cosine_similarity'] * cosine_score
            )
            
            # Build match reasons
            reasons = self._build_match_reasons(
                file_score, func_score, cosine_score, issue, metadata
            )
            
            match = HybridMatch(
                issue_id=metadata.get('issue_id', ''),
                issue_title=metadata.get('title', ''),
                issue_url=f"rdar://{metadata.get('issue_id', '')}",
                issue_description=candidate_description,
                file_path_score=file_score,
                function_name_score=func_score,
                cosine_similarity_score=cosine_score,
                hybrid_score=hybrid_score,
                match_reasons=reasons
            )
            matches.append(match)
        
        # Step 3: Filter and sort
        matches = [m for m in matches if m.hybrid_score >= self.threshold]
        matches.sort(key=lambda m: m.hybrid_score, reverse=True)
        
        return matches[:self.top_k]
    
    def _compute_file_score(
        self,
        issue: Issue,
        metadata: Dict[str, Any],
        candidate_description: str
    ) -> float:
        """Compute file path similarity score."""
        if not issue.file_path:
            return 0.0
        
        # Check metadata file_path first
        candidate_file = metadata.get('file_path', '')
        if candidate_file:
            score = self.file_matcher.compute_score(issue.file_path, candidate_file)
            if score > 0:
                return score
        
        # Extract file paths from description
        extracted_paths = self.file_extractor.extract_file_paths(candidate_description)
        if extracted_paths:
            scores = [
                self.file_matcher.compute_score(issue.file_path, path)
                for path in extracted_paths
            ]
            return max(scores) if scores else 0.0
        
        return 0.0
    
    def _compute_function_score(
        self,
        issue: Issue,
        metadata: Dict[str, Any],
        candidate_description: str
    ) -> float:
        """Compute function name similarity score."""
        if not issue.function_name:
            return 0.0
        
        # Check metadata function_name first
        candidate_func = metadata.get('function_name', '')
        if candidate_func:
            score = self.func_matcher.compute_score(issue.function_name, candidate_func)
            if score > 0:
                return score
        
        # Extract function names from description
        extracted_funcs = self.func_extractor.extract_function_names(candidate_description)
        if extracted_funcs:
            scores = [
                self.func_matcher.compute_score(issue.function_name, func)
                for func in extracted_funcs
            ]
            return max(scores) if scores else 0.0
        
        return 0.0
    
    def _build_match_reasons(
        self,
        file_score: float,
        func_score: float,
        cosine_score: float,
        issue: Issue,
        metadata: Dict[str, Any]
    ) -> List[str]:
        """Build human-readable match reasons."""
        reasons = []
        
        if file_score >= 0.8:
            reasons.append(f"📁 Same file: {issue.file_name}")
        elif file_score >= 0.4:
            reasons.append(f"📁 Similar file path (score: {file_score:.0%})")
        
        if func_score >= 0.8:
            reasons.append(f"🔧 Same function: {issue.function_name}")
        elif func_score >= 0.4:
            reasons.append(f"🔧 Similar function (score: {func_score:.0%})")
        
        if cosine_score >= 0.8:
            reasons.append(f"📝 High semantic similarity ({cosine_score:.0%})")
        elif cosine_score >= 0.6:
            reasons.append(f"📝 Moderate semantic similarity ({cosine_score:.0%})")
        
        return reasons
```

### 5. Cluster-Based Pre-filtering (Optional Optimization)

For large issue databases, we can pre-cluster issues by file name to speed up matching:

```python
class FileClusterIndex:
    """
    Pre-computed index of issues grouped by file name.
    
    This allows O(1) lookup of issues affecting the same file,
    avoiding the need to scan all issues for file matching.
    """
    
    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store
        self._file_to_issues: Dict[str, List[str]] = {}
        self._func_to_issues: Dict[str, List[str]] = {}
        self._built = False
    
    def build_index(self):
        """Build the cluster index from all issues in the store."""
        # Get all issues from the store
        all_issues = self._get_all_issues()
        
        for issue_id, metadata, description in all_issues:
            # Index by file name
            file_paths = FilePathExtractor.extract_file_paths(description)
            for path in file_paths:
                file_name = Path(path).name
                if file_name not in self._file_to_issues:
                    self._file_to_issues[file_name] = []
                self._file_to_issues[file_name].append(issue_id)
            
            # Index by function name
            func_names = FunctionNameExtractor.extract_function_names(description)
            for func in func_names:
                norm_func = FunctionNameMatcher.normalize(func)
                if norm_func not in self._func_to_issues:
                    self._func_to_issues[norm_func] = []
                self._func_to_issues[norm_func].append(issue_id)
        
        self._built = True
    
    def get_issues_by_file(self, file_name: str) -> List[str]:
        """Get issue IDs that mention the given file name."""
        return self._file_to_issues.get(file_name, [])
    
    def get_issues_by_function(self, func_name: str) -> List[str]:
        """Get issue IDs that mention the given function name."""
        norm_func = FunctionNameMatcher.normalize(func_name)
        return self._func_to_issues.get(norm_func, [])
```

### 6. Updated Ingestion Pipeline

The issue ingestion process needs to extract and store file paths and function names:

```python
# In vector_db/ingestion.py

class IssueIngester:
    """Enhanced ingestion with file/function extraction."""
    
    def ingest_issue(self, issue: IssueEntry) -> None:
        """Ingest an issue with enhanced metadata."""
        
        # Extract file paths from description
        file_paths = FilePathExtractor.extract_file_paths(issue.description)
        if file_paths:
            issue.file_path = file_paths[0]  # Primary file
        
        # Extract function names from description
        func_names = FunctionNameExtractor.extract_function_names(issue.description)
        if func_names:
            issue.function_name = func_names[0]  # Primary function
        
        # Update metadata to include extracted info
        metadata = issue.to_metadata()
        metadata['extracted_files'] = ','.join(file_paths)
        metadata['extracted_functions'] = ','.join(func_names)
        
        # Generate embedding and store
        embedding = self.embedding_generator.generate(issue.to_embedding_text())
        self.vector_store.add_issue(issue, embedding)
```

### 7. Configuration Options

Add new configuration options in [`config.py`](issue_tracking_deduper/config.py):

```python
# Hybrid scoring weights
HYBRID_WEIGHTS = {
    'file_path': 0.40,
    'function_name': 0.30,
    'cosine_similarity': 0.30,
}

# Minimum scores for each signal to contribute
MIN_FILE_SCORE = 0.2
MIN_FUNCTION_SCORE = 0.2
MIN_COSINE_SCORE = 0.5

# Hybrid threshold (lower than pure cosine since we have more signals)
DEFAULT_HYBRID_THRESHOLD = 0.50

# Enable/disable cluster-based pre-filtering
ENABLE_CLUSTER_INDEX = True
```

### 8. CLI Updates

Update the CLI to support hybrid mode:

```bash
# Use hybrid matching (default)
python3 -m issue_tracking_deduper dedupe \
    --report report.html \
    --mode hybrid \
    --file-weight 0.4 \
    --func-weight 0.3 \
    --cosine-weight 0.3

# Use pure cosine similarity (legacy mode)
python3 -m issue_tracking_deduper dedupe \
    --report report.html \
    --mode cosine

# Use file-first matching (prioritize file matches)
python3 -m issue_tracking_deduper dedupe \
    --report report.html \
    --mode file-first
```

## Implementation Plan

### Phase 1: Data Model Updates (1-2 days)
- [ ] Add `file_name`, `directory_path`, `normalized_function_name` properties to `Issue`
- [ ] Add `function_name` field to `IssueEntry`
- [ ] Create `HybridMatch` dataclass
- [ ] Update `to_metadata()` and `from_metadata()` methods

### Phase 2: Matching Components (2-3 days)
- [ ] Implement `FilePathMatcher` class
- [ ] Implement `FunctionNameMatcher` class
- [ ] Implement `FilePathExtractor` class
- [ ] Implement `FunctionNameExtractor` class
- [ ] Write unit tests for all matchers

### Phase 3: Hybrid Matcher (2-3 days)
- [ ] Implement `HybridMatcher` class
- [ ] Integrate with existing `IssueMatcher` (backward compatibility)
- [ ] Add configuration options for weights
- [ ] Write integration tests

### Phase 4: Ingestion Updates (1 day)
- [ ] Update ingestion to extract file paths and function names
- [ ] Store extracted metadata in vector DB
- [ ] Re-ingest existing issues with new metadata

### Phase 5: CLI and Report Updates (1-2 days)
- [ ] Add `--mode` flag to CLI
- [ ] Add weight configuration flags
- [ ] Update HTML report to show match reasons
- [ ] Update summary statistics

### Phase 6: Optional Optimizations (1-2 days)
- [ ] Implement `FileClusterIndex` for large databases
- [ ] Add caching for extracted file/function names
- [ ] Performance benchmarking

## Expected Benefits

1. **Higher Precision**: File path matching reduces false positives from semantically similar but unrelated issues
2. **Higher Recall**: Function name matching catches duplicates with different descriptions
3. **Transparency**: Match reasons explain why each duplicate was identified
4. **Configurability**: Weights can be tuned based on use case
5. **Backward Compatibility**: Pure cosine mode remains available

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| File paths not always present in issues | Fall back to cosine similarity when file info missing |
| Function name extraction may be noisy | Use conservative patterns, require minimum score |
| Performance impact from additional processing | Implement cluster index for pre-filtering |
| Weight tuning may be difficult | Provide sensible defaults, allow per-run configuration |

## Success Metrics

1. **Precision**: % of identified duplicates that are true duplicates (target: >90%)
2. **Recall**: % of true duplicates that are identified (target: >85%)
3. **Processing Time**: Time to dedupe a 1000-issue report (target: <60 seconds)
4. **User Satisfaction**: Reduction in manual duplicate review time

## Appendix: Example Scenarios

### Scenario 1: Same File, Different Description

**Issue:**
```
Title: Memory leak in location tracking
File: CLMicroLocationSemiSupervisedAlgorithm.mm
Function: processLocationUpdate
```

**Candidate Issue:**
```
Title: Unbounded memory growth in micro-location algorithm
Description: The processLocationUpdate method allocates memory...
```

**Scores:**
- File Path: 1.0 (exact match)
- Function Name: 1.0 (exact match)
- Cosine Similarity: 0.65 (moderate - different wording)
- **Hybrid Score: 0.90** (correctly identified as duplicate)

### Scenario 2: Similar Description, Different File

**Issue:**
```
Title: Linear search creates O(n²) complexity
File: LocationManager.mm
Function: findNearestBeacon
```

**Candidate Issue:**
```
Title: Linear search creates O(n²) complexity
File: BeaconTracker.mm
Function: scanForBeacons
```

**Scores:**
- File Path: 0.0 (different files)
- Function Name: 0.3 (some token overlap)
- Cosine Similarity: 0.95 (very similar description)
- **Hybrid Score: 0.38** (correctly identified as NOT a duplicate)

### Scenario 3: Same Function, Partial File Match

**Issue:**
```
Title: Crash in location callback
File: Source/Location/CLLocationManager+Extensions.mm
Function: -[CLLocationManager handleLocationUpdate:]
```

**Candidate Issue:**
```
Title: Null pointer dereference in location handling
File: CLLocationManager+Extensions.mm
Function: handleLocationUpdate
```

**Scores:**
- File Path: 0.85 (same filename, partial directory match)
- Function Name: 0.9 (normalized match)
- Cosine Similarity: 0.55 (moderate similarity)
- **Hybrid Score: 0.78** (correctly identified as likely duplicate)

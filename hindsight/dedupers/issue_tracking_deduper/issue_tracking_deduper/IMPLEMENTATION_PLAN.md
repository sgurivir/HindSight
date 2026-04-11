# Issue Tracking Deduper Implementation Plan

## Overview

The `issue_tracking_deduper` is a Python tool that identifies potential duplicate issues between LLM static analyzer HTML reports and existing issue descriptions stored in a vector database. It helps engineers avoid filing duplicate issues by showing close matches from previously filed issues.

## Key Features

1. **Vector Database Ingestion**: Build a vector DB from a directory of issue description markdown files
2. **HTML Report Parsing**: Parse LLM static analyzer HTML reports to extract issues
3. **Deduplication Matching**: Find close matches between new issues and existing issues
4. **Annotated Report Generation**: Create a new HTML report with deduplication annotations

## Directory Structure

```
issue_tracking_deduper/
├── __init__.py
├── __main__.py                    # Entry point for `python -m issue_tracking_deduper`
├── IMPLEMENTATION_PLAN.md         # This document
├── cli.py                         # Command-line interface
├── config.py                      # Configuration constants
├── vector_db/
│   ├── __init__.py
│   ├── embeddings.py              # Embedding generation (sentence-transformers)
│   ├── store.py                   # Vector DB operations (ChromaDB)
│   └── ingestion.py               # Issue ingestion logic
├── parsers/
│   ├── __init__.py
│   ├── base_parser.py             # Abstract base class for report parsers
│   ├── issue_parser.py            # Parse issue markdown files
│   └── html_report_parser.py      # Parse LLM static analyzer HTML reports
├── deduper/
│   ├── __init__.py
│   ├── matcher.py                 # Similarity matching logic
│   └── issue.py                   # Issue data model
└── report/
    ├── __init__.py
    └── html_generator.py          # Generate annotated HTML report
```

## Command Line Interface

### Ingest Issues into Vector DB
```bash
python -m issue_tracking_deduper ingest \
    --issue-dir /Users/sgurivireddy/issues_on_file \
    --db-path ~/.llm/issue_vectors.db
```

### Dedupe HTML Report
```bash
python -m issue_tracking_deduper dedupe \
    --report file:///Users/sgurivireddy/Desktop/repo_analysis_Corelocation_hindsight_20260216_120133_UPDATED.html \
    --db-path ~/.llm/issue_vectors.db \
    --output /path/to/output_report.html \
    --threshold 0.75
```

### Full Pipeline (Ingest + Dedupe)
```bash
python -m issue_tracking_deduper run \
    --issue-dir /Users/sgurivireddy/issues_on_file \
    --report file:///path/to/report.html \
    --output /path/to/output_report.html
```

### Additional Options
```bash
python -m issue_tracking_deduper dedupe \
    --report /path/to/report.html \
    --db-path ~/.llm/issue_vectors.db \
    --threshold 0.8                    # Similarity threshold (0.0-1.0)
    --top-k 2                          # Max number of matches to show
    --output /path/to/output.html      # Output file path
```

## Data Models

### Issue Model
```python
@dataclass
class Issue:
    """Represents an issue from an LLM static analyzer report."""
    id: str                    # Unique identifier
    title: str                 # Issue title
    description: str           # Issue description
    file_path: Optional[str]   # Source file path
    function_name: Optional[str]  # Function name
    severity: Optional[str]    # Issue severity
    category: Optional[str]    # Issue category
    raw_html: Optional[str]    # Original HTML content
    
    def to_embedding_text(self) -> str:
        """Generate text for embedding."""
        parts = [self.title, self.description]
        if self.file_path:
            parts.append(f"File: {self.file_path}")
        if self.function_name:
            parts.append(f"Function: {self.function_name}")
        return " ".join(parts)
```

### IssueEntry Model
```python
@dataclass
class IssueEntry:
    """Represents an issue entry in the vector database."""
    issue_id: str              # Issue ID (e.g., "123456789")
    title: str                 # Issue title
    description: str           # Issue description
    component: Optional[str]   # Component name
    keywords: List[str]        # Keywords
    file_path: str             # Source markdown file path
    content_hash: str          # Hash of content for deduplication
    
    def to_embedding_text(self) -> str:
        """Generate text for embedding."""
        parts = [self.title, self.description]
        if self.component:
            parts.append(f"Component: {self.component}")
        if self.keywords:
            parts.append(f"Keywords: {', '.join(self.keywords)}")
        return " ".join(parts)
```

### DedupeMatch Model
```python
@dataclass
class DedupeMatch:
    """Represents a potential duplicate match."""
    issue_id: str              # Matched issue ID
    issue_title: str           # Matched issue title
    similarity_score: float    # Similarity score (0.0-1.0)
    issue_url: str             # URL to issue (rdar://...)
    match_reason: str          # Brief explanation of match
```

## Vector Database Architecture

### Technology Choice: ChromaDB

ChromaDB is chosen for the vector database because:
- Lightweight and embedded (no server required)
- Persistent storage to disk
- Built-in support for sentence-transformers embeddings
- Simple Python API
- Handles deduplication via document IDs

### Database Location
```
~/.llm/
├── issue_vectors/             # ChromaDB persistent storage
│   ├── chroma.sqlite3         # Metadata and mappings
│   └── index/                 # Vector index files
└── config.json                # Optional configuration
```

### Embedding Model

Using `sentence-transformers/all-MiniLM-L6-v2`:
- Fast and efficient
- Good balance of quality and speed
- 384-dimensional embeddings
- Works well for semantic similarity

### Schema Design

```python
# ChromaDB Collection Schema
collection = client.get_or_create_collection(
    name="issue_embeddings",
    metadata={"hnsw:space": "cosine"}  # Use cosine similarity
)

# Document structure
{
    "ids": ["issue_123456789"],
    "documents": ["<embedding text>"],
    "metadatas": [{
        "issue_id": "123456789",
        "title": "Issue title",
        "component": "CoreLocation",
        "keywords": "memory,leak,performance",
        "file_path": "/path/to/issue_123456789_title.md",
        "content_hash": "sha256:abc123...",
        "ingested_at": "2026-02-28T12:00:00Z"
    }],
    "embeddings": [[0.1, 0.2, ...]]  # 384-dim vector
}
```

### Duplicate Ingestion Handling

To prevent duplicate ingestion:

1. **Content Hash Check**: Before ingesting, compute SHA-256 hash of issue content
2. **ID-based Deduplication**: Use issue ID as document ID in ChromaDB
3. **Upsert Operation**: Use ChromaDB's upsert to update existing entries

```python
def ingest_issue(self, issue: IssueEntry) -> bool:
    """
    Ingest an issue entry, handling duplicates.
    
    Returns:
        True if new entry was added, False if duplicate was skipped/updated
    """
    doc_id = f"issue_{issue.issue_id}"
    
    # Check if already exists with same content
    existing = self.collection.get(ids=[doc_id])
    if existing and existing['metadatas']:
        existing_hash = existing['metadatas'][0].get('content_hash')
        if existing_hash == issue.content_hash:
            return False  # Skip - identical content
    
    # Upsert (insert or update)
    self.collection.upsert(
        ids=[doc_id],
        documents=[issue.to_embedding_text()],
        metadatas=[issue.to_metadata()]
    )
    return True
```

## HTML Report Parsing Strategy

### Parser Architecture

The parser system is designed to be extensible for future report formats:

```python
class BaseReportParser(ABC):
    """Abstract base class for report parsers."""
    
    @abstractmethod
    def can_parse(self, report_path: str) -> bool:
        """Check if this parser can handle the given report."""
        pass
    
    @abstractmethod
    def parse(self, report_path: str) -> List[Issue]:
        """Parse the report and return list of issues."""
        pass
    
    @abstractmethod
    def get_format_name(self) -> str:
        """Return the name of the format this parser handles."""
        pass
```

### Current Format: LLM Static Analyzer Report

Based on the example file pattern `repo_analysis_Corelocation_hindsight_20260216_120133_UPDATED.html`:

```python
class LLMStaticAnalyzerParser(BaseReportParser):
    """Parser for LLM static analyzer HTML reports."""
    
    def can_parse(self, report_path: str) -> bool:
        """Check if this is an LLM static analyzer report."""
        # Check filename pattern or HTML structure
        return "repo_analysis" in report_path or self._check_html_structure(report_path)
    
    def parse(self, report_path: str) -> List[Issue]:
        """Parse HTML report and extract issues."""
        soup = BeautifulSoup(html_content, 'html.parser')
        issues = []
        
        # Find issue containers (structure TBD based on actual HTML)
        for issue_elem in soup.find_all(class_='issue'):
            issue = Issue(
                id=self._extract_id(issue_elem),
                title=self._extract_title(issue_elem),
                description=self._extract_description(issue_elem),
                file_path=self._extract_file_path(issue_elem),
                function_name=self._extract_function_name(issue_elem),
                severity=self._extract_severity(issue_elem),
                category=self._extract_category(issue_elem),
                raw_html=str(issue_elem)
            )
            issues.append(issue)
        
        return issues
```

### Parser Registry

```python
class ParserRegistry:
    """Registry of available report parsers."""
    
    def __init__(self):
        self._parsers: List[BaseReportParser] = []
    
    def register(self, parser: BaseReportParser):
        """Register a parser."""
        self._parsers.append(parser)
    
    def get_parser(self, report_path: str) -> Optional[BaseReportParser]:
        """Find a parser that can handle the given report."""
        for parser in self._parsers:
            if parser.can_parse(report_path):
                return parser
        return None

# Default registry with built-in parsers
default_registry = ParserRegistry()
default_registry.register(LLMStaticAnalyzerParser())
# Future: default_registry.register(OtherFormatParser())
```

## Deduplication Logic

### Matching Algorithm

```python
class IssueMatcher:
    """Matches issues against issue database."""
    
    def __init__(self, vector_store: VectorStore, threshold: float = 0.75, top_k: int = 5):
        self.vector_store = vector_store
        self.threshold = threshold
        self.top_k = top_k
    
    def find_matches(self, issue: Issue) -> List[DedupeMatch]:
        """
        Find potential duplicate issues for an issue.
        
        Args:
            issue: The issue to match
        
        Returns:
            List of DedupeMatch objects, sorted by similarity (highest first)
        """
        # Generate embedding text
        query_text = issue.to_embedding_text()
        
        # Query vector DB
        results = self.vector_store.query(
            query_text=query_text,
            n_results=self.top_k
        )
        
        # Filter by threshold and create matches
        matches = []
        for doc_id, metadata, distance in results:
            similarity = 1 - distance  # Convert distance to similarity
            if similarity >= self.threshold:
                matches.append(DedupeMatch(
                    issue_id=metadata['issue_id'],
                    issue_title=metadata['title'],
                    similarity_score=similarity,
                    issue_url=f"rdar://{metadata['issue_id']}",
                    match_reason=self._generate_match_reason(issue, metadata, similarity)
                ))
        
        return sorted(matches, key=lambda m: m.similarity_score, reverse=True)
    
    def _generate_match_reason(self, issue: Issue, issue_metadata: dict, similarity: float) -> str:
        """Generate a brief explanation of why this is a match."""
        if similarity > 0.9:
            return "Very high similarity - likely duplicate"
        elif similarity > 0.8:
            return "High similarity - probable duplicate"
        elif similarity > 0.7:
            return "Moderate similarity - possible duplicate"
        else:
            return "Low similarity - may be related"
```

### Batch Processing

```python
def dedupe_report(
    report_path: str,
    vector_store: VectorStore,
    threshold: float = 0.75,
    top_k: int = 5
) -> Dict[str, List[DedupeMatch]]:
    """
    Process an entire report and find matches for all issues.
    
    Returns:
        Dictionary mapping issue IDs to their matches
    """
    # Parse report
    parser = default_registry.get_parser(report_path)
    if not parser:
        raise ValueError(f"No parser available for report: {report_path}")
    
    issues = parser.parse(report_path)
    
    # Find matches for each issue
    matcher = IssueMatcher(vector_store, threshold, top_k)
    results = {}
    
    for issue in issues:
        matches = matcher.find_matches(issue)
        results[issue.id] = matches
    
    return results
```

## Output Report Generation

### Annotated HTML Report

The output report preserves the original structure but adds deduplication annotations:

```python
class AnnotatedReportGenerator:
    """Generates HTML report with deduplication annotations."""
    
    def generate(
        self,
        original_report_path: str,
        dedupe_results: Dict[str, List[DedupeMatch]],
        output_path: str
    ):
        """
        Generate annotated report.
        
        Args:
            original_report_path: Path to original HTML report
            dedupe_results: Mapping of issue IDs to matches
            output_path: Path for output HTML file
        """
        # Load original HTML
        with open(original_report_path, 'r') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')
        
        # Add CSS for annotations
        self._inject_styles(soup)
        
        # Add annotations to each issue
        for issue_id, matches in dedupe_results.items():
            issue_elem = soup.find(id=issue_id)
            if issue_elem and matches:
                annotation = self._create_annotation(matches)
                issue_elem.insert(0, annotation)
        
        # Add summary section
        summary = self._create_summary(dedupe_results)
        soup.body.insert(0, summary)
        
        # Write output
        with open(output_path, 'w') as f:
            f.write(str(soup))
```

### Annotation HTML Structure

```html
<!-- Injected annotation for an issue with matches -->
<div class="dedupe-annotation">
    <div class="dedupe-header">
        <span class="dedupe-icon">⚠️</span>
        <span class="dedupe-title">Potential Duplicates Found</span>
    </div>
    <ul class="dedupe-matches">
        <li class="dedupe-match high-similarity">
            <a href="rdar://123456789" class="issue-link">rdar://123456789</a>
            <span class="match-title">Memory leak in CLLocationManager</span>
            <span class="match-score">92% match</span>
            <span class="match-reason">Very high similarity - likely duplicate</span>
        </li>
        <li class="dedupe-match moderate-similarity">
            <a href="rdar://987654321" class="issue-link">rdar://987654321</a>
            <span class="match-title">CLLocationManager retains delegate</span>
            <span class="match-score">78% match</span>
            <span class="match-reason">Moderate similarity - possible duplicate</span>
        </li>
    </ul>
</div>
```

### Summary Section

```html
<!-- Summary section at top of report -->
<div class="dedupe-summary">
    <h2>Deduplication Summary</h2>
    <div class="summary-stats">
        <div class="stat">
            <span class="stat-value">15</span>
            <span class="stat-label">Total Issues</span>
        </div>
        <div class="stat">
            <span class="stat-value">8</span>
            <span class="stat-label">With Potential Duplicates</span>
        </div>
        <div class="stat">
            <span class="stat-value">3</span>
            <span class="stat-label">High Confidence Matches</span>
        </div>
    </div>
    <p class="summary-note">
        Issues with potential duplicates are marked with ⚠️. 
        Review matches before filing new issues.
    </p>
</div>
```

## Implementation Phases

### Phase 1: Core Infrastructure (2-3 days)
1. Create directory structure
2. Implement configuration and constants
3. Set up CLI with argparse
4. Implement data models (Issue, IssueEntry, DedupeMatch)

### Phase 2: Vector Database (3-4 days)
1. Implement ChromaDB wrapper (`vector_db/store.py`)
2. Implement embedding generation (`vector_db/embeddings.py`)
3. Implement issue ingestion with deduplication (`vector_db/ingestion.py`)
4. Add issue markdown parser (`parsers/issue_parser.py`)

### Phase 3: Report Parsing (2-3 days)
1. Implement base parser class (`parsers/base_parser.py`)
2. Implement LLM static analyzer parser (`parsers/html_report_parser.py`)
3. Create parser registry
4. Add support for file:// URLs

### Phase 4: Deduplication Logic (2-3 days)
1. Implement matcher (`deduper/matcher.py`)
2. Add batch processing
3. Implement match reason generation
4. Add threshold and top-k configuration

### Phase 5: Report Generation (2-3 days)
1. Implement annotated report generator (`report/html_generator.py`)
2. Add CSS styles for annotations
3. Implement summary section
4. Add output file handling

### Phase 6: Testing & Polish (2-3 days)
1. Unit tests for each component
2. Integration tests
3. Documentation
4. Error handling improvements

## Dependencies

### Python Packages
```
# requirements.txt additions
chromadb>=0.4.0           # Vector database
sentence-transformers>=2.2.0  # Embeddings
beautifulsoup4>=4.12.0    # HTML parsing
lxml>=4.9.0               # HTML parser backend
```

### Existing Dependencies
- `tqdm` - Progress bars (already in issue_tracking_deduper)
- `pathlib` - Path handling (stdlib)
- `hashlib` - Content hashing (stdlib)
- `json` - JSON handling (stdlib)

## Configuration

### Default Configuration
```python
# config.py
from pathlib import Path

# Vector DB settings
DEFAULT_DB_PATH = Path.home() / ".llm" / "issue_vectors"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
COLLECTION_NAME = "issue_embeddings"

# Matching settings
DEFAULT_THRESHOLD = 0.75
DEFAULT_TOP_K = 5

# Issue directory
DEFAULT_ISSUE_DIR = Path.home() / "issues_on_file"
```

### Environment Variables
```bash
ISSUE_DEDUPER_DB_PATH=~/.llm/issue_vectors
ISSUE_DEDUPER_THRESHOLD=0.75
ISSUE_DEDUPER_TOP_K=5
```

## Error Handling

### Common Errors
1. **Missing issue directory**: Clear error message with path
2. **Invalid HTML report**: Graceful fallback with warning
3. **Vector DB not initialized**: Prompt to run ingest first
4. **Network errors for file:// URLs**: Handle local file access

### Logging
```python
import logging

logger = logging.getLogger("issue_tracking_deduper")
logger.setLevel(logging.INFO)

# Log levels:
# DEBUG: Detailed processing info
# INFO: Progress updates
# WARNING: Non-fatal issues
# ERROR: Fatal errors
```

## Future Enhancements

1. **Additional Report Formats**: Add parsers for other LLM analyzer outputs
2. **Incremental Ingestion**: Only process new/modified issue files
3. **Web Interface**: Simple Flask/FastAPI UI for interactive deduplication
4. **Issue API Integration**: Fetch issue descriptions directly from Issue API
5. **Confidence Calibration**: Learn from user feedback to improve matching
6. **Batch Report Processing**: Process multiple reports at once
7. **Export to CSV/JSON**: Export dedupe results in different formats

## Testing Plan

### Unit Tests
```python
# tests/test_issue_parser.py
def test_parse_issue_markdown():
    """Test parsing issue markdown files."""
    pass

# tests/test_html_parser.py
def test_parse_llm_report():
    """Test parsing LLM static analyzer reports."""
    pass

# tests/test_vector_store.py
def test_ingest_and_query():
    """Test vector DB operations."""
    pass

# tests/test_matcher.py
def test_find_matches():
    """Test deduplication matching."""
    pass
```

### Integration Tests
```python
# tests/test_integration.py
def test_full_pipeline():
    """Test complete ingest -> dedupe -> report pipeline."""
    pass
```

## Timeline Estimate

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase 1: Core Infrastructure | 2-3 days | None |
| Phase 2: Vector Database | 3-4 days | Phase 1 |
| Phase 3: Report Parsing | 2-3 days | Phase 1 |
| Phase 4: Deduplication Logic | 2-3 days | Phase 2, 3 |
| Phase 5: Report Generation | 2-3 days | Phase 4 |
| Phase 6: Testing & Polish | 2-3 days | Phase 5 |

**Total Estimated Time: 13-19 days**

## References

- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Sentence Transformers](https://www.sbert.net/)
- [BeautifulSoup Documentation](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)
- Existing `issue_helper.py` for issue markdown format
- Existing `issue_dupe_scrape.py` for issue deduplication patterns

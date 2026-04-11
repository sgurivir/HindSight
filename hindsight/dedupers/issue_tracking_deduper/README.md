# Issue Scripts

Tools for working with Bug management bug tracking system, including downloading issues and identifying potential duplicates in static analyzer reports.

## Overview

This package provides two main capabilities:

1. **Issue Downloading** - Download issue descriptions from Bug management system to local markdown files
2. **Issue Deduplication** - Identify potential duplicate issues in LLM static analyzer reports using **hybrid matching**

## Hybrid Deduplication

The deduplication system uses a **multi-signal hybrid scoring** approach that combines three signals:

| Signal | Weight | Description |
|--------|--------|-------------|
| **File Path** | 40% | Strongest signal - same file = likely same bug |
| **Function Name** | 30% | Strong signal - same function = related issues |
| **Semantic Similarity** | 30% | Cosine similarity for description matching |

This hybrid approach provides:
- **Higher Precision**: File path matching reduces false positives from semantically similar but unrelated issues
- **Higher Recall**: Function name matching catches duplicates with different descriptions
- **Transparency**: Match reasons explain why each duplicate was identified

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Hybrid Deduplication Pipeline                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │   Download   │───▶│    Ingest    │───▶│   Hybrid     │───▶│  Output   │ │
│  │   Issues     │    │   to VectorDB│    │   Matching   │    │   HTML    │ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └───────────┘ │
│         │                   │                   │                   │       │
│         ▼                   ▼                   ▼                   ▼       │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │ Radar API    │    │ ChromaDB +   │    │ File Path +  │    │ Annotated │ │
│  │ (radarclient)│    │ File/Func    │    │ Function +   │    │ Report    │ │
│  │              │    │ Extraction   │    │ Cosine Sim   │    │           │ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └───────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Issue Downloading (`issue_helper.py`, `issue_downloader.py`)

Downloads issue descriptions from Bug management system using the `radarclient` library.

**How it works:**
- Authenticates with Bug management API using credentials
- Searches for issues by keyword (default: "Lomo Perf Found by AI Static Analysis")
- Downloads issue metadata and descriptions to markdown files
- Skips issues that already exist locally (incremental download)

**Output format:** Each issue is saved as a markdown file:
```
~/issues_on_file/rdar_123456789_Issue_Title.md
```

### 2. Vector Database (`issue_tracking_deduper/vector_db/`)

Uses ChromaDB to store issue embeddings for semantic similarity search.

**How it works:**
1. **Embedding Generation** - Uses `sentence-transformers/all-MiniLM-L6-v2` model to convert issue text into 384-dimensional vectors
2. **Storage** - Stores embeddings in ChromaDB with metadata (issue ID, title, component, keywords)
3. **Similarity Search** - Finds similar issues using cosine similarity

**Database location:** `~/.llm/issue_vectors/`

#### Cosine Similarity Implementation

The cosine similarity calculation spans three key components:

| Component | File | Role |
|-----------|------|------|
| `sentence-transformers` | `embeddings.py` | Converts text → embedding vectors |
| ChromaDB with `hnsw:space: cosine` | `store.py` | Stores vectors, performs approximate nearest neighbor search using cosine distance |
| `_distance_to_similarity()` | `hybrid_matcher.py:229` | Converts distance (0-2) → similarity (0-1) |
| Hybrid weights | `config.py` | Cosine similarity contributes 30% to final match score |

**Step-by-step process:**

1. **Embedding Generation** (`embeddings.py`):
   ```python
   from sentence_transformers import SentenceTransformer
   self._model = SentenceTransformer(self.model_name)
   embedding = self.model.encode(text, convert_to_numpy=True)
   ```

2. **Vector Storage with Cosine Distance** (`store.py`):
   ```python
   self._collection = self.client.get_or_create_collection(
       name=self.collection_name,
       metadata={"hnsw:space": "cosine"}  # Use cosine similarity
   )
   ```

3. **Distance to Similarity Conversion** (`hybrid_matcher.py`):
   ```python
   def _distance_to_similarity(self, distance: float) -> float:
       # Cosine distance: 0 = identical, 2 = opposite
       # Similarity: 1 = identical, 0 = orthogonal
       return max(0.0, 1 - (distance / 2))
   ```

4. **Hybrid Scoring** (`hybrid_matcher.py`):
   ```python
   hybrid_score = (
       self.weights['file_path'] * file_score +
       self.weights['function_name'] * func_score +
       self.weights['cosine_similarity'] * cosine_score  # 30% weight
   )
   ```

### 3. Hybrid Deduplication (`issue_tracking_deduper/deduper/`)

Matches issues from static analyzer reports against the issue vector database using hybrid scoring.

**How it works:**
1. **Parse Report** - Extracts issues from HTML report (supports StaticIntelligence and Repo IQ formats)
2. **Generate Embeddings** - Creates embeddings for each issue's title and description
3. **Semantic Search** - Queries vector database for semantically similar issues (3x top-k candidates)
4. **File Path Matching** - Computes file path similarity scores for each candidate
5. **Function Name Matching** - Computes function name similarity scores for each candidate
6. **Hybrid Scoring** - Combines all three signals using weighted average
7. **Confidence Scoring** - Classifies matches as very_high (>85%), high (70-85%), moderate (55-70%), or low (<55%)

**Matching Components:**
- `FilePathMatcher` - Scores file path similarity (exact: 1.0, same filename: 0.8+, same directory: 0.4)
- `FunctionNameMatcher` - Scores function name similarity with Objective-C normalization
- `FilePathExtractor` - Extracts file paths from issue descriptions
- `FunctionNameExtractor` - Extracts function names from issue descriptions

### 4. HTML Report Generation (`issue_tracking_deduper/report/`)

Generates annotated HTML reports showing potential duplicates.

**Features:**
- Preserves original report format
- Highlights issues with potential duplicates in yellow
- Shows clickable `rdar://` links that open in the local Radar app
- Displays confidence scores and issue descriptions

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install radarclient (Apple internal)
pip install -i https://pypi.apple.com/simple radarclient
```

## Usage

### Full Pipeline (Recommended)

Run the complete pipeline: download → ingest → dedupe

```bash
# Run with default keyword ("Lomo Perf Found by AI Static Analysis")
python3 -m issue_tracking_deduper run \
    --issue-dir ~/issues_on_file \
    --report ~/Desktop/static_analysis_report.html \
    --output ~/Desktop/report_deduped.html

# Run with custom issue keyword
python3 -m issue_tracking_deduper run \
    --issue-dir ~/issues_on_file \
    --report ~/Desktop/static_analysis_report.html \
    --issue-keyword "memory leak"
```

### Individual Commands

#### Download Issues Only

```bash
# Download issues with default keyword
python3 issue_downloader.py

# Download issues with custom keyword
python3 issue_downloader.py -k "memory leak"

# Download to custom directory
python3 issue_downloader.py -o ./my_issues
```

#### Ingest Issues into Vector Database

```bash
python3 -m issue_tracking_deduper ingest \
    --issue-dir ~/issues_on_file
```

#### Find Duplicates in Report

```bash
python3 -m issue_tracking_deduper dedupe \
    --report ~/Desktop/report.html \
    --output ~/Desktop/report_deduped.html \
    --threshold 0.75 \
    --top-k 5
```

## Command Line Options

### `run` Command (Full Pipeline)

| Option | Default | Description |
|--------|---------|-------------|
| `--issue-dir` | `~/issues_on_file` | Directory for issue markdown files |
| `--report` | (required) | Path to HTML report |
| `--output` | `<input>_deduped.html` | Output path for annotated report |
| `--issue-keyword` | `"Lomo Perf Found by AI Static Analysis"` | Keyword to search for issues to download |
| `--db-path` | `~/.llm/issue_vectors` | Vector database path |
| `--threshold` | `0.75` | Hybrid similarity threshold (0.0-1.0) |
| `--top-k` | `5` | Max matches per issue |
| `-v, --verbose` | `false` | Enable debug logging |

### `ingest` Command

| Option | Default | Description |
|--------|---------|-------------|
| `--issue-dir` | `~/issues_on_file` | Directory containing issue markdown files |
| `--db-path` | `~/.llm/issue_vectors` | Vector database path |

### `dedupe` Command

| Option | Default | Description |
|--------|---------|-------------|
| `--report` | (required) | Path to HTML report |
| `--output` | `<input>_deduped.html` | Output path |
| `--threshold` | `0.75` | Hybrid similarity threshold |
| `--top-k` | `5` | Max matches per issue |

## Environment Variables

You can customize hybrid scoring weights via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ISSUE_DEDUPER_FILE_WEIGHT` | `0.40` | Weight for file path matching |
| `ISSUE_DEDUPER_FUNC_WEIGHT` | `0.30` | Weight for function name matching |
| `ISSUE_DEDUPER_COSINE_WEIGHT` | `0.30` | Weight for cosine similarity |
| `ISSUE_DEDUPER_HYBRID_THRESHOLD` | `0.50` | Minimum hybrid score for matches |
| `ISSUE_DEDUPER_THRESHOLD` | `0.75` | Default similarity threshold |
| `ISSUE_DEDUPER_TOP_K` | `5` | Default top-k results |
| `ISSUE_DEDUPER_DB_PATH` | `~/.llm/issue_vectors` | Vector database path |
| `ISSUE_DEDUPER_ISSUE_DIR` | `~/issues_on_file` | Default issue directory |

## Example Output

The annotated HTML report includes:

1. **Summary Banner** - Shows total issues with matches and confidence breakdown
2. **Highlighted Issues** - Issues with potential duplicates have yellow background
3. **Match Details** - For each match:
   - Clickable `rdar://` link
   - Hybrid score percentage
   - Individual scores (file, function, cosine)
   - Match reasons explaining why it was identified
   - Issue description preview

### Console Output (Verbose Mode)

```
📋 Issue: Linear search creates O(n²) complexity...
   File: CLMicroLocationSemiSupervisedAlgorithm.mm
   Function: -[CLMicroLocationSemiSupervisedAlgorithm processLocationUpdate:]
   Severity: high
   Potential duplicates:
     🔴 rdar://169023877 (hybrid: 90%)
        📁 file: 100% | 🔧 func: 85% | 📝 cosine: 78%
        📁 Same file: CLMicroLocationSemiSupervisedAlgorithm.mm
        🔧 Same function: processLocationUpdate
        📝 High semantic similarity (78%)
        Linear search through unLabeledRecordingTriggers...
```

### HTML Report

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🔍 Deduplication Analysis (Hybrid Matching)                                 │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                            │
│ │   236   │ │   50    │ │   100   │ │   86    │                            │
│ │ Matches │ │Very High│ │  High   │ │Moderate │                            │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘                            │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ ⚠️ Issue: Linear search creates O(n²) complexity                           │
│ File: CLMicroLocationSemiSupervisedAlgorithm.mm                            │
│ Function: processLocationUpdate                                             │
│                                                                             │
│ 🔴 Likely Duplicate Found (hybrid: 90%)                                    │
│ ┌─────────────────────────────────────────────────────────────────────────┐│
│ │ rdar://169023877                                                        ││
│ │ 📁 file: 100% | 🔧 func: 85% | 📝 cosine: 78%                          ││
│ │ Match reasons:                                                          ││
│ │   📁 Same file: CLMicroLocationSemiSupervisedAlgorithm.mm              ││
│ │   🔧 Same function: processLocationUpdate                               ││
│ │ Linear search through unLabeledRecordingTriggers...                     ││
│ └─────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
```

## Supported Report Formats

The parser supports two HTML report formats:

### StaticIntelligence Format
```javascript
const issuesData = {
  "issues": [{
    "issue": "...",
    "file": "path/to/file.mm",
    "functionName": "functionName",
    "line": "136",
    "potentialSolution": "...",
    "validationReasoning": "..."
  }]
}
```

### Repo IQ Format
```javascript
const issuesData = {
  "issues": [{
    "issue": "...",
    "file_path": "path/to/file.mm",
    "function_name": "functionName",
    "line_number": "136",
    "suggestion": "...",
    "evidence": "..."
  }]
}
```

## File Structure

```
issue_tracking_deduper/
├── README.md                    # This file
├── requirements.txt             # Python dependencies
├── issue_helper.py              # Issue API utilities
├── issue_downloader.py          # Standalone issue downloader
└── issue_tracking_deduper/
    ├── __init__.py
    ├── __main__.py              # Entry point
    ├── cli.py                   # Command-line interface
    ├── config.py                # Configuration (including hybrid weights)
    ├── deduper/
    │   ├── issue.py             # Data models (Issue, IssueEntry, HybridMatch)
    │   ├── matcher.py           # Legacy cosine-only matcher
    │   ├── matching.py          # File/function matchers and extractors
    │   └── hybrid_matcher.py    # Hybrid matching logic
    ├── parsers/
    │   ├── base_parser.py       # Parser interface
    │   ├── html_report_parser.py # HTML report parser
    │   └── issue_parser.py      # Issue markdown parser
    ├── report/
    │   └── html_generator.py    # Annotated report generator
    └── vector_db/
        ├── embeddings.py        # Embedding generation
        ├── ingestion.py         # Issue ingestion (with file/func extraction)
        └── store.py             # ChromaDB wrapper
```

## Dependencies

- `radarclient` - Bug management API client (internal)
- `chromadb` - Vector database
- `sentence-transformers` - Embedding model
- `beautifulsoup4` - HTML parsing
- `tqdm` - Progress bars

## Troubleshooting

### "radarclient not installed"
```bash
pip install -i https://pypi.apple.com/simple radarclient
```

### "Vector database not found"
Run the ingest command first:
```bash
python3 -m issue_tracking_deduper ingest --issue-dir ~/issues_on_file
```

### "ChromaDB error with Python 3.14"
Use Python 3.11:
```bash
/opt/homebrew/opt/python@3.11/bin/python3.11 -m issue_tracking_deduper run ...
```

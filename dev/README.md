# Hindsight Development Tools

This directory contains development utilities, scripts, and documentation for the Hindsight code analysis system.

## Quick Start Commands

### Generate Config File for a Repository

Generate a configuration file for analyzing a repository:

```bash
python3 -m dev.generate_repo_config --repo ~/src/my-project --output <path>
```

### Run Code Analysis

Analyze a repository for code issues using LLM-based analysis:

```bash
# Example with coretime repository
python3 -m hindsight.analyzers.code_analyzer \
    --config ./hindsight/example_configs/repo_analysis/coretime.json \
    --repo ~/src/coretime/

# Example with safari repository
python3 -m hindsight.analyzers.code_analyzer \
    --repo ~/src/safari/ \
    --config hindsight/example_configs/repo_analysis/safari.json
```

### Generate AST (Abstract Syntax Tree)

Generate AST call graphs for a repository:

```bash
python3 -m hindsight.core.lang_util.ast_util \
    --repo ~/src/corelocation \
    --out-dir ~/Desktop/cl_ast_artifacts/ \
    --exclude .git Tools Tests Test ProtobufDefs Protobuf External Plugins proto \
             ci_scripts TestPlans test-files hws-log Oscar LogFormatters xcconfig \
             Resources en.lproj CardioHealthTests CoreLocation.xcodeproj vagrant \
             regressiontests .workflow
```

### Run Diff Analyzer

Analyze differences between two commits:

```bash
python3 -m hindsight.diff_analyzers.git_simple_diff_analyzer \
    --repo ~/third_party/coreboot/ \
    --config ./hindsight/example_configs/comparative_analysis/tesla-coreboot-claude.json \
    --c1 ea193411417dda86f90d89e51dc93367ebee9ab2 \
    --c2 f050028525e0823b39ed0912a99523b7ab55ba02 \
    --out_dir /tmp/
```

### Run Data Flow Analyzer

Analyze data flow patterns in a repository:

```bash
python3 -m hindsight.analyzers.data_flow_analyzer \
    --config ./hindsight/example_configs/repo_analysis/loc.json \
    --repo ~/src/corelocation/
```

### Move False Positives

Move identified false positive issues to a separate directory for audit:

```bash
# Preview changes (dry run)
python3 dev/move_false_positives.py \
    --results-dir ~/llm_artifacts/<repo>/results/code_analysis \
    --false-positives-file /path/to/false_positives.csv \
    --dry-run

# Execute the move
python3 dev/move_false_positives.py \
    --results-dir ~/llm_artifacts/<repo>/results/code_analysis \
    --false-positives-file /path/to/false_positives.csv
```

The CSV file format for false positives:
```csv
checksum,issue_title,issue_description
880f92c1,"GetLastError() called twice","GetLastError() called twice, potentially losing error"
```

See [`docs/examples/false_positives.csv`](../docs/examples/false_positives.csv) for a complete example.

## Directory Structure

```
dev/
├── README.md                           # This file
├── move_false_positives.py             # Script to move false positive issues
├── generate_repo_config.py             # Generate config files for repositories
├── FALSE_POSITIVE_MANAGEMENT_DESIGN.md # Design doc for false positive management
├── prompts/                            # Prompts for Roo/LLM analysis
│   └── prompt_for_roo_to_analyze_si.txt
├── ast_inspection/                     # AST inspection utilities
├── hotspots/                           # Hotspot analysis tools
└── profiling/                          # Performance profiling tools
```

## Related Documentation

- [False Positive Management Design](FALSE_POSITIVE_MANAGEMENT_DESIGN.md) - Design document for the false positive management feature
- [Example Configs](../hindsight/example_configs/) - Example configuration files for various analysis types

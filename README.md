# Hindsight

LLM-powered static analysis tool for finding bugs and performance issues in Swift, Objective-C, and other codebases.

## Installation

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Python dependencies

```bash
python3 -m pip install -r requirements.txt
```

## Usage

### Generate a config file for a repo

```bash
python3 -m dev.generate_repo_config --repo ~/src/my-project --output <path>
```

### Run code analysis

```bash
python3 -m hindsight.analyzers.code_analyzer \
  --config ./hindsight/example_configs/repo_analysis/some_repo.json \
  --repo ~/src/some_repo/
```

### Download hotspots

```bash
python3 dev/hotspots/download_hotspot.py \
  --daemon some_daemon \
  --dataset "Some_Seed_3_(23T5558e)" \
  --device N210 \
  --output-dir ~/bugs/hotspots/some_repo/
```

### Run trace analysis (hotspot processing)

```bash
python3 -m hindsight.analyzers.trace_analyzer \
  --config ./hindsight/example_configs/hotspot_analysis/example.json \
  --repo ~/src/some_repo \
  --hotspot ~/bugs/hotspots/some_repo/20260507_220026_some_daemon_hotspot_data.json
```

### Run diff analysis

```bash
python3 -m hindsight.diff_analyzers.git_simple_diff_analyzer \
  --repo ~/src/some_repo \
  --config ./hindsight/example_configs/repo_analysis/some_repo.json \
  --c1 eae7b0b8878a46739a11e6113dbcf6da3efcc7e6 \
  --c2 8e87ed4b8f39d6fd9cd1b3bd2b609d9a4849ed26 \
  --out_dir /tmp/
```

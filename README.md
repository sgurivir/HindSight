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

### 3. Install radarclient (Apple internal)

`radarclient` is hosted on Apple's internal PyPI and must be installed separately:

```bash
pip install -i https://pypi.apple.com/simple radarclient
```

> **Note:** Do not use `--user` when installing inside a virtual environment.

## Usage

### Generate a config file for a repo

```bash
python3 -m dev.generate_repo_config --repo ~/src/my-project --output <path>
```

### Run code analysis

```bash
python3 -m hindsight.analyzers.code_analyzer \
  --config ./hindsight/example_configs/repo_analysis/coretime.json \
  --repo ~/src/coretime/
```

### Download hotspots

```bash
python3 dev/hotspots/download_hotspot.py -o ~/bugs/hotspots/napiliF/
```

```bash
python3 -m staticintelligence.plugins.aggregatedMicroStackShot.service.SpinDistillAPI \
  --dataset "NapiliF_Seed_3_(23T5558e)" \
  --process "locationd" \
  --device "N210" \
  --context-filter "Unplugged" \
  --country-code "All" \
  --slice-type "Overall" \
  --output-dir ~/bugs/hotspots/napiliF
```

### Run trace analysis (hotspot processing)

```bash
python3 -m hindsight.analyzers.trace_analyzer \
  --config ./hindsight/example_configs/hotspot_analysis/example.json \
  --repo ~/src/corelocation \
  --hotspot ~/bugs/hotspots/napiliF/20260507_220026_locationd_hotspot_data.json
```

### Run diff analysis

```bash
python3 -m hindsight.diff_analyzers.git_simple_diff_analyzer \
  --repo ~/src/corelocation \
  --config ./hindsight/example_configs/repo_analysis/loc.json \
  --c1 eae7b0b8878a46739a11e6113dbcf6da3efcc7e6 \
  --c2 8e87ed4b8f39d6fd9cd1b3bd2b609d9a4849ed26 \
  --out_dir /tmp/
```

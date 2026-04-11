#!/usr/bin/env python3
"""
Profiling script for Hindsight Code Analyzer

This script allows you to run the code analyzer with memory profiling tools like memray.

Usage:
    # Basic profiling with memray
    memray run -o memray.bin dev/profiling/run_ca.py --repo ~/third_party/opencv/opencv/ --config ./hindsight/example_configs/repo_analysis/opencv.json --num-functions-to-analyze 10

    # Using cProfile
    python3 -m cProfile -o profile.stats dev/profiling/run_ca.py --repo ~/third_party/opencv --config ./hindsight/example_configs/repo_analysis/loc.json --num-functions-to-analyze 1

    # Run without profiling (for testing)
    python dev/profiling/run_ca.py --repo ~/third_party/opencv/opencv/ --config ./hindsight/example_configs/repo_analysis/opencv.json --num-functions-to-analyze 10
"""

import sys
import os
from pathlib import Path

# Add the project root to Python path so we can import hindsight
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.analyzers.code_analyzer import main

if __name__ == "__main__":
    main()

# Create updated checkbox-style restartable agent prompt
# with automatic storage root: ~/.roo_analysys/<repo_dir_name>/

import argparse
import os

# Parse command line arguments
parser = argparse.ArgumentParser(
    description="Generate a restartable CPU analysis agent prompt for a repository"
)
parser.add_argument(
    "repo_path",
    type=str,
    help="Path to the repository to analyze"
)
parser.add_argument(
    "functions_file",
    type=str,
    help="Path to file containing list of functions to analyze"
)
args = parser.parse_args()

# Get repo_dir_name from the basename of the provided path
repo_dir_name = os.path.basename(os.path.normpath(args.repo_path))

prompt = f"""
You are operating as a restartable analysis agent.

PRIMARY INPUT:
Repository path: {args.repo_path}
Functions file: {args.functions_file}

All generated files MUST be stored under:

~/.roo_analysys/{repo_dir_name}/

Where:
<repo_dir_name> = {repo_dir_name}

Create directory if missing.
Never write outside this directory.

============================================================
DIRECTORY STRUCTURE (MANDATORY)
============================================================

~/.roo_analysys/{repo_dir_name}/
    dataset_state.txt
    analysys_progress.txt
    memory.txt
    memory_index.txt
    notes.txt
    optimizations_identified.txt
    logs/
    archives/

Archive old progress files into:
~/.roo_analysys/{repo_dir_name}/archives/

============================================================
FUNCTION TRACKING FORMAT (VISIBLE CHECKBOX STYLE)
============================================================

[ ] <func_name>  -> Not started
[P] <func_name>  -> In progress
[D] <func_name>  -> Done (no optimization)
[O] <func_name>  -> Optimization found
[E] <func_name>  -> Error

Exactly one letter inside brackets.

============================================================
RUN / DATASET VERSIONING
============================================================

DATASET_SIG = (byte_size, first_5_lines, last_5_lines)

On startup:
1) Compute DATASET_SIG
2) Compare with dataset_state.txt inside repo directory
3) If different:
   - Move analysys_progress.txt to archives/
   - Start fresh analysys_progress.txt
   - DO NOT delete memory.txt
   - DO NOT delete notes.txt
   - DO NOT delete optimizations_identified.txt
   - Append DATASET_RESET entry to notes.txt
4) Overwrite dataset_state.txt with new signature

============================================================
CRITICAL FILE PROTECTION
============================================================

optimizations_identified.txt is STRICTLY append-only.
memory.txt and notes.txt are append-only.

Never truncate.
Never rewrite.
Never clear.

If file size shrinks -> ABORT.

============================================================
STATE FILE FORMATS
============================================================

analysys_progress.txt (append-only)
STATUS|<func>|<file>|<line>|<timestamp>|<0/1 opt_found>

memory.txt (append-only)
Max 30 lines per entry.

optimizations_identified.txt (append-only blocks)
FUNC:
FILE:
LINE:
REC:
WHY:
RISK:
DATASET_SIG:
----

============================================================
PER-FUNCTION ALGORITHM
============================================================

1) Check analysys_progress.txt
2) Skip if already [D] or [O]
3) Mark as [P]
4) Analyze function only
5) Identify up to 3 micro-optimizations
6) Append optimization block (if any)
7) Update memory
8) Mark final status:
   - [O] if optimization found
   - [D] if none
   - [E] if error

============================================================
OUTPUT RULES (CHAT)
============================================================

For each function:

[<status>] <function_name>
Optimization: Yes/No
Tiny before/after snippet only

Never paste full function.
Never print full memory.

============================================================
BEGIN
============================================================

Ensure directory exists.
Run dataset signature check.
Process first function.
"""

output_path = "restartable_cpu_analysis_agent_prompt_checkbox_with_rootdir.txt"

with open(output_path, 'w') as f:
    f.write(prompt)

print(f"Prompt written to: {output_path}")
print()
print("=" * 60)
print(prompt)
print("=" * 60)

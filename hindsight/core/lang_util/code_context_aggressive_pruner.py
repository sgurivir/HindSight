import argparse
import re
import sys
from pathlib import Path

class CodeCommentStripper:
    """
    Strip // and /* */ comments while preserving line count.
    - Keeps all newlines intact to maintain original line numbers.
    - Replaces commented characters with spaces.
    - Supports nested block comments (Swift-style).
    - Respects string/char literals and escapes.
    """
    @staticmethod
    def strip_preserve_lines(text: str) -> str:
        i, n = 0, len(text)
        out = []
        in_line = False
        block_depth = 0
        in_string = False
        string_quote = ''
        escape = False

        def put(ch, replace_space=False):
            out.append(' ' if replace_space else ch)

        while i < n:
            ch = text[i]
            nxt = text[i+1] if i + 1 < n else ''

            # Always preserve newlines exactly
            if ch == '\n':
                out.append('\n')
                in_line = False
                escape = False
                i += 1
                continue

            # Inside a string literal
            if in_string and block_depth == 0 and not in_line:
                out.append(ch)
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == string_quote:
                    in_string = False
                    string_quote = ''
                i += 1
                continue

            # Inside a line comment: turn everything into spaces (except newlines handled above)
            if in_line:
                put(ch, replace_space=True)
                i += 1
                continue

            # Inside a (possibly nested) block comment
            if block_depth > 0:
                if ch == '/' and nxt == '*':
                    block_depth += 1
                    put(ch, True); put(nxt, True)
                    i += 2
                    continue
                if ch == '*' and nxt == '/':
                    block_depth -= 1
                    put(ch, True); put(nxt, True)
                    i += 2
                    continue
                # Preserve structural whitespace columns roughly
                put(ch, replace_space=(ch != '\t' and ch != '\r'))
                i += 1
                continue

            # Not in comment/string: detect comment starts
            if ch == '/' and nxt == '/':
                # Start line comment
                put(ch, True); put(nxt, True)
                in_line = True
                i += 2
                continue
            if ch == '/' and nxt == '*':
                # Start block comment (support nesting)
                put(ch, True); put(nxt, True)
                block_depth = 1
                i += 2
                continue

            # Detect string/char literal start
            if ch in ('"', "'"):
                in_string = True
                string_quote = ch
                out.append(ch)
                i += 1
                continue

            # Normal code
            out.append(ch)
            i += 1

        return ''.join(out)


class CodeContextAggressivePruner:
    """
    Aggressively prune function/method bodies to empty, preserving:
      - Original line count (blank out body lines)
      - Function signature lines
      - Opening and closing brace lines

    Works for C/C++/ObjC/Swift (heuristic, not a full parser).
    Assumes the input code is comment-free (use CodeCommentStripper first).
    Outputs lines prefixed with 'NNNN | ' where NNNN matches original line numbers.
    """

    _re_false_heads = re.compile(r'^\s*(if|for|while|switch|catch|else|do|defer|guard|case|default)\b')
    _re_swift_head  = re.compile(r'^\s*(?:@\w+(?:\([^)]*\))?\s*)*func\b')
    _re_objc_head   = re.compile(r'^\s*[-+]\s*\(.*\)\s*.*')  # - (Type)name..., + (Type)name...
    _re_c_like_head = re.compile(
        r"""
        ^
        (?:
          (?:template\s*<[^>]*>\s*)?             # optional template declaration
          (?:auto|[A-Za-z_~]\w*[\w:\s<>\*\&\[\]]*)?   # return/type qualifiers (including auto)
        )?
        \s*
        (?:[A-Za-z_~]\w*::)?                     # optional class scope
        (?:operator\s*[+\-*/=<>!&|^%~\[\]()]+|[A-Za-z_~]\w*)  # name or operator
        \s*
        \(
            [^()]*                               # params (single-line heuristic)
        \)
        (?:\s*(?:const|noexcept|override|final)\b.*)?   # trailing qualifiers
        (?:\s*->\s*[^{]*)?                       # optional trailing return type
        (?:\s*:\s*[^{]*)?                        # optional initializer list
        \s*
        (\{)?\s*$
        """,
        re.VERBOSE
    )

    @staticmethod
    def prune(code_without_comments: str) -> str:
        lines = code_without_comments.splitlines()
        n = len(lines)
        i = 0
        out = list(lines)  # start with exact copy; we will blank ranges

        while i < n:
            if CodeContextAggressivePruner._looks_like_function_start(lines, i):
                sig_end, body_start, body_end = CodeContextAggressivePruner._locate_function_block(lines, i)
                if body_start is not None and body_end is not None and body_end >= body_start:
                    # Blank interior lines strictly between the opening and closing brace lines
                    for k in range(body_start + 1, body_end):
                        out[k] = ''
                    # Keep signature lines and brace lines as-is
                    i = max(body_end + 1, i + 1)
                    continue
                else:
                    # Prototype or couldn't find braces; leave as-is
                    i = sig_end + 1 if sig_end is not None else i + 1
                    continue
            i += 1

        return CodeContextAggressivePruner._with_line_numbers(out)

    # ---------- helpers ----------
    @staticmethod
    def get_character_count(repo_path: str, relative_path: str):
        """
        Return number of characters in a file.
        Tries both absolute and repo-relative paths.
        Returns error text if file is not found or cannot be read.
        """
        base = Path(repo_path).resolve()
        candidate = Path(relative_path).expanduser()

        # Determine actual file path
        if candidate.is_absolute() and candidate.exists():
            file_path = candidate
        else:
            file_path = (base / relative_path).resolve()
            if not file_path.exists():
                return f"File not found:\nrepo_path :<{repo_path}>\nrelative_path:<{relative_path}>"

        if not file_path.is_file():
            return f"File not found:\nrepo_path :<{repo_path}>\nrelative_path:<{relative_path}>"

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return len(content)
        except Exception:
            return f"File not found:\nrepo_path :<{repo_path}>\nrelative_path:<{relative_path}>"

    @staticmethod
    def prune_file(repo_path: str, relative_path: str) -> str:
        """
        Read a file, strip comments, and prune function bodies.

        Args:
            repo_path: Base repository path
            relative_path: Relative path to the file within the repository

        Returns:
            Pruned code with line numbers as a string, or error text if file not found
        """
        base = Path(repo_path).resolve()
        candidate = Path(relative_path).expanduser()

        # Determine actual file path
        if candidate.is_absolute() and candidate.exists():
            file_path = candidate
        else:
            file_path = (base / relative_path).resolve()
            if not file_path.exists():
                return f"File not found:\nrepo_path :<{repo_path}>\nrelative_path:<{relative_path}>"

        if not file_path.is_file():
            return f"File not found:\nrepo_path :<{repo_path}>\nrelative_path:<{relative_path}>"

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                original_code = f.read()
        except Exception:
            return f"File not found:\nrepo_path :<{repo_path}>\nrelative_path:<{relative_path}>"

        # Strip comments first, then prune function bodies
        stripped = CodeCommentStripper.strip_preserve_lines(original_code)
        pruned = CodeContextAggressivePruner.prune(stripped)

        return pruned

    @staticmethod
    def _with_line_numbers(lines: list[str]) -> str:
        width = max(4, len(str(len(lines))))
        return '\n'.join(f"{str(idx+1).rjust(width)} | {lines[idx]}" for idx in range(len(lines)))

    @staticmethod
    def _looks_like_function_start(lines: list[str], idx: int) -> bool:
        s = lines[idx].strip()
        if not s:
            return False
        if CodeContextAggressivePruner._re_false_heads.match(s):
            return False
        if CodeContextAggressivePruner._re_swift_head.match(s):
            return True
        if CodeContextAggressivePruner._re_objc_head.match(s):
            return True
        # Try a few-line lookahead join for C/C++
        joined = s
        if CodeContextAggressivePruner._re_c_like_head.match(joined) and not s.endswith(';'):
            return True
        # If params split across lines, cheaply join a few
        for j in range(1, 6):  # Look ahead more lines for complex signatures
            if idx + j >= len(lines):
                break
            next_line = lines[idx + j].strip()
            joined += ' ' + next_line
            if CodeContextAggressivePruner._re_c_like_head.match(joined) and ';' not in joined:
                return True
            # Stop if we hit a line that looks like it's not part of the signature
            if next_line.endswith(';') or ('{' in next_line and '}' in next_line):
                break
        return False

    @staticmethod
    def _locate_function_block(lines: list[str], idx: int):
        """
        From a (heuristic) signature start at idx, return:
          (sig_end_idx, open_brace_idx, close_brace_idx)
        If no braces found (prototype), returns (sig_end_idx, None, None).
        """
        i = idx
        sig_end = idx
        # Crawl until we see an opening brace '{' that starts a body
        open_idx = None

        # Helper to strip quoted regions to avoid counting braces in strings
        def strip_quotes(s: str) -> str:
            return re.sub(r'".*?"|\'.*?\'', '', s)

        # Extend signature up to either '{' or ';'
        while i < len(lines):
            cur = lines[i]
            sig_end = i
            s = strip_quotes(cur)

            if '{' in s:
                open_idx = i if s.find('{') >= 0 else None
                break
            if s.strip().endswith(';'):
                # Prototype/forward decl
                return (sig_end, None, None)
            # If next line begins with '{' alone, treat that as open
            if i + 1 < len(lines) and lines[i+1].strip().startswith('{'):
                open_idx = i + 1
                sig_end = i + 1
                break
            i += 1

        if open_idx is None:
            return (sig_end, None, None)

        # Find matching close brace from open_idx
        depth = 0
        j = open_idx
        while j < len(lines):
            s = strip_quotes(lines[j])
            # Count all braces on this line (works for Swift/C/ObjC)
            for ch in s:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        # Matching close for the initial open was encountered.
                        return (sig_end, open_idx, j)
            j += 1

        # Unbalanced braces; treat as body spanning to EOF
        return (sig_end, open_idx, len(lines) - 1)


def process_single_file(input_file, output_file):
    """
    Process a single file and return original and pruned character counts.
    """
    with open(input_file, 'r', encoding='utf-8') as f:
        original_code = f.read()

    stripped = CodeCommentStripper.strip_preserve_lines(original_code)
    pruned = CodeContextAggressivePruner.prune(stripped)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(pruned)

    return len(original_code), len(pruned)


def main():
    """
    Main function for command-line usage of CodeContextAggressivePruner.
    Removes comments, prunes function bodies, and preserves line numbers.
    """
    parser = argparse.ArgumentParser(
        description="Aggressively prune code context while preserving line numbers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process single file
  python CodeContextAggressivePruner.py --file input.cpp --out output_pruned.txt

  # Process directory
  python CodeContextAggressivePruner.py --dir /path/to/source --out /path/to/output
        """
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--file', help='Input source file to process')
    group.add_argument('--dir', help='Input directory to process recursively')

    parser.add_argument('--out', required=True, help='Output file or directory for pruned content')

    args = parser.parse_args()

    try:
        if args.file:
            # Single file processing
            input_file = Path(args.file)
            if not input_file.exists() or not input_file.is_file():
                print(f"Error: Input file not found or invalid: {input_file}", file=sys.stderr)
                sys.exit(1)

            output_file = Path(args.out)
            orig_chars, pruned_chars = process_single_file(input_file, output_file)

            reduction = orig_chars - pruned_chars
            reduction_pct = (reduction / orig_chars * 100) if orig_chars > 0 else 0

            print(f"{input_file.name:<25}  BytesBefore: {orig_chars:>10,}  BytesAfter: {pruned_chars:>10,}  Reduction: {reduction:>10,}  ({reduction_pct:5.1f}%)")

        elif args.dir:
            # Directory processing
            input_dir = Path(args.dir)
            if not input_dir.exists() or not input_dir.is_dir():
                print(f"Error: Input directory not found or invalid: {input_dir}", file=sys.stderr)
                sys.exit(1)

            output_dir = Path(args.out)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Find all source files (common extensions)
            source_extensions = ['*.cpp', '*.c', '*.cc', '*.cxx', '*.h', '*.hpp', '*.hxx',
                               '*.swift', '*.m', '*.mm', '*.java', '*.kt', '*.cs']

            source_files = []
            for ext in source_extensions:
                source_files.extend(input_dir.rglob(ext))

            if not source_files:
                print(f"No source files found in directory: {input_dir}")
                sys.exit(1)

            total_orig = 0
            total_pruned = 0

            for input_file in sorted(source_files):
                # Maintain directory structure in output
                relative_path = input_file.relative_to(input_dir)
                output_file = output_dir / relative_path

                try:
                    orig_chars, pruned_chars = process_single_file(input_file, output_file)

                    reduction = orig_chars - pruned_chars
                    reduction_pct = (reduction / orig_chars * 100) if orig_chars > 0 else 0

                    print(f"{str(relative_path):<60} :  Count Before: {orig_chars:>8,}  Count After: {pruned_chars:>8,}  Reduction: {reduction:>8,}  ({reduction_pct:5.1f}%)")

                    total_orig += orig_chars
                    total_pruned += pruned_chars

                except Exception as e:
                    print(f"Error processing {input_file}: {e}", file=sys.stderr)
                    continue

            # Print summary
            total_reduction = total_orig - total_pruned
            total_reduction_pct = (total_reduction / total_orig * 100) if total_orig > 0 else 0
            print(f"\nSummary: {len(source_files)} files processed")
            print(f"Total: Count Before: {total_orig:,}, Count after: {total_pruned:,}, Reduction: {total_reduction:,} ({total_reduction_pct:.1f}%)")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
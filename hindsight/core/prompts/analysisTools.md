# Code Analysis Tools Documentation

**CRITICAL**: You MAY use tools to gather additional context for thorough analysis. The code provided may not contain all necessary information.

## TOOL CALLING FORMAT

**CRITICAL - Tool Calling Format**: When you need to use a tool, return a JSON object in a markdown code block with this exact structure:

```json
{
  "tool": "tool_name_here",
  "parameter1": "value1",
  "parameter2": "value2",
  "reason": "Specific reason why you need this tool for the analysis"
}
```

**Example Tool Invocation**:
```json
{
  "tool": "getImplementation",
  "name": "TMTimeSynthesizer",
  "reason": "Need to understand the implementation of TMTimeSynthesizer class to analyze its logic flow and data transformations"
}
```

**IMPORTANT**:
- You can include both text content AND tool requests in your response
- Each tool request must be in its own ```json code block
- After tool execution, you'll receive results and can continue your analysis
- You can request multiple tools by including multiple ```json blocks
- **MANDATORY**: Always include a "reason" field explaining your specific need for the tool

## WHEN TO USE TOOLS

- When you need to understand class implementations mentioned in the code
- When you need to see the full context of functions being called
- When you need to search for specific patterns or dependencies
- When the provided code references files, classes, or functions not fully shown
- When you need to verify assumptions about the codebase structure

## AUTHORIZED TOOLS LIST (USE ONLY THESE)

**CRITICAL**: You MUST ONLY use these exact tool names - no variations, abbreviations, or similar names are allowed:

1. `getImplementation`: retrieve complete class implementation from all associated files.
2. `findSpecificFilesWithSearchString`: find files containing specific text patterns with extension filtering.
3. `checkFileSize`: check if file exists and get size information to determine if readFile can be used. Only use readFile for small files < 16000 characters.
4. `readFile`: inspect specific files only when getImplementation is not applicable.
5. `runTerminalCmd`: run safe commands for exploration and searching (including grep for file searches).
6. `getSummaryOfFile`: retrieve summary of file's functionality
7. `list_files`: list files and directories within a specified directory.
8. `getFileContentByLines`: retrieve content from a file between specific line numbers
9. `getFileContent`: alias for `getFileContentByLines` - retrieve content from a file between specific line numbers
10. `inspectDirectoryHierarchy`: get detailed directory structure information including file counts and sizes

**CRITICAL TOOL USAGE PRIORITY:**
1. **ALWAYS use `checkFileSize` BEFORE `readFile` or `getFileContentByLines`** to determine if file is within size limits and get the total line count (prevents out-of-bounds errors)
2. Use `getSummaryOfFile` to quickly understand a file's purpose and context before deeper analysis
3. Use `runTerminalCmd` for exploration and searching when the above tools are insufficient

## Tool Details

### list_files Tool
**Purpose**: List files and directories within a specified directory to understand project structure
**Usage**: Use this to explore directory contents and understand project organization
**Advantages**: Helps in exploration of directory structure, can list recursively

**Example Usage**:
```json
{
  "tool": "list_files",
  "path": "app/src/main/java/org/thoughtcrime/securesms/util",
  "recursive": false,
  "reason": "Need to understand directory structure before deeper analysis"
}
```

**Parameters**:
- `path`: Directory path to list (relative to repository root)
- `recursive`: (optional) Set to true for recursive listing, false for top-level only

### checkFileSize Tool
**Purpose**: Check if a file exists and get its size and line count information. Returns total line count to prevent out-of-bounds errors with getFileContentByLines.
**Usage**: **ALWAYS USE BEFORE readFile or getFileContentByLines** to prevent "file too large" errors, choose appropriate tools, and know the valid line range for the file.
**Advantages**: Prevents context window overflow, helps choose between readFile/getSummaryOfFile/getFileContentByLines, provides line_count to validate line ranges

**Example Usage**:
```json
{
  "tool": "checkFileSize",
  "path": "app/src/main/java/org/thoughtcrime/securesms/util/ViewUtil.java",
  "reason": "Need to check file size and line count before reading to determine if readFile is safe and get valid line range"
}
```

**IMPORTANT**: Use the `line_count` field from the response to ensure your `startLine` and `endLine` parameters for `getFileContentByLines` are within valid bounds (1 to line_count).

**Decision Making Based on checkFileSize Results**:
- **recommended_for_readFile: true** → Safe to use readFile
- **recommended_for_readFile: false** → Use getSummaryOfFile or getFileContentByLines instead
- **file_available: false** → File not found, use `list_files` on the parent directory to discover actual filenames
- **line_count** → Use this to validate line ranges before calling getFileContentByLines (startLine and endLine must be ≤ line_count)

### getFileContentByLines Tool
**Purpose**: Retrieve content from a file between specific line numbers for targeted analysis
**Usage**: When you need to examine specific sections of a file without reading the entire content
**Advantages**: More efficient than reading entire large files, provides precise content extraction

**Example Usage**:
```json
{
  "tool": "getFileContentByLines",
  "path": "app/src/main/java/org/thoughtcrime/securesms/util/ViewUtil.java",
  "startLine": 208,
  "endLine": 223,
  "reason": "Need to examine the dpToPx implementation to verify pixel conversion logic"
}
```

### getImplementation Tool
**Purpose**: Retrieve the complete implementation of a class, struct or enum from ALL associated files
**Usage**: Whenever you need to understand any class, struct, or enum. This tool automatically finds and reads all relevant files for a class.
**Advantages**: More efficient than multiple readFile calls, provides complete context, includes all related files

**Example Usage**:
```json
{
  "tool": "getImplementation",
  "name": "TMTimeSynthesizer",
  "reason": "Need to understand the complete implementation of TMTimeSynthesizer class to analyze its logic and behavior"
}
```

### getSummaryOfFile Tool
**Purpose**: Retrieve file summary for quick understanding of file purpose and context
**Usage**: When you need to quickly understand what a file does before deeper analysis
**Advantages**: Much faster than reading entire files, provides curated context

**Example Usage**:
```json
{
  "tool": "getSummaryOfFile",
  "path": "app/src/main/java/org/thoughtcrime/securesms/util/ViewUtil.java",
  "reason": "Need to understand the overall purpose of ViewUtil before analyzing specific functions"
}
```

### readFile Tool
**Purpose**: Read specific files when getImplementation is not applicable
**Usage**: Reading non-class files (headers, config files, build files, etc.)
**CRITICAL**: **ALWAYS use checkFileSize FIRST** to ensure file is within size limits and get line count for getFileContentByLines

**Example Usage**:
```json
{
  "tool": "readFile",
  "path": "config/settings.json",
  "reason": "Need to read configuration file to understand project settings"
}
```

**File Size Guidelines**:
- **Small files** (< 5,000 chars): Safe to read with readFile
- **Medium files** (5,000 - 20,000 chars): Read with caution, consider getSummaryOfFile first
- **Large files** (20,000 - 80,000 chars): Use getSummaryOfFile instead of readFile
- **Very large files** (> 80,000 chars): Always use getSummaryOfFile, never readFile

### runTerminalCmd Tool (Exploration & Search)
**Purpose**: Execute safe terminal commands to explore the codebase structure and search for patterns.
**Allowed Commands**: ls, find, grep, wc, head, tail, cat (for small files), tree, file, sed
**Usage**:
  - You need to search for patterns or explore project structure
  - You need to find class names before using getImplementation
  - You need to search for text patterns across files (use grep)

#### ✅ DO - Reliable grep usage (single-word patterns)
```json
{
  "tool": "runTerminalCmd",
  "command": "grep -rn 'TimeSynthesizer' --include='*.m' .",
  "reason": "Find files containing TimeSynthesizer"
}
```

#### ❌ DON'T - Patterns that frequently fail

- **Multi-word patterns**: ❌ `grep 'class MyClassName'` → ✅ `grep 'MyClassName'`
- **Regex patterns**: ❌ `grep 'enum.*Type'` → ✅ `grep 'EnumType'` (use exact name)
- **OR patterns**: ❌ `grep 'word1\|word2'` → ✅ Run two separate grep commands
- **Wildcard file paths**: ❌ `grep 'pattern' dir/*.swift` → ✅ `grep -r 'pattern' --include='*.swift' dir/`

**Strategy**: Search for the most distinctive single word, then use `getFileContentByLines` to examine context around matches.

**Grep flags reference:**
- `-r`: Recursive search
- `-l`: List only filenames (not matching lines)
- `-n`: Show line numbers
- `--include='*.ext'`: Filter by file extension

**IMPORTANT**: Always wrap search patterns in single quotes to prevent shell interpretation of special characters.

### findSpecificFilesWithSearchString Tool
**Purpose**: Find files containing a specific string, searching only files with given extensions recursively.
**Advantages**: More efficient than terminal commands, built-in filtering by file extensions

**Example Usage**:
```json
{
  "tool": "findSpecificFilesWithSearchString",
  "search_string": "TMTimeSynthesizer",
  "extensions": [".h", ".m", ".mm"],
  "reason": "Need to find all files that reference TMTimeSynthesizer to understand its usage patterns"
}
```

### inspectDirectoryHierarchy Tool
**Purpose**: Get detailed directory structure information including file counts and sizes
**Usage**: When you need to understand the organization of a directory tree
**Advantages**: Provides hierarchical view with metadata, useful for large codebases

**Example Usage**:
```json
{
  "tool": "inspectDirectoryHierarchy",
  "path": "app/src/main/java",
  "reason": "Need to understand the package structure of the Java source code"
}
```

**Parameters**:
- `path`: Directory path to inspect (relative to repository root)
- `reason`: (optional) Explanation for why this inspection is needed

## Tool Selection Decision Tree

```
Need to understand code?
├── Need to read a file?
│   ├── YES → Use checkFileSize first, then:
│   │   ├── Large file & broad understanding → Use getSummaryOfFile
│   │   ├── Large file & focused lines → Use getFileContentByLines
│   │   └── Small file → Use readFile
│   └── NO → Continue to next question
├── Need to search/explore/find
│   └── YES → Use runTerminalCmd with grep, findSpecificFilesWithSearchString, or list_files
├── Need to list directory contents
│   └── YES → Use list_files
└── If unsure → Use list_files first, then getImplementation, then getSummaryOfFile, then checkFileSize + readFile as needed
```

## CRITICAL TOOL USAGE ENFORCEMENT

**MANDATORY PRE-TOOL CHECKLIST**: Before invoking ANY tool, you MUST:

1. **VERIFY TOOL NAME**: Confirm the tool name EXACTLY matches one from the authorized list
2. **REJECT INVALID TOOLS**: If you find yourself about to use any other tool name (like `searchCode`, `findCode`, `getCode`, etc.), IMMEDIATELY STOP and select the appropriate authorized tool instead.

**COMMON SUBSTITUTIONS**:
- Want `searchCode`? → Use `runTerminalCmd` with grep or `findSpecificFilesWithSearchString`
- Want `findCode`? → Use `findSpecificFilesWithSearchString` or `runTerminalCmd` with grep
- Want `getCode`? → Use `getImplementation` or `readFile`
- Want file content? → Use `readFile`
- Want directory listing? → Use `list_files`
- Want `getDirectoryListing`? → Use `list_files` instead
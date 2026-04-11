# getDirectoryListing Tool Usage Guide

## Overview

The `getDirectoryListing` tool provides directory tree structure information using the DirectoryTreeUtil class. This tool is particularly useful for LLMs to understand file sizes and directory structure before attempting to read files, helping to avoid context window overflow issues.

## Purpose

- **File Size Awareness**: Shows file sizes in characters to help LLMs decide whether files are too large to read
- **Directory Structure**: Provides a clear view of the repository structure
- **Context Window Management**: Helps prevent token limit issues by showing file sizes upfront

## Tool Signature

```json
{
  "tool": "getDirectoryListing",
  "path": "relative/path/to/directory/or/file",
  "reason": "Why you need this directory listing"
}
```

## Parameters

- **path** (required): Relative path from repository root to the directory or file you want to inspect
  - Use `"."` or `""` for repository root
  - Use `"src"` for src directory
  - Use `"src/main.cpp"` for a specific file
- **reason** (optional): Description of why you need this information

## Example Usage

### 1. Get Repository Root Structure

**Request:**
```json
{
  "tool": "getDirectoryListing",
  "path": ".",
  "reason": "Need to understand the overall project structure before analyzing code"
}
```

**Expected Result:**
```
Directory listing for '.' (use this to understand file sizes before reading files):
hindsight/
|-- analyzers/
|-- core/
|-- utils/
|-- DirectoryTreeUtil.py Size : (3420 chars)
|-- README.md Size : (1250 chars)
|-- requirements.txt Size : (450 chars)
```

### 2. Check Specific Directory

**Request:**
```json
{
  "tool": "getDirectoryListing",
  "path": "core/llm",
  "reason": "Want to see what LLM-related files are available and their sizes"
}
```

**Expected Result:**
```
Directory listing for 'core/llm' (use this to understand file sizes before reading files):
core/llm/
|-- __init__.py Size : (120 chars)
|-- codeAnalysis.py Size : (15420 chars)
|-- llm.py Size : (8950 chars)
|-- summaryService.py Size : (12300 chars)
|-- tools.py Size : (45600 chars)
|-- ttl_manager.py Size : (3200 chars)
```

### 3. Check Single File Size

**Request:**
```json
{
  "tool": "getDirectoryListing",
  "path": "core/llm/tools.py",
  "reason": "Need to check if this file is too large before reading it"
}
```

**Expected Result:**
```
Directory listing for 'core/llm/tools.py' (use this to understand file sizes before reading files):
core/llm/tools.py (45600 chars)
```

## Best Practices

### 1. Use Before Reading Large Files

Always check file sizes before using `readFile` tool:

```json
// First, check the size
{
  "tool": "getDirectoryListing",
  "path": "large_file.cpp",
  "reason": "Check file size before reading"
}

// If size is reasonable (< 80,000 chars), then read
{
  "tool": "readFile",
  "path": "large_file.cpp",
  "reason": "File size is acceptable, reading for analysis"
}
```

### 2. Explore Directory Structure

Use to understand project layout:

```json
{
  "tool": "getDirectoryListing",
  "path": "src",
  "reason": "Understanding source code organization"
}
```

### 3. Find Relevant Files

Use to locate files of interest:

```json
{
  "tool": "getDirectoryListing",
  "path": "tests",
  "reason": "Looking for test files to understand testing patterns"
}
```

## File Size Guidelines

- **Small files** (< 5,000 chars): Safe to read directly
- **Medium files** (5,000 - 20,000 chars): Read with caution
- **Large files** (20,000 - 80,000 chars): Consider reading specific sections
- **Very large files** (> 80,000 chars): Use `getSummaryOfFile` instead or ask for specific sections

## Integration with Other Tools

The `getDirectoryListing` tool works well with:

1. **readFile**: Check sizes first, then read appropriate files
2. **getSummaryOfFile**: For large files, get summaries instead
3. **getImplementation**: Use to find class/function locations
4. **runTerminalCmd**: Use `find` or `grep` for more specific searches

## Error Handling

The tool will return error messages for:
- Non-existent paths
- Invalid path formats
- DirectoryTreeUtil initialization issues

Example error:
```
Path not found: invalid/path/here
```

## Performance Notes

- The tool is lightweight and fast
- Results are cached for better performance
- Shows only supported file extensions by default (.h, .c, .mm, .cpp, .m, .swift, .cc)
- Directory traversal is limited to one level for performance
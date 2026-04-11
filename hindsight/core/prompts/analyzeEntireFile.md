// Code to ANALYZE

{json_content}

### Analysis Process for Entire File:
1. **USE CONTEXTUAL SUMMARIES**: If file and directory summaries are provided above, use them to understand the broader context, purpose, and role of this file in the system
2. Parse the JSON data above to identify the ENTIRE FILE content to analyze
3. **ANALYZE PRIMARY FUNCTIONS**: Review all primary functions and methods in the file (not helper/utility functions)
4. **FOCUS ON MAIN IMPLEMENTATIONS**: Examine the main functional code, constructors, destructors, and public methods
5. **EXCLUDE HELPERS**: Do not report issues in small utility functions, getters, setters, or simple helper methods
6. **FUNCTIONAL VERIFICATION**: Before suggesting any optimization, verify that it would actually work and not break functionality
7. Apply the issue detection criteria to PRIMARY functions and main implementations only, informed by the contextual summaries
8. Report findings with accurate line numbers from the file content
9. **SCOPE**: Analyze the main functional code for production-ready quality, excluding trivial helpers
10. Extract the primary code content from the JSON fileContext and analyze systematically, considering the file's role in the directory and system architecture
11. **NO ISSUE IS VALID**: If the code is already well-implemented for its requirements, use "noIssue" category - this is a valuable analysis result

**LINE NUMBER REPORTING - MUST RULE**: When reporting issues, the "lines" field MUST contain ONLY line numbers or line ranges (e.g., "212" or "212-218"), NEVER actual lines of code.
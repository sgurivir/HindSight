"""
All file extensions supported by the analyzer

Note: JavaScript/TypeScript extensions (.js, .jsx, .ts, .tsx) are intentionally
excluded from AST generation. The JS/TS AST generation code exists but is not
called from ast_util to avoid including JS/TS files in the final merged outputs.
"""


ALL_SUPPORTED_EXTENSIONS = [".cpp", ".cc", ".c", ".mm", ".m", ".h",
                             ".swift", ".kt", ".kts", ".java", ".go"]

#!/usr/bin/env python3
# Author: Sridhar Gurivireddy

import argparse
import hashlib
import json
import logging
import os
import re
import sys

from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
from multiprocessing import cpu_count
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from clang import cindex

from hindsight.utils.log_util import setup_default_logging
from hindsight.core.lang_util.Environment import Environment
from hindsight.core.constants import (
    AST_DEFAULT_PARALLEL_ENABLED,
    AST_DEFAULT_MAX_WORKERS,
    AST_MIN_FILES_FOR_PARALLEL,
)

# Supported file extensions for parsing
SUPPORTED_EXTENSIONS = [".cpp", ".cc", ".c", ".mm", ".m", ".h"]

# Function/Method cursor kinds we care about
ALLOWED_FUNCTION_KINDS = {
    k for k in [
        cindex.CursorKind.FUNCTION_DECL,
        getattr(cindex.CursorKind, "FUNCTION_TEMPLATE", None),
        cindex.CursorKind.CXX_METHOD,
        getattr(cindex.CursorKind, "CXX_CONSTRUCTOR", None),
        getattr(cindex.CursorKind, "CXX_DESTRUCTOR", None),
        getattr(cindex.CursorKind, "OBJC_INSTANCE_METHOD_DECL", None),
        getattr(cindex.CursorKind, "OBJC_CLASS_METHOD_DECL", None),
    ] if k is not None
}

# Class/Struct cursor kinds we care about
ALLOWED_CLASS_KINDS = {
    k for k in [
        cindex.CursorKind.CLASS_DECL,
        cindex.CursorKind.STRUCT_DECL,
        getattr(cindex.CursorKind, "CLASS_TEMPLATE", None),
        getattr(cindex.CursorKind, "OBJC_INTERFACE_DECL", None),
        getattr(cindex.CursorKind, "OBJC_IMPLEMENTATION_DECL", None),
        getattr(cindex.CursorKind, "OBJC_PROTOCOL_DECL", None),
    ] if k is not None
}

# Data type cursor kinds for type usage analysis
ALLOWED_DATA_TYPE_KINDS = {
    k for k in [
        cindex.CursorKind.CLASS_DECL,
        cindex.CursorKind.STRUCT_DECL,
        cindex.CursorKind.ENUM_DECL,
        getattr(cindex.CursorKind, "CLASS_TEMPLATE", None),
        getattr(cindex.CursorKind, "OBJC_INTERFACE_DECL", None),
        getattr(cindex.CursorKind, "OBJC_PROTOCOL_DECL", None),
        cindex.CursorKind.TYPEDEF_DECL,
    ] if k is not None
}

# Constants cursor kinds for constants registry analysis
ALLOWED_CONSTANTS_KINDS = {
    k for k in [
        cindex.CursorKind.VAR_DECL,
        cindex.CursorKind.ENUM_CONSTANT_DECL,
        cindex.CursorKind.MACRO_DEFINITION,
        getattr(cindex.CursorKind, "MACRO_INSTANTIATION", None),
    ] if k is not None
}

def get_clang_flags_for_file(file_path):
    """Get appropriate clang flags based on file extension."""
    suffix = Path(file_path).suffix.lower()

    if suffix == '.c':
        return [
            '-x', 'c',
            '-std=c99',
            '-O2',
            '-I/usr/include',
            '-I/usr/local/include',
            '-DDEBUG=1',
            '-Wno-unused-macros',
            '-D__has_feature(x)=1',
            '-D__has_extension(x)=1',
            '-D__has_attribute(x)=1',
            '-D__has_builtin(x)=1',
        ]
    elif suffix in ['.cpp', '.cc', '.cxx']:
        return [
            '-x', 'c++',
            '-std=c++20',
            '-O2',
            '-I/usr/include',
            '-I/usr/local/include',
            '-fexceptions',
            '-fcolor-diagnostics',
            '-DDEBUG=1',
            '-Wno-unused-macros',
            '-D__has_feature(x)=1',
            '-D__has_extension(x)=1',
            '-D__has_attribute(x)=1',
            '-D__has_builtin(x)=1',
        ]
    elif suffix in ['.m', '.mm']:
        # Keep original Objective-C++ flags for .m/.mm files
        return [
            "-x", "objective-c++",
            "-std=c++20",
            "-O2",
            "-fno-blocks",
            "-fblocks",
            "-fobjc-arc",
            "-fexceptions",
            "-fno-exceptions",
            "-fobjc-exceptions",
            "-fmodules",
            "-fcxx-modules",
            "-fcolor-diagnostics",
            "-DDEBUG=1",
            "-Wno-unused-macros",
            "-D__has_feature(x)=1",
            "-D__has_extension(x)=1",
            "-D__has_attribute(x)=1",
            "-D__has_builtin(x)=1",
        ]
    else:
        # Default to C++ for headers and unknown extensions
        # Headers may contain C++ constructs (templates, classes, namespaces)
        return [
            '-x', 'c++',
            '-std=c++20',
            '-O2',
            '-I/usr/include',
            '-I/usr/local/include',
            '-fexceptions',
            '-fcolor-diagnostics',
            '-DDEBUG=1',
            '-Wno-unused-macros',
            '-D__has_feature(x)=1',
            '-D__has_extension(x)=1',
            '-D__has_attribute(x)=1',
            '-D__has_builtin(x)=1',
        ]


@lru_cache(maxsize=261072)
def strip_template_params(name: str) -> str:
    """Remove template arguments (<...>) from a string."""
    result, depth = [], 0
    for ch in name:
        if ch == '<':
            depth += 1
        elif ch == '>':
            depth = max(0, depth - 1)
        elif depth == 0:
            result.append(ch)
    return ''.join(result)

def base_function_name(name: str) -> str:
    """Return function name without template args or params."""
    name = strip_template_params(name)
    idx = name.find('(')
    return name[:idx] if idx != -1 else name

def generate_code_checksum(cursor):
    """Generate a checksum from the code block defining the data type."""
    try:
        # Get the source code for this cursor
        start = cursor.extent.start
        end = cursor.extent.end
        
        if not start.file or not end.file:
            return "unknown"
            
        file_path = start.file.name
        start_offset = start.offset
        end_offset = end.offset
        
        # Read the file and extract the code block
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                f.seek(start_offset)
                code_block = f.read(end_offset - start_offset)
        except Exception:
            # Fallback: use cursor location info
            code_block = f"line_{start.line}_col_{start.column}_to_line_{end.line}_col_{end.column}"
        
        # Generate MD5 checksum of the code block
        checksum = hashlib.md5(code_block.encode('utf-8')).hexdigest()[:8]
        return checksum
        
    except Exception:
        # Fallback checksum based on cursor location
        try:
            start = cursor.extent.start
            end = cursor.extent.end
            location_str = f"line_{start.line}_col_{start.column}_to_line_{end.line}_col_{end.column}"
            return hashlib.md5(location_str.encode('utf-8')).hexdigest()[:8]
        except Exception:
            return "unknown"

def generate_unnamed_type_name(cursor, file_path):
    """Generate a name for unnamed types with format: unnamed_<file_name>_<checksum>."""
    try:
        # Extract file name and replace dots with underscores
        if file_path:
            file_name = Path(file_path).stem  # Get filename without extension
            file_name = file_name.replace('.', '_')
        else:
            file_name = "unknown"
        
        # Generate checksum from code block
        checksum = generate_code_checksum(cursor)
        
        # Create the unnamed type name
        unnamed_name = f"unnamed_{file_name}_{checksum}"
        return unnamed_name
        
    except Exception:
        return "unnamed_unknown_unknown"

def detect_preprocessor_macros(source_files: List[Path], include_headers: bool = True) -> Set[str]:
    """
    Scan source files to detect all preprocessor macros used in #if, #ifdef, #ifndef directives,
    as well as derived macros defined in terms of other macros.
    Returns a set of macro names that should be defined to ensure all code paths are parsed.
    
    Args:
        source_files: List of source file paths to scan
        include_headers: If True, also scan header files for derived macro definitions
        
    Returns:
        Set of macro names found in preprocessor conditionals and derived macro definitions
    """
    macros, _ = detect_preprocessor_macros_with_derived(source_files, include_headers)
    return macros


def detect_preprocessor_macros_with_derived(source_files: List[Path], include_headers: bool = True) -> Tuple[Set[str], Dict[str, str]]:
    """
    Scan source files to detect all preprocessor macros used in #if, #ifdef, #ifndef directives,
    as well as derived macros defined in terms of other macros.
    
    Args:
        source_files: List of source file paths to scan
        include_headers: If True, also scan header files for derived macro definitions
        
    Returns:
        Tuple of:
        - Set of all macro names (both direct and derived)
        - Dict mapping derived macro names to their base macro dependencies
    """
    macros = set()
    derived_macros: Dict[str, str] = {}  # Maps derived macro -> base macro it depends on

    # Regex patterns to match various preprocessor conditionals
    # Enhanced patterns to support trailing comments (#if MACRO // comment)
    conditional_patterns = [
        r'#\s*if\s+defined\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)',  # #if defined(MACRO)
        r'#\s*ifdef\s+([A-Za-z_][A-Za-z0-9_]*)',                    # #ifdef MACRO
        r'#\s*ifndef\s+([A-Za-z_][A-Za-z0-9_]*)',                   # #ifndef MACRO
        r'#\s*if\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?://|/\*|$)',        # #if MACRO (with optional comment or EOL)
        r'#\s*if\s+!([A-Za-z_][A-Za-z0-9_]*)\s*(?://|/\*|$)',       # #if !MACRO (with optional comment or EOL)
        r'#\s*if\s+.*?([A-Za-z_][A-Za-z0-9_]*)\s*==',               # #if MACRO == value
        r'#\s*if\s+.*?([A-Za-z_][A-Za-z0-9_]*)\s*!=',               # #if MACRO != value
        r'#\s*if\s+.*?([A-Za-z_][A-Za-z0-9_]*)\s*>=',               # #if MACRO >= value
        r'#\s*if\s+.*?([A-Za-z_][A-Za-z0-9_]*)\s*<=',               # #if MACRO <= value
        r'#\s*if\s+.*?([A-Za-z_][A-Za-z0-9_]*)\s*>',                # #if MACRO > value
        r'#\s*if\s+.*?([A-Za-z_][A-Za-z0-9_]*)\s*<',                # #if MACRO < value
        r'#\s*elif\s+defined\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)', # #elif defined(MACRO)
        r'#\s*elif\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?://|/\*|$)',      # #elif MACRO (with optional comment or EOL)
    ]
    
    # Patterns to detect derived macros: #define MACRO !OTHER_MACRO or #define MACRO OTHER_MACRO
    derived_macro_patterns = [
        # #define MACRO !OTHER_MACRO (negation)
        r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+!([A-Za-z_][A-Za-z0-9_]*)\s*(?://|/\*|$)',
        # #define MACRO OTHER_MACRO (alias)
        r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?://|/\*|$)',
        # #define MACRO (OTHER_MACRO) (parenthesized)
        r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+\(([A-Za-z_][A-Za-z0-9_]*)\)\s*(?://|/\*|$)',
        # #define MACRO !(OTHER_MACRO) (negated parenthesized)
        r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+!\(([A-Za-z_][A-Za-z0-9_]*)\)\s*(?://|/\*|$)',
    ]

    compiled_conditional_patterns = [re.compile(pattern, re.MULTILINE) for pattern in conditional_patterns]
    compiled_derived_patterns = [re.compile(pattern, re.MULTILINE) for pattern in derived_macro_patterns]

    # Common tokens to exclude (not actual macros)
    excluded_tokens = {
        'defined', 'true', 'false', 'TRUE', 'FALSE', 'NULL', 'nullptr',
        'if', 'else', 'elif', 'endif', 'ifdef', 'ifndef', 'define', 'undef',
        'include', 'pragma', 'error', 'warning', 'line',
    }
    
    # Determine which file extensions to scan
    impl_extensions = {'.c', '.cpp', '.cc', '.m', '.mm'}
    header_extensions = {'.h', '.hpp', '.hxx'} if include_headers else set()
    scan_extensions = impl_extensions | header_extensions

    logging.info(f"[+] Scanning {len(source_files)} source files for preprocessor macros...")

    for source_file in source_files:
        if source_file.suffix not in scan_extensions:
            continue
        try:
            with open(source_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Detect macros used in conditionals
            for pattern in compiled_conditional_patterns:
                matches = pattern.findall(content)
                for match in matches:
                    # Filter out common non-macro tokens
                    if (match and
                        not match.isdigit() and
                        not match.startswith('__') and  # Skip compiler built-ins like __cplusplus
                        len(match) > 1 and
                        match not in excluded_tokens):
                        macros.add(match)
            
            # Detect derived macro definitions (only in headers or all files)
            for pattern in compiled_derived_patterns:
                matches = pattern.findall(content)
                for match in matches:
                    if len(match) == 2:
                        derived_macro, base_macro = match
                        # Filter out invalid matches
                        if (derived_macro and base_macro and
                            not derived_macro.isdigit() and not base_macro.isdigit() and
                            not derived_macro.startswith('__') and not base_macro.startswith('__') and
                            len(derived_macro) > 1 and len(base_macro) > 1 and
                            derived_macro not in excluded_tokens and base_macro not in excluded_tokens):
                            derived_macros[derived_macro] = base_macro

        except Exception as e:
            logging.warning(f"Could not read {source_file} for macro detection: {e}")
            continue

    # Add derived macros and their base macros to the result
    # This ensures both the derived macro and its dependency are defined
    for derived, base in derived_macros.items():
        macros.add(derived)
        macros.add(base)
        logging.debug(f"[+] Found derived macro: {derived} depends on {base}")

    logging.info(f"[+] Detected {len(macros)} preprocessor macros from {len(source_files)} files")
    if derived_macros:
        logging.info(f"[+] Including {len(derived_macros)} derived macros")
    return macros, derived_macros


def create_macro_flags_excluding_derived(macros: Set[str], derived_macros: Dict[str, str]) -> List[str]:
    """
    Convert detected macros into clang -D flags, excluding derived macros.
    Derived macros should not be explicitly defined because they are computed
    from their base macros in header files.
    
    Args:
        macros: Set of all macro names
        derived_macros: Dict mapping derived macro names to their base macros
        
    Returns:
        List of -D flags for clang (e.g., ['-DDEBUG=1', '-DFEATURE_X=1'])
    """
    # Get the set of derived macro names to exclude
    derived_names = set(derived_macros.keys())
    
    flags = []
    for macro in sorted(macros):
        # Skip derived macros - they will be computed from their base macros
        if macro in derived_names:
            logging.debug(f"[+] Skipping derived macro: {macro} (depends on {derived_macros[macro]})")
            continue
        # Define each macro with value 1 to enable conditional code paths
        flags.append(f"-D{macro}=1")
    return flags


def create_macro_flags(macros: Set[str]) -> List[str]:
    """
    Convert detected macros into clang -D flags.
    
    Args:
        macros: Set of macro names to define
        
    Returns:
        List of -D flags for clang (e.g., ['-DDEBUG=1', '-DFEATURE_X=1'])
    """
    flags = []
    for macro in sorted(macros):
        # Define each macro with value 1 to enable conditional code paths
        flags.append(f"-D{macro}=1")
    return flags


# ======================== Parallel Processing Infrastructure ========================
# These functions are defined at module level to support pickling for multiprocessing

def _init_worker_process(shared_log_file: str = None):
    """
    Initialize libclang in a worker process.
    This must be called once per worker process to set up the clang library path.
    Also suppresses known harmless warnings that appear in worker processes.
    
    Args:
        shared_log_file: Path to the shared log file from the main process.
                        If provided, worker will log to this file instead of creating a new one.
    """
    import warnings
    from hindsight.utils.log_util import LogUtil
    
    # Mark this process as a worker process FIRST
    # This prevents duplicate "Logs will be persisted to file:" messages
    # by telling LogUtil to skip printing log file paths in worker processes
    LogUtil.mark_as_worker_process()
    
    # Set the shared log file path so workers log to the same file as the main process
    if shared_log_file:
        LogUtil.set_shared_log_file(shared_log_file)
    
    # Suppress known harmless warnings in worker processes
    # 1. urllib3 NotOpenSSLWarning - appears when using LibreSSL instead of OpenSSL
    #    This is a system-level SSL library compatibility warning, not a code issue
    #    ROOT CAUSE: macOS uses LibreSSL instead of OpenSSL - cannot be fixed in code
    try:
        from urllib3.exceptions import NotOpenSSLWarning
        warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
    except ImportError:
        # urllib3 may not be installed or may not have this warning class
        pass
    
    # 2. RuntimeWarning about module imports - appears when worker processes
    #    re-import modules that were already imported in the parent process
    #    ROOT CAUSE: Python's runpy detects module already in sys.modules when workers start
    #    This is expected behavior in multiprocessing and doesn't affect functionality
    warnings.filterwarnings(
        "ignore",
        message=".*found in sys.modules after import of package.*",
        category=RuntimeWarning
    )
    
    # Initialize libclang
    try:
        Environment.initialize_libclang()
        logging.debug("[Worker] libclang initialized successfully")
    except Exception as e:
        logging.warning(f"[Worker] Failed to initialize libclang: {e}")


def _process_single_file_for_functions(args: Tuple) -> Dict[str, Any]:
    """
    Process a single source file to extract function definitions.
    This is a worker function for parallel processing.
    
    Args:
        args: Tuple of (source_file, repo_root, clang_args, macro_flags)
        
    Returns:
        Dict with 'file' key and 'registry' containing function definitions
    """
    source_file, repo_root, clang_args, macro_flags = args
    
    try:
        index = cindex.Index.create()
        registry = {}
        resolved_repo_root = Path(repo_root).resolve()
        
        try:
            rel = str(Path(source_file).relative_to(resolved_repo_root))
        except ValueError:
            try:
                rel = str(Path(source_file).relative_to(repo_root))
            except ValueError:
                rel = str(Path(source_file).name)
        
        try:
            base_flags = get_clang_flags_for_file(source_file) or []
            additional_args = clang_args or []
            additional_macros = macro_flags or []
            file_flags = base_flags + additional_args + additional_macros
            tu = index.parse(str(source_file), args=file_flags)
        except Exception as e:
            logging.debug(f"[Worker] Failed to parse {rel}: {e}")
            return {'file': str(source_file), 'registry': {}, 'error': str(e)}
        
        # Collect function definitions using iterative traversal
        stack = [tu.cursor]
        while stack:
            cursor = stack.pop()
            try:
                try:
                    cursor_kind = cursor.kind
                except ValueError:
                    try:
                        children = list(cursor.get_children())
                        for child in reversed(children):
                            stack.append(child)
                    except Exception:
                        pass
                    continue
                
                if cursor_kind in ALLOWED_FUNCTION_KINDS and cursor.is_definition():
                    # Import CASTUtil methods for naming
                    name = None
                    qualified_name = None
                    parts = []
                    current = cursor
                    
                    valid_parent_kinds = {
                        cindex.CursorKind.NAMESPACE,
                        cindex.CursorKind.CLASS_DECL,
                        cindex.CursorKind.STRUCT_DECL,
                        cindex.CursorKind.ENUM_DECL,
                    }
                    for attr in ["CLASS_TEMPLATE", "OBJC_INTERFACE_DECL", "OBJC_IMPLEMENTATION_DECL", "OBJC_PROTOCOL_DECL"]:
                        kind = getattr(cindex.CursorKind, attr, None)
                        if kind:
                            valid_parent_kinds.add(kind)
                    
                    while current:
                        if getattr(current, "spelling", None):
                            spelling = current.spelling
                            if spelling and current.kind not in (
                                cindex.CursorKind.TRANSLATION_UNIT,
                                cindex.CursorKind.NAMESPACE_REF,
                                cindex.CursorKind.TEMPLATE_REF,
                                cindex.CursorKind.TYPE_REF,
                            ):
                                if current == cursor or current.kind in valid_parent_kinds:
                                    parts.append(spelling)
                        
                        if hasattr(current, "semantic_parent") and current.semantic_parent:
                            current = current.semantic_parent
                            if current.kind == cindex.CursorKind.TRANSLATION_UNIT:
                                break
                        else:
                            break
                    
                    parts.reverse()
                    name = "::".join(parts) if parts else cursor.spelling
                    
                    if name:
                        start, end = cursor.extent.start, cursor.extent.end
                        start_line = getattr(start, 'line', None)
                        end_line = getattr(end, 'line', None)
                        start_byte = getattr(start, 'offset', None)
                        end_byte = getattr(end, 'offset', None)
                        file_path = start.file.name if start.file else rel
                    
                        # Skip cursors from system/SDK headers - they should not be registered
                        # as belonging to project files (fixes transitive system framework pollution)
                        if not _is_file_in_repo(file_path, repo_root, resolved_repo_root):
                            logging.debug(f"[Worker] Skipping system header cursor: {name} from {file_path}")
                            continue
                    
                        try:
                            file_rel = str(Path(file_path).relative_to(resolved_repo_root))
                        except ValueError:
                            try:
                                file_rel = str(Path(file_path).relative_to(repo_root))
                            except ValueError:
                                # This should not happen after _is_file_in_repo check, but log if it does
                                logging.warning(f"[Worker] Unexpected: file {file_path} passed repo check but failed relative_to")
                                continue
                    
                        if name not in registry:
                            registry[name] = set()
                        registry[name].add((file_rel, start_line, end_line, start_byte, end_byte))
                
                # Add children to stack
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
                    
            except Exception as e:
                logging.debug(f"[Worker] Error processing cursor: {e}")
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
        
        # Convert sets to lists for JSON serialization
        serializable_registry = {}
        for fn_name, entries in registry.items():
            serializable_registry[fn_name] = [list(entry) for entry in entries]
        
        return {'file': str(source_file), 'registry': serializable_registry}
        
    except Exception as e:
        logging.debug(f"[Worker] Exception processing {source_file}: {e}")
        return {'file': str(source_file), 'registry': {}, 'error': str(e)}


def _process_single_file_for_data_types(args: Tuple) -> Dict[str, Any]:
    """
    Process a single source file to extract data type definitions.
    This is a worker function for parallel processing.
    
    Args:
        args: Tuple of (source_file, repo_root, clang_args, macro_flags)
        
    Returns:
        Dict with 'file' key and 'registry' containing data type definitions
    """
    source_file, repo_root, clang_args, macro_flags = args
    
    try:
        index = cindex.Index.create()
        registry = {}
        resolved_repo_root = Path(repo_root).resolve()
        
        try:
            rel = str(Path(source_file).relative_to(resolved_repo_root))
        except ValueError:
            try:
                rel = str(Path(source_file).relative_to(repo_root))
            except ValueError:
                rel = str(Path(source_file).name)
        
        try:
            base_flags = get_clang_flags_for_file(source_file) or []
            additional_args = clang_args or []
            additional_macros = macro_flags or []
            file_flags = base_flags + additional_args + additional_macros
            tu = index.parse(str(source_file), args=file_flags)
        except Exception as e:
            logging.debug(f"[Worker] Failed to parse {rel}: {e}")
            return {'file': str(source_file), 'registry': {}, 'error': str(e)}
        
        # Collect data type definitions using iterative traversal
        stack = [tu.cursor]
        while stack:
            cursor = stack.pop()
            try:
                try:
                    cursor_kind = cursor.kind
                except ValueError:
                    try:
                        children = list(cursor.get_children())
                        for child in reversed(children):
                            stack.append(child)
                    except Exception:
                        pass
                    continue
                
                # For Objective-C interfaces, we accept declarations (not just definitions)
                # because @interface in .h files is the canonical class declaration,
                # while @implementation in .m files is the definition
                is_objc_interface = cursor_kind == getattr(cindex.CursorKind, "OBJC_INTERFACE_DECL", None)
                is_objc_protocol = cursor_kind == getattr(cindex.CursorKind, "OBJC_PROTOCOL_DECL", None)
                
                if cursor_kind in ALLOWED_DATA_TYPE_KINDS and (cursor.is_definition() or is_objc_interface or is_objc_protocol):
                    # Get qualified name
                    parts = []
                    current = cursor
                    
                    valid_parent_kinds = {
                        cindex.CursorKind.NAMESPACE,
                        cindex.CursorKind.CLASS_DECL,
                        cindex.CursorKind.STRUCT_DECL,
                        cindex.CursorKind.ENUM_DECL,
                    }
                    for attr in ["CLASS_TEMPLATE", "OBJC_INTERFACE_DECL", "OBJC_IMPLEMENTATION_DECL", "OBJC_PROTOCOL_DECL"]:
                        kind = getattr(cindex.CursorKind, attr, None)
                        if kind:
                            valid_parent_kinds.add(kind)
                    
                    while current:
                        if getattr(current, "spelling", None):
                            spelling = current.spelling
                            if spelling and current.kind not in (
                                cindex.CursorKind.TRANSLATION_UNIT,
                                cindex.CursorKind.NAMESPACE_REF,
                                cindex.CursorKind.TEMPLATE_REF,
                                cindex.CursorKind.TYPE_REF,
                            ):
                                if current == cursor or current.kind in valid_parent_kinds:
                                    parts.append(spelling)
                        
                        if hasattr(current, "semantic_parent") and current.semantic_parent:
                            current = current.semantic_parent
                            if current.kind == cindex.CursorKind.TRANSLATION_UNIT:
                                break
                        else:
                            break
                    
                    parts.reverse()
                    qualified_name = "::".join(parts) if parts else cursor.spelling
                    
                    if qualified_name:
                        start, end = cursor.extent.start, cursor.extent.end
                        start_line = getattr(start, 'line', None)
                        end_line = getattr(end, 'line', None)
                        file_path = start.file.name if start.file else rel
                        
                        # Skip cursors from system/SDK headers - they should not be registered
                        # as belonging to project files (fixes transitive system framework pollution)
                        if not _is_file_in_repo(file_path, repo_root, resolved_repo_root):
                            logging.debug(f"[Worker] Skipping system header data type cursor: {qualified_name} from {file_path}")
                            continue
                        
                        try:
                            file_rel = str(Path(file_path).relative_to(resolved_repo_root))
                        except ValueError:
                            try:
                                file_rel = str(Path(file_path).relative_to(repo_root))
                            except ValueError:
                                # This should not happen after _is_file_in_repo check, but log if it does
                                logging.warning(f"[Worker] Unexpected: file {file_path} passed repo check but failed relative_to")
                                continue
                        
                        if start_line is not None and end_line is not None:
                            file_entry = {
                                "file_name": file_rel,
                                "start": start_line,
                                "end": end_line
                            }
                            
                            if qualified_name not in registry:
                                registry[qualified_name] = []
                            
                            if file_entry not in registry[qualified_name]:
                                registry[qualified_name].append(file_entry)
                
                # Add children to stack
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
                    
            except Exception as e:
                logging.debug(f"[Worker] Error processing cursor: {e}")
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
        
        return {'file': str(source_file), 'registry': registry}
        
    except Exception as e:
        logging.debug(f"[Worker] Exception processing {source_file}: {e}")
        return {'file': str(source_file), 'registry': {}, 'error': str(e)}


def _process_single_file_for_call_graph(args: Tuple) -> Dict[str, Any]:
    """
    Process a single source file to extract call graph edges.
    This is a worker function for parallel processing.
    
    Args:
        args: Tuple of (source_file, repo_root, clang_args, macro_flags, filter_external_calls, defined_funcs)
        
    Returns:
        Dict with 'file' key and 'edges' containing caller->callee mappings
    """
    source_file, repo_root, clang_args, macro_flags, filter_external_calls, defined_funcs = args
    
    try:
        index = cindex.Index.create()
        forward_map = {}
        resolved_repo_root = Path(repo_root).resolve()
        
        try:
            rel = str(Path(source_file).relative_to(resolved_repo_root))
        except ValueError:
            try:
                rel = str(Path(source_file).relative_to(repo_root))
            except ValueError:
                rel = str(Path(source_file).name)
        
        try:
            base_flags = get_clang_flags_for_file(source_file) or []
            additional_args = clang_args or []
            additional_macros = macro_flags or []
            file_flags = base_flags + additional_args + additional_macros
            tu = index.parse(str(source_file), args=file_flags)
        except Exception as e:
            logging.debug(f"[Worker] Failed to parse {rel}: {e}")
            return {'file': str(source_file), 'edges': {}, 'error': str(e)}
        
        def base_name_only(name: str) -> str:
            idx = name.find('(')
            return name[:idx] if idx != -1 else name
        
        def keep_callee(name):
            if not defined_funcs or not filter_external_calls:
                return True
            b = base_name_only(name)
            if b in defined_funcs:
                return True
            candidates = [d for d in defined_funcs if d.endswith(f"::{b}")]
            return len(candidates) == 1
        
        def get_function_name(cursor):
            """Get fully qualified function name."""
            parts = []
            current = cursor
            
            valid_parent_kinds = {
                cindex.CursorKind.NAMESPACE,
                cindex.CursorKind.CLASS_DECL,
                cindex.CursorKind.STRUCT_DECL,
                cindex.CursorKind.ENUM_DECL,
            }
            for attr in ["CLASS_TEMPLATE", "OBJC_INTERFACE_DECL", "OBJC_IMPLEMENTATION_DECL", "OBJC_PROTOCOL_DECL"]:
                kind = getattr(cindex.CursorKind, attr, None)
                if kind:
                    valid_parent_kinds.add(kind)
            
            while current:
                if getattr(current, "spelling", None):
                    spelling = current.spelling
                    if spelling and current.kind not in (
                        cindex.CursorKind.TRANSLATION_UNIT,
                        cindex.CursorKind.NAMESPACE_REF,
                        cindex.CursorKind.TEMPLATE_REF,
                        cindex.CursorKind.TYPE_REF,
                    ):
                        if current == cursor or current.kind in valid_parent_kinds:
                            parts.append(spelling)
                
                if hasattr(current, "semantic_parent") and current.semantic_parent:
                    current = current.semantic_parent
                    if current.kind == cindex.CursorKind.TRANSLATION_UNIT:
                        break
                else:
                    break
            
            parts.reverse()
            return "::".join(parts) if parts else cursor.spelling
        
        def resolve_callable(cursor):
            """Resolve a cursor to a callable function name."""
            if cursor.kind == cindex.CursorKind.CALL_EXPR:
                ref = cursor.referenced
                if ref and ref.kind in ALLOWED_FUNCTION_KINDS:
                    return get_function_name(ref)
            elif cursor.kind == cindex.CursorKind.DECL_REF_EXPR:
                ref = cursor.referenced
                if ref and ref.kind in ALLOWED_FUNCTION_KINDS:
                    return get_function_name(ref)
            elif cursor.kind == cindex.CursorKind.MEMBER_REF_EXPR:
                ref = cursor.referenced
                if ref and ref.kind in ALLOWED_FUNCTION_KINDS:
                    return get_function_name(ref)
            elif hasattr(cindex.CursorKind, 'OBJC_MESSAGE_EXPR') and cursor.kind == cindex.CursorKind.OBJC_MESSAGE_EXPR:
                # For Objective-C message expressions like [self methodName]
                ref = cursor.referenced
                if ref and ref.kind in ALLOWED_FUNCTION_KINDS:
                    return get_function_name(ref)
            return None
        
        # Collect call graph edges
        stack = [tu.cursor]
        current_function = None
        function_stack = []
        
        while stack:
            cursor = stack.pop()
            try:
                try:
                    cursor_kind = cursor.kind
                except ValueError:
                    try:
                        children = list(cursor.get_children())
                        for child in reversed(children):
                            stack.append(child)
                    except Exception:
                        pass
                    continue
                
                if cursor_kind in ALLOWED_FUNCTION_KINDS and cursor.is_definition():
                    fn_name = get_function_name(cursor)
                    if fn_name:
                        # Process function body for calls
                        body_stack = list(cursor.get_children())
                        while body_stack:
                            body_cursor = body_stack.pop()
                            try:
                                target = resolve_callable(body_cursor)
                                if target and keep_callee(target):
                                    if fn_name not in forward_map:
                                        forward_map[fn_name] = set()
                                    forward_map[fn_name].add(target)
                                
                                try:
                                    body_children = list(body_cursor.get_children())
                                    for child in reversed(body_children):
                                        body_stack.append(child)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                
                # Add children to stack
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
                    
            except Exception as e:
                logging.debug(f"[Worker] Error processing cursor: {e}")
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
        
        # Convert sets to lists for JSON serialization
        serializable_edges = {}
        for caller, callees in forward_map.items():
            serializable_edges[caller] = list(callees)
        
        return {'file': str(source_file), 'edges': serializable_edges}
        
    except Exception as e:
        logging.debug(f"[Worker] Exception processing {source_file}: {e}")
        return {'file': str(source_file), 'edges': {}, 'error': str(e)}


def _process_single_file_for_data_type_usage(args: Tuple) -> Dict[str, Any]:
    """
    Process a single source file to extract data type usage per function.
    This is a worker function for parallel processing.
    
    Args:
        args: Tuple of (source_file, repo_root, clang_args, macro_flags, custom_types_list)
        
    Returns:
        Dict with 'file' key and 'usage' containing function->types mappings
    """
    source_file, repo_root, clang_args, macro_flags, custom_types_list = args
    custom_types_registry = set(custom_types_list) if custom_types_list else None
    
    try:
        index = cindex.Index.create()
        data_type_map = {}
        resolved_repo_root = Path(repo_root).resolve()
        
        try:
            rel = str(Path(source_file).relative_to(resolved_repo_root))
        except ValueError:
            try:
                rel = str(Path(source_file).relative_to(repo_root))
            except ValueError:
                rel = str(Path(source_file).name)
        
        try:
            base_flags = get_clang_flags_for_file(source_file) or []
            additional_args = clang_args or []
            additional_macros = macro_flags or []
            file_flags = base_flags + additional_args + additional_macros
            tu = index.parse(str(source_file), args=file_flags)
        except Exception as e:
            logging.debug(f"[Worker] Failed to parse {rel}: {e}")
            return {'file': str(source_file), 'usage': {}, 'error': str(e)}
        
        def extract_type_from_cursor(cursor):
            """Extract type information from a cursor."""
            type_names = set()
            
            if hasattr(cursor, 'type') and cursor.type:
                type_obj = cursor.type
                if hasattr(type_obj, 'get_canonical'):
                    type_obj = type_obj.get_canonical()
                
                type_spelling = getattr(type_obj, 'spelling', '')
                if type_spelling:
                    # Check if it's a standard library type
                    clean_name = strip_template_params(type_spelling).strip()
                    for qualifier in ['const', 'volatile', 'static', 'extern', 'inline', '*', '&', '&&']:
                        clean_name = clean_name.replace(qualifier, '').strip()
                    
                    # Skip standard types
                    std_types = {"int", "long", "short", "char", "wchar_t", "bool", "float", "double",
                                "size_t", "ptrdiff_t", "void", "id", "char16_t", "char32_t", "signed",
                                "unsigned", "nullptr_t", "auto"}
                    std_prefixes = ("std::", "__gnu_cxx::", "::std::", "__NS", "objc_", "dispatch_",
                                   "__", "_", "NS", "CF", "CG", "CA", "UI", "GL")
                    
                    if clean_name not in std_types and not any(clean_name.startswith(p) for p in std_prefixes):
                        type_names.add(clean_name)
            
            if cursor.kind == cindex.CursorKind.TYPE_REF:
                ref = cursor.referenced
                if ref and ref.kind in ALLOWED_DATA_TYPE_KINDS:
                    parts = []
                    current = ref
                    valid_parent_kinds = {
                        cindex.CursorKind.NAMESPACE,
                        cindex.CursorKind.CLASS_DECL,
                        cindex.CursorKind.STRUCT_DECL,
                        cindex.CursorKind.ENUM_DECL,
                    }
                    while current:
                        if getattr(current, "spelling", None):
                            spelling = current.spelling
                            if spelling and current.kind not in (
                                cindex.CursorKind.TRANSLATION_UNIT,
                                cindex.CursorKind.NAMESPACE_REF,
                            ):
                                if current == ref or current.kind in valid_parent_kinds:
                                    parts.append(spelling)
                        if hasattr(current, "semantic_parent") and current.semantic_parent:
                            current = current.semantic_parent
                            if current.kind == cindex.CursorKind.TRANSLATION_UNIT:
                                break
                        else:
                            break
                    parts.reverse()
                    qualified_name = "::".join(parts) if parts else None
                    if qualified_name:
                        type_names.add(qualified_name)
            
            return type_names
        
        def traverse_for_types(node, collected_types):
            """Traverse AST to find type usage."""
            stack = [node]
            while stack:
                current = stack.pop()
                node_types = extract_type_from_cursor(current)
                collected_types.update(node_types)
                try:
                    children = list(current.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    continue
        
        # Collect function type usage
        stack = [tu.cursor]
        while stack:
            cursor = stack.pop()
            try:
                try:
                    cursor_kind = cursor.kind
                except ValueError:
                    try:
                        children = list(cursor.get_children())
                        for child in reversed(children):
                            stack.append(child)
                    except Exception:
                        pass
                    continue
                
                if cursor_kind in ALLOWED_FUNCTION_KINDS and cursor.is_definition():
                    # Get function name
                    parts = []
                    current = cursor
                    valid_parent_kinds = {
                        cindex.CursorKind.NAMESPACE,
                        cindex.CursorKind.CLASS_DECL,
                        cindex.CursorKind.STRUCT_DECL,
                        cindex.CursorKind.ENUM_DECL,
                    }
                    while current:
                        if getattr(current, "spelling", None):
                            spelling = current.spelling
                            if spelling and current.kind not in (
                                cindex.CursorKind.TRANSLATION_UNIT,
                                cindex.CursorKind.NAMESPACE_REF,
                            ):
                                if current == cursor or current.kind in valid_parent_kinds:
                                    parts.append(spelling)
                        if hasattr(current, "semantic_parent") and current.semantic_parent:
                            current = current.semantic_parent
                            if current.kind == cindex.CursorKind.TRANSLATION_UNIT:
                                break
                        else:
                            break
                    parts.reverse()
                    fn_name = "::".join(parts) if parts else cursor.spelling
                    
                    if fn_name:
                        collected_types = set()
                        
                        # Analyze function parameters
                        try:
                            for arg in cursor.get_arguments():
                                arg_types = extract_type_from_cursor(arg)
                                collected_types.update(arg_types)
                        except Exception:
                            pass
                        
                        # Analyze return type
                        if hasattr(cursor, 'result_type') and cursor.result_type:
                            return_type = cursor.result_type
                            if hasattr(return_type, 'get_canonical'):
                                return_type = return_type.get_canonical()
                            return_type_spelling = getattr(return_type, 'spelling', '')
                            if return_type_spelling:
                                clean_type = strip_template_params(return_type_spelling).strip()
                                if clean_type:
                                    collected_types.add(clean_type)
                        
                        # Analyze function body
                        try:
                            for child in cursor.get_children():
                                traverse_for_types(child, collected_types)
                        except Exception:
                            pass
                        
                        # Filter to custom types if registry provided
                        if custom_types_registry:
                            collected_types = {t for t in collected_types if t in custom_types_registry}
                        
                        if collected_types:
                            data_type_map[fn_name] = sorted(collected_types)
                
                # Add children to stack
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
                    
            except Exception as e:
                logging.debug(f"[Worker] Error processing cursor: {e}")
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
        
        return {'file': str(source_file), 'usage': data_type_map}
        
    except Exception as e:
        logging.debug(f"[Worker] Exception processing {source_file}: {e}")
        return {'file': str(source_file), 'usage': {}, 'error': str(e)}


def _is_file_in_repo(file_path: str, repo_root: str, resolved_repo_root) -> bool:
    """
    Check if a file path is within the repository root.
    This is used to filter out system/SDK headers from analysis.
    
    Args:
        file_path: The file path to check
        repo_root: The repository root path (string)
        resolved_repo_root: The resolved (absolute) repository root path
        
    Returns:
        True if the file is within the repository, False otherwise
    """
    if not file_path:
        return False
    
    try:
        file_path_obj = Path(file_path)
        # Try to check if file is relative to resolved repo root
        try:
            file_path_obj.relative_to(resolved_repo_root)
            return True
        except ValueError:
            pass
        
        # Try with original repo root
        try:
            file_path_obj.relative_to(repo_root)
            return True
        except ValueError:
            pass
        
        # Check common system/SDK paths that should be excluded
        system_prefixes = (
            '/usr/include',
            '/usr/local/include',
            '/Library/Developer',
            '/Applications/Xcode',
            '/System/Library',
            '/opt/homebrew',
            '/usr/lib',
        )
        
        if any(file_path.startswith(prefix) for prefix in system_prefixes):
            return False
        
        return False
    except Exception:
        return False


def _process_single_file_for_constants_usage(args: Tuple) -> Dict[str, Any]:
    """
    Process a single source file to extract constants usage per function.
    This is a worker function for parallel processing.
    
    Args:
        args: Tuple of (source_file, repo_root, clang_args, macro_flags)
        
    Returns:
        Dict with 'file' key and 'usage' containing function->constants mappings
    """
    import time
    
    source_file, repo_root, clang_args, macro_flags = args
    start_time = time.time()
    
    # Initialize constants_map at the top level so it's accessible in exception handlers
    constants_map = {}
    rel = str(Path(source_file).name)  # Default relative path
    
    try:
        index = cindex.Index.create()
        resolved_repo_root = Path(repo_root).resolve()
        
        try:
            rel = str(Path(source_file).relative_to(resolved_repo_root))
        except ValueError:
            try:
                rel = str(Path(source_file).relative_to(repo_root))
            except ValueError:
                rel = str(Path(source_file).name)
        
        try:
            base_flags = get_clang_flags_for_file(source_file) or []
            additional_args = clang_args or []
            additional_macros = macro_flags or []
            file_flags = base_flags + additional_args + additional_macros
            tu = index.parse(str(source_file), args=file_flags)
        except Exception as e:
            logging.debug(f"[Worker] Failed to parse {rel}: {e}")
            return {'file': str(source_file), 'usage': {}, 'error': str(e)}
        
        # Build file constant registry first
        file_constants_registry = {}
        
        # OPTIMIZATION: Cache for get_tokens() results to avoid repeated expensive calls
        # Key: cursor hash (location-based), Value: list of token spellings
        tokens_cache = {}
        max_tokens_cache_size = 10000  # Limit cache size to prevent memory issues
        
        def get_cached_tokens(cursor):
            """Get tokens with caching to avoid repeated expensive clang_tokenize calls."""
            try:
                # Create a cache key based on cursor location
                loc = cursor.location
                if loc and loc.file:
                    cache_key = (loc.file.name, loc.line, loc.column)
                    
                    if cache_key in tokens_cache:
                        return tokens_cache[cache_key]
                    
                    # Only cache if we haven't exceeded the limit
                    if len(tokens_cache) < max_tokens_cache_size:
                        tokens = list(cursor.get_tokens())
                        tokens_cache[cache_key] = tokens
                        return tokens
                    else:
                        # Cache full, just get tokens without caching
                        return list(cursor.get_tokens())
                else:
                    return list(cursor.get_tokens())
            except Exception:
                return []
        
        def check_timeout():
            """Check if we've exceeded the per-file timeout (20 seconds)."""
            elapsed = time.time() - start_time
            if elapsed > 20.0:
                raise TimeoutError(f"File processing exceeded 20 second timeout after {elapsed:.1f}s")
        
        def extract_literal_value(cursor):
            """Extract literal value from cursor."""
            try:
                check_timeout()
                if cursor.kind == cindex.CursorKind.INTEGER_LITERAL:
                    tokens = get_cached_tokens(cursor)
                    if tokens:
                        token_spelling = tokens[0].spelling
                        try:
                            if token_spelling.startswith('0x') or token_spelling.startswith('0X'):
                                return int(token_spelling, 16)
                            elif token_spelling.startswith('0b') or token_spelling.startswith('0B'):
                                return int(token_spelling, 2)
                            else:
                                return int(token_spelling)
                        except ValueError:
                            try:
                                return float(token_spelling)
                            except ValueError:
                                return token_spelling
                elif cursor.kind == cindex.CursorKind.FLOATING_LITERAL:
                    tokens = get_cached_tokens(cursor)
                    if tokens:
                        try:
                            return float(tokens[0].spelling)
                        except ValueError:
                            return tokens[0].spelling
                elif cursor.kind == cindex.CursorKind.CXX_BOOL_LITERAL_EXPR:
                    tokens = get_cached_tokens(cursor)
                    if tokens:
                        return tokens[0].spelling.lower() == 'true'
            except TimeoutError:
                raise
            except Exception:
                pass
            return None
        
        # Build constant registry - only scan cursors from files within the repository
        stack = [tu.cursor]
        cursor_count = 0
        while stack:
            current = stack.pop()
            cursor_count += 1
            
            # Check timeout every 1000 cursors to avoid overhead
            if cursor_count % 1000 == 0:
                check_timeout()
            
            try:
                # OPTIMIZATION: Skip cursors from system/SDK headers
                # This significantly reduces processing time by avoiding expensive
                # get_tokens() calls on system headers
                cursor_file = None
                try:
                    if current.location and current.location.file:
                        cursor_file = current.location.file.name
                except Exception:
                    pass
                
                # Skip processing if cursor is from a system/SDK header
                if cursor_file and not _is_file_in_repo(cursor_file, repo_root, resolved_repo_root):
                    # Don't process this cursor or its children - they're from system headers
                    continue
                
                if current.kind == cindex.CursorKind.VAR_DECL:
                    if hasattr(current, 'type') and current.type:
                        type_spelling = getattr(current.type, 'spelling', '')
                        if 'const' in type_spelling or 'static' in type_spelling:
                            constant_name = current.spelling
                            if constant_name:
                                for child in current.get_children():
                                    value = extract_literal_value(child)
                                    if value is not None and isinstance(value, (int, float)):
                                        file_constants_registry[constant_name] = value
                                        break
                elif current.kind == cindex.CursorKind.ENUM_CONSTANT_DECL:
                    constant_name = current.spelling
                    if constant_name:
                        try:
                            enum_value = current.enum_value
                            if isinstance(enum_value, (int, float)):
                                file_constants_registry[constant_name] = enum_value
                        except:
                            pass
                
                try:
                    children = list(current.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    continue
            except TimeoutError:
                raise
            except Exception:
                pass
        
        def extract_constants_from_cursor(cursor):
            """Extract constants using pre-built registry."""
            constants = {}
            try:
                check_timeout()
                if cursor.kind == cindex.CursorKind.DECL_REF_EXPR:
                    ref = cursor.referenced
                    if ref and ref.kind == cindex.CursorKind.VAR_DECL:
                        constant_name = ref.spelling
                        if constant_name in file_constants_registry:
                            constants[constant_name] = file_constants_registry[constant_name]
                elif cursor.kind == cindex.CursorKind.UNEXPOSED_EXPR:
                    try:
                        # OPTIMIZATION: Only call get_tokens() if we have constants to look up
                        if file_constants_registry:
                            tokens = get_cached_tokens(cursor)
                            if tokens and len(tokens) == 1:
                                token_spelling = tokens[0].spelling
                                if token_spelling and token_spelling.isidentifier():
                                    if token_spelling in file_constants_registry:
                                        constants[token_spelling] = file_constants_registry[token_spelling]
                    except:
                        pass
            except TimeoutError:
                raise
            except Exception:
                pass
            return constants
        
        def traverse_for_constants(node, collected_constants):
            """Traverse AST to find constants usage."""
            stack = [node]
            while stack:
                current = stack.pop()
                node_constants = extract_constants_from_cursor(current)
                collected_constants.update(node_constants)
                try:
                    children = list(current.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    continue
        
        # Collect function constants usage - only scan cursors from files within the repository
        stack = [tu.cursor]
        while stack:
            cursor = stack.pop()
            try:
                # OPTIMIZATION: Skip cursors from system/SDK headers
                cursor_file = None
                try:
                    if cursor.location and cursor.location.file:
                        cursor_file = cursor.location.file.name
                except Exception:
                    pass
                
                # Skip processing if cursor is from a system/SDK header
                if cursor_file and not _is_file_in_repo(cursor_file, repo_root, resolved_repo_root):
                    continue
                
                try:
                    cursor_kind = cursor.kind
                except ValueError:
                    try:
                        children = list(cursor.get_children())
                        for child in reversed(children):
                            stack.append(child)
                    except Exception:
                        pass
                    continue
                
                if cursor_kind in ALLOWED_FUNCTION_KINDS and cursor.is_definition():
                    # Get function name
                    parts = []
                    current = cursor
                    valid_parent_kinds = {
                        cindex.CursorKind.NAMESPACE,
                        cindex.CursorKind.CLASS_DECL,
                        cindex.CursorKind.STRUCT_DECL,
                        cindex.CursorKind.ENUM_DECL,
                    }
                    while current:
                        if getattr(current, "spelling", None):
                            spelling = current.spelling
                            if spelling and current.kind not in (
                                cindex.CursorKind.TRANSLATION_UNIT,
                                cindex.CursorKind.NAMESPACE_REF,
                            ):
                                if current == cursor or current.kind in valid_parent_kinds:
                                    parts.append(spelling)
                        if hasattr(current, "semantic_parent") and current.semantic_parent:
                            current = current.semantic_parent
                            if current.kind == cindex.CursorKind.TRANSLATION_UNIT:
                                break
                        else:
                            break
                    parts.reverse()
                    fn_name = "::".join(parts) if parts else cursor.spelling
                    
                    if fn_name:
                        collected_constants = {}
                        
                        # Analyze function body for constants
                        try:
                            for child in cursor.get_children():
                                traverse_for_constants(child, collected_constants)
                        except Exception:
                            pass
                        
                        if collected_constants:
                            constants_map[fn_name] = collected_constants
                
                # Add children to stack
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
                    
            except Exception as e:
                logging.debug(f"[Worker] Error processing cursor: {e}")
                try:
                    children = list(cursor.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    pass
        
        # Log per-file timing for slow files (> 5 seconds)
        elapsed = time.time() - start_time
        if elapsed > 5.0:
            logging.info(f"[Worker] SLOW FILE: {rel} took {elapsed:.1f}s - found {len(constants_map)} functions with constants")
        
        return {'file': str(source_file), 'usage': constants_map}
    
    except TimeoutError as e:
        elapsed = time.time() - start_time
        logging.warning(f"[Worker] TIMEOUT: {rel} exceeded 20s timeout after {elapsed:.1f}s - returning partial results ({len(constants_map)} functions)")
        return {'file': str(source_file), 'usage': constants_map, 'error': f'timeout after {elapsed:.1f}s'}
        
    except Exception as e:
        elapsed = time.time() - start_time
        logging.debug(f"[Worker] Exception processing {source_file} after {elapsed:.1f}s: {e}")
        return {'file': str(source_file), 'usage': {}, 'error': str(e)}


class CASTUtil:
    """
    Utility class for analyzing C/C++/ObjC code using libclang.
    Provides:
      - Function definition collection
      - Call graph construction
      - Nested call graph generation
      - Data type usage analysis
    """

    # Class-level cache for preprocessor macros to avoid redundant scanning
    _macro_cache = {}

    # ------------------------ Setup Helpers ------------------------

    @staticmethod
    def get_libclang_path_from_platform():
        """Return libclang.dylib path if installed via brew or other platform package managers."""
        return Environment.get_libclang_path_from_platform()


    @staticmethod
    def find_source_files(repo_root: Path, ignored_dirs: Set[str]):
        """Find all source/header files in repo that match SUPPORTED_EXTENSIONS."""
        from ...utils.file_filter_util import find_files_with_extensions
        return find_files_with_extensions(repo_root, ignored_dirs, set(SUPPORTED_EXTENSIONS))

    @staticmethod
    def get_cached_preprocessor_macros(source_files: List[Path]) -> Set[str]:
        """
        Get preprocessor macros with caching to avoid redundant scanning.
        Uses a cache key based on the sorted list of source file paths and their modification times.
        """
        macros, _ = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
        return macros

    @staticmethod
    def get_cached_preprocessor_macros_with_derived(source_files: List[Path], include_headers: bool = True) -> Tuple[Set[str], Dict[str, str]]:
        """
        Get preprocessor macros with caching to avoid redundant scanning.
        Returns both the set of all macros AND a dictionary mapping derived macros to their base dependencies.
        Uses a cache key based on the sorted list of source file paths and their modification times.
        
        Args:
            source_files: List of source file paths to scan
            include_headers: If True, also scan header files for derived macro definitions
            
        Returns:
            Tuple of:
            - Set of all macro names (both direct and derived)
            - Dict mapping derived macro names to their base macro dependencies
        """
        # Create a cache key based on file paths and modification times
        cache_key_data = []
        for source_file in sorted(source_files):
            try:
                mtime = source_file.stat().st_mtime
                cache_key_data.append((str(source_file), mtime))
            except Exception:
                # If we can't get mtime, use the path only
                cache_key_data.append((str(source_file), 0))

        cache_key = hash(tuple(cache_key_data))
        derived_cache_key = f"{cache_key}_derived"

        # Check if we have cached results
        if cache_key in CASTUtil._macro_cache and derived_cache_key in CASTUtil._macro_cache:
            cached_macros = CASTUtil._macro_cache[cache_key]
            cached_derived = CASTUtil._macro_cache[derived_cache_key]
            logging.info(f"[+] Using cached preprocessor macros: {len(cached_macros)} macros found ({len(cached_derived)} derived)")
            return cached_macros, cached_derived

        # Not in cache, scan and cache the results
        macros, derived_macros = detect_preprocessor_macros_with_derived(source_files, include_headers)
        CASTUtil._macro_cache[cache_key] = macros
        CASTUtil._macro_cache[derived_cache_key] = derived_macros
        return macros, derived_macros

    @staticmethod
    def clear_macro_cache():
        """Clear the preprocessor macro cache. Useful when analyzing different repositories."""
        CASTUtil._macro_cache.clear()
        logging.info("[+] Cleared preprocessor macro cache")
    
    @staticmethod
    def clear_all_caches():
        """
        Clear all caches used by CASTUtil to free memory.
        This should be called after completing AST analysis operations.
        
        Clears:
        - Preprocessor macro cache (_macro_cache)
        - LRU caches (strip_template_params, _std_consts)
        """
        # Clear preprocessor macro cache
        CASTUtil._macro_cache.clear()
        
        # Clear LRU caches
        strip_template_params.cache_clear()
        CASTUtil._std_consts.cache_clear()
        
        logging.info("[+] Cleared all CASTUtil caches (macro cache, LRU caches)")

    # ------------------------ Parallel Processing Methods ------------------------

    @staticmethod
    def _should_use_parallel(source_files: List, use_parallel: Optional[bool] = None,
                             max_workers: Optional[int] = None) -> Tuple[bool, int]:
        """
        Determine whether to use parallel processing based on configuration and file count.
        
        Args:
            source_files: List of source files to process
            use_parallel: Explicit parallel flag (None means use default)
            max_workers: Maximum number of worker processes (None means use default)
            
        Returns:
            Tuple of (should_use_parallel, num_workers)
        """
        # Determine if parallel should be used
        if use_parallel is None:
            # Default: use parallel if file count exceeds threshold
            should_parallel = len(source_files) >= AST_MIN_FILES_FOR_PARALLEL and AST_DEFAULT_PARALLEL_ENABLED
        else:
            should_parallel = use_parallel
        
        # Determine number of workers
        if max_workers is None:
            num_workers = min(AST_DEFAULT_MAX_WORKERS, cpu_count())
        else:
            num_workers = min(max_workers, cpu_count())
        
        # Don't use parallel if only 1 worker or too few files
        if num_workers <= 1 or len(source_files) < AST_MIN_FILES_FOR_PARALLEL:
            should_parallel = False
        
        return should_parallel, num_workers

    @staticmethod
    def _merge_parallel_function_results(results: List[Dict]) -> Dict:
        """
        Merge function registry results from parallel workers.
        
        Args:
            results: List of result dicts from worker processes
            
        Returns:
            Merged registry dict
        """
        merged = {}
        for result in results:
            if 'error' in result:
                continue
            for fn_name, entries in result.get('registry', {}).items():
                if fn_name not in merged:
                    merged[fn_name] = set()
                for entry in entries:
                    # Convert list back to tuple for set operations
                    merged[fn_name].add(tuple(entry))
        return merged

    @staticmethod
    def _merge_parallel_data_types_results(results: List[Dict]) -> Dict:
        """
        Merge data types registry results from parallel workers.
        
        Args:
            results: List of result dicts from worker processes
            
        Returns:
            Merged registry dict
        """
        merged = {}
        for result in results:
            if 'error' in result:
                continue
            for type_name, file_entries in result.get('registry', {}).items():
                if type_name not in merged:
                    merged[type_name] = []
                for entry in file_entries:
                    if entry not in merged[type_name]:
                        merged[type_name].append(entry)
        return merged

    @staticmethod
    def _merge_parallel_call_graph_results(results: List[Dict]) -> Dict:
        """
        Merge call graph results from parallel workers.
        
        Args:
            results: List of result dicts from worker processes
            
        Returns:
            Merged call graph dict
        """
        merged = {}
        for result in results:
            if 'error' in result:
                continue
            for caller, callees in result.get('edges', {}).items():
                if caller not in merged:
                    merged[caller] = set()
                merged[caller].update(callees)
        
        # Convert sets to sorted lists and deduplicate
        final = {}
        for caller, callees in merged.items():
            pick = {}
            for callee in callees:
                b = base_function_name(callee)
                cur = pick.get(b)
                if cur is None or ('(' not in cur and '(' in callee):
                    pick[b] = callee
            final[caller] = sorted(pick.values())
        
        return final

    @staticmethod
    def _build_function_registry_parallel(repo_root, source_files, clang_args, macro_flags,
                                          max_workers: int = AST_DEFAULT_MAX_WORKERS):
        """
        Build function registry using parallel processing.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macro_flags: Macro definition flags
            max_workers: Maximum number of worker processes
            
        Returns:
            Registry dict mapping function names to location sets
        """
        logging.info(f"[+] Building function registry in parallel with {max_workers} workers...")
        
        # Prepare arguments for each file
        work_items = [
            (str(sf), str(repo_root), clang_args or [], macro_flags or [])
            for sf in source_files
        ]
        
        results = []
        errors = 0
        total_files = len(work_items)
        completed = 0
        last_logged_pct = 0
        
        # Get the current log file path to share with workers
        from hindsight.utils.log_util import LogUtil
        current_log_file = LogUtil.get_current_log_file()
        
        try:
            with ProcessPoolExecutor(max_workers=max_workers,
                                     initializer=_init_worker_process,
                                     initargs=(current_log_file,)) as executor:
                futures = {executor.submit(_process_single_file_for_functions, item): item[0]
                          for item in work_items}
                
                for future in as_completed(futures):
                    file_path = futures[future]
                    try:
                        result = future.result(timeout=300)  # 5 minute timeout per file
                        results.append(result)
                        if 'error' in result:
                            errors += 1
                    except Exception as e:
                        logging.debug(f"[Parallel] Worker failed for {file_path}: {e}")
                        errors += 1
                        results.append({'file': file_path, 'registry': {}, 'error': str(e)})
                    
                    # Progress tracking
                    completed += 1
                    pct = (completed * 100) // total_files
                    if pct >= last_logged_pct + 10 or completed == total_files:
                        logging.info(f"[+] Function registry progress: {completed}/{total_files} files - Completed {pct}%")
                        last_logged_pct = (pct // 10) * 10
        
        except Exception as e:
            logging.warning(f"[Parallel] ProcessPoolExecutor failed: {e}, falling back to sequential")
            # Fallback to sequential processing
            return CASTUtil._build_function_registry_single_pass(
                repo_root, source_files, clang_args, macro_flags
            )
        
        if errors > 0:
            logging.info(f"[+] Parallel processing completed with {errors} errors out of {len(source_files)} files")
        
        # Merge results
        merged = CASTUtil._merge_parallel_function_results(results)
        logging.info(f"[+] Parallel function registry: {len(merged)} functions from {len(source_files)} files")
        
        return merged

    @staticmethod
    def _build_data_types_registry_parallel(repo_root, source_files, clang_args, macro_flags,
                                            max_workers: int = AST_DEFAULT_MAX_WORKERS):
        """
        Build data types registry using parallel processing.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macro_flags: Macro definition flags
            max_workers: Maximum number of worker processes
            
        Returns:
            Registry dict mapping data type names to file entries
        """
        logging.info(f"[+] Building data types registry in parallel with {max_workers} workers...")
        
        # Prepare arguments for each file
        work_items = [
            (str(sf), str(repo_root), clang_args or [], macro_flags or [])
            for sf in source_files
        ]
        
        results = []
        errors = 0
        total_files = len(work_items)
        completed = 0
        last_logged_pct = 0
        
        # Get the current log file path to share with workers
        from hindsight.utils.log_util import LogUtil
        current_log_file = LogUtil.get_current_log_file()
        
        try:
            with ProcessPoolExecutor(max_workers=max_workers,
                                     initializer=_init_worker_process,
                                     initargs=(current_log_file,)) as executor:
                futures = {executor.submit(_process_single_file_for_data_types, item): item[0]
                          for item in work_items}
                
                for future in as_completed(futures):
                    file_path = futures[future]
                    try:
                        result = future.result(timeout=300)
                        results.append(result)
                        if 'error' in result:
                            errors += 1
                    except Exception as e:
                        logging.debug(f"[Parallel] Worker failed for {file_path}: {e}")
                        errors += 1
                        results.append({'file': file_path, 'registry': {}, 'error': str(e)})
                    
                    # Progress tracking
                    completed += 1
                    pct = (completed * 100) // total_files
                    if pct >= last_logged_pct + 10 or completed == total_files:
                        logging.info(f"[+] Data types registry progress: {completed}/{total_files} files - Completed {pct}%")
                        last_logged_pct = (pct // 10) * 10
        
        except Exception as e:
            logging.warning(f"[Parallel] ProcessPoolExecutor failed: {e}, falling back to sequential")
            return CASTUtil._build_data_types_registry_single_pass(
                repo_root, source_files, clang_args, macro_flags
            )
        
        if errors > 0:
            logging.info(f"[+] Parallel processing completed with {errors} errors out of {len(source_files)} files")
        
        # Merge results
        merged = CASTUtil._merge_parallel_data_types_results(results)
        logging.info(f"[+] Parallel data types registry: {len(merged)} types from {len(source_files)} files")
        
        return merged

    @staticmethod
    def _build_forward_call_graph_parallel(repo_root, source_files, clang_args, macro_flags,
                                           filter_external_calls=False, registry=None,
                                           max_workers: int = AST_DEFAULT_MAX_WORKERS):
        """
        Build forward call graph using parallel processing.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macro_flags: Macro definition flags
            filter_external_calls: If True, filter callees to only repo-defined functions
            registry: Optional set of known function names to filter against
            max_workers: Maximum number of worker processes
            
        Returns:
            Dict mapping caller names to lists of callee names
        """
        logging.info(f"[+] Building call graph in parallel with {max_workers} workers...")
        
        # Convert registry to list for pickling (sets can't be pickled directly in some cases)
        defined_funcs_list = list(registry) if registry else None
        
        # Prepare arguments for each file
        work_items = [
            (str(sf), str(repo_root), clang_args or [], macro_flags or [],
             filter_external_calls, defined_funcs_list)
            for sf in source_files
        ]
        
        results = []
        errors = 0
        total_files = len(work_items)
        completed = 0
        last_logged_pct = 0
        
        # Get the current log file path to share with workers
        from hindsight.utils.log_util import LogUtil
        current_log_file = LogUtil.get_current_log_file()
        
        try:
            with ProcessPoolExecutor(max_workers=max_workers,
                                     initializer=_init_worker_process,
                                     initargs=(current_log_file,)) as executor:
                futures = {executor.submit(_process_single_file_for_call_graph, item): item[0]
                          for item in work_items}
                
                for future in as_completed(futures):
                    file_path = futures[future]
                    try:
                        result = future.result(timeout=300)
                        results.append(result)
                        if 'error' in result:
                            errors += 1
                    except Exception as e:
                        logging.debug(f"[Parallel] Worker failed for {file_path}: {e}")
                        errors += 1
                        results.append({'file': file_path, 'edges': {}, 'error': str(e)})
                    
                    # Progress tracking
                    completed += 1
                    pct = (completed * 100) // total_files
                    if pct >= last_logged_pct + 10 or completed == total_files:
                        logging.info(f"[+] Call graph progress: {completed}/{total_files} files - Completed {pct}%")
                        last_logged_pct = (pct // 10) * 10
        
        except Exception as e:
            logging.warning(f"[Parallel] ProcessPoolExecutor failed: {e}, falling back to sequential")
            return CASTUtil._build_forward_call_graph_single_pass(
                repo_root, source_files, clang_args, macro_flags,
                filter_external_calls, registry
            )
        
        if errors > 0:
            logging.info(f"[+] Parallel processing completed with {errors} errors out of {len(source_files)} files")
        
        # Merge results
        merged = CASTUtil._merge_parallel_call_graph_results(results)
        total_edges = sum(len(callees) for callees in merged.values())
        logging.info(f"[+] Parallel call graph: {len(merged)} callers, {total_edges} edges from {len(source_files)} files")
        
        return merged

    @staticmethod
    def _merge_parallel_data_type_usage_results(results: List[Dict]) -> Dict:
        """
        Merge data type usage results from parallel workers.
        
        Args:
            results: List of result dicts from worker processes
            
        Returns:
            Merged usage dict mapping function names to type lists
        """
        merged = {}
        for result in results:
            if 'error' in result:
                continue
            for fn_name, types in result.get('usage', {}).items():
                if fn_name not in merged:
                    merged[fn_name] = set()
                if isinstance(types, list):
                    merged[fn_name].update(types)
                else:
                    merged[fn_name].add(types)
        
        # Convert sets to sorted lists
        return {fn: sorted(types) for fn, types in merged.items()}

    @staticmethod
    def _merge_parallel_constants_usage_results(results: List[Dict]) -> Dict:
        """
        Merge constants usage results from parallel workers.
        
        Args:
            results: List of result dicts from worker processes
            
        Returns:
            Merged usage dict mapping function names to constants dicts
        """
        merged = {}
        for result in results:
            if 'error' in result:
                continue
            for fn_name, constants in result.get('usage', {}).items():
                if fn_name not in merged:
                    merged[fn_name] = {}
                merged[fn_name].update(constants)
        
        return merged

    @staticmethod
    def _build_data_type_use_parallel(repo_root, source_files, clang_args, macro_flags,
                                       custom_types_registry=None, max_workers: int = AST_DEFAULT_MAX_WORKERS):
        """
        Build data type usage using parallel processing.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macro_flags: Macro definition flags
            custom_types_registry: Optional set of known custom type names to filter against
            max_workers: Maximum number of worker processes
            
        Returns:
            Dict mapping function names to lists of data types they use
        """
        logging.info(f"[+] Building data type usage in parallel with {max_workers} workers...")
        
        # Convert custom_types_registry to list for pickling
        custom_types_list = list(custom_types_registry) if custom_types_registry else None
        
        # Prepare arguments for each file
        work_items = [
            (str(sf), str(repo_root), clang_args or [], macro_flags or [], custom_types_list)
            for sf in source_files
        ]
        
        results = []
        errors = 0
        total_files = len(work_items)
        completed = 0
        last_logged_pct = 0
        
        # Get the current log file path to share with workers
        from hindsight.utils.log_util import LogUtil
        current_log_file = LogUtil.get_current_log_file()
        
        try:
            with ProcessPoolExecutor(max_workers=max_workers,
                                     initializer=_init_worker_process,
                                     initargs=(current_log_file,)) as executor:
                futures = {executor.submit(_process_single_file_for_data_type_usage, item): item[0]
                          for item in work_items}
                
                for future in as_completed(futures):
                    file_path = futures[future]
                    try:
                        result = future.result(timeout=300)  # 5 minute timeout per file
                        results.append(result)
                        if 'error' in result:
                            errors += 1
                    except Exception as e:
                        logging.debug(f"[Parallel] Worker failed for {file_path}: {e}")
                        errors += 1
                        results.append({'file': file_path, 'usage': {}, 'error': str(e)})
                    
                    # Progress tracking
                    completed += 1
                    pct = (completed * 100) // total_files
                    if pct >= last_logged_pct + 10 or completed == total_files:
                        logging.info(f"[+] Data type usage progress: {completed}/{total_files} files - Completed {pct}%")
                        last_logged_pct = (pct // 10) * 10
        
        except Exception as e:
            logging.warning(f"[Parallel] ProcessPoolExecutor failed: {e}, falling back to sequential")
            # Fallback to sequential processing
            return CASTUtil._build_data_type_use_single_pass(
                repo_root, source_files, clang_args, macro_flags, custom_types_registry
            )
        
        if errors > 0:
            logging.info(f"[+] Parallel processing completed with {errors} errors out of {len(source_files)} files")
        
        # Merge results
        merged = CASTUtil._merge_parallel_data_type_usage_results(results)
        logging.info(f"[+] Parallel data type usage: {len(merged)} functions from {len(source_files)} files")
        
        return merged

    @staticmethod
    def _build_constants_usage_parallel(repo_root, source_files, clang_args, macro_flags,
                                         function_registry=None, max_workers: int = AST_DEFAULT_MAX_WORKERS):
        """
        Build constants usage using parallel processing.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macro_flags: Macro definition flags
            function_registry: Optional set of known function names to filter against
            max_workers: Maximum number of worker processes
            
        Returns:
            Dict mapping function names to dicts of constants they use
        """
        logging.info(f"[+] Building constants usage in parallel with {max_workers} workers...")
        
        # Prepare arguments for each file
        work_items = [
            (str(sf), str(repo_root), clang_args or [], macro_flags or [])
            for sf in source_files
        ]
        
        results = []
        errors = 0
        total_files = len(work_items)
        completed = 0
        last_logged_pct = 0
        
        # Get the current log file path to share with workers
        from hindsight.utils.log_util import LogUtil
        current_log_file = LogUtil.get_current_log_file()
        
        try:
            with ProcessPoolExecutor(max_workers=max_workers,
                                     initializer=_init_worker_process,
                                     initargs=(current_log_file,)) as executor:
                futures = {executor.submit(_process_single_file_for_constants_usage, item): item[0]
                          for item in work_items}
                
                for future in as_completed(futures):
                    file_path = futures[future]
                    try:
                        result = future.result(timeout=300)  # 5 minute timeout per file
                        results.append(result)
                        if 'error' in result:
                            errors += 1
                    except TimeoutError:
                        logging.warning(f"[Parallel] TIMEOUT: Worker timed out after 300s for {file_path}")
                        errors += 1
                        results.append({'file': file_path, 'usage': {}, 'error': 'timeout'})
                    except Exception as e:
                        logging.warning(f"[Parallel] Worker failed for {file_path}: {e}")
                        errors += 1
                        results.append({'file': file_path, 'usage': {}, 'error': str(e)})
                    
                    # Progress tracking
                    completed += 1
                    pct = (completed * 100) // total_files
                    if pct >= last_logged_pct + 10 or completed == total_files:
                        logging.info(f"[+] Constants usage progress: {completed}/{total_files} files - Completed {pct}%")
                        last_logged_pct = (pct // 10) * 10
                    
                    # Log every file at debug level to help identify stuck files
                    logging.debug(f"[+] Processed file {completed}/{total_files}: {file_path}")
        
        except Exception as e:
            logging.warning(f"[Parallel] ProcessPoolExecutor failed: {e}, falling back to sequential")
            # Fallback to sequential processing
            return CASTUtil._build_constants_usage_single_pass(
                repo_root, source_files, clang_args, macro_flags, function_registry
            )
        
        if errors > 0:
            logging.info(f"[+] Parallel processing completed with {errors} errors out of {len(source_files)} files")
        
        # Merge results
        merged = CASTUtil._merge_parallel_constants_usage_results(results)
        logging.info(f"[+] Parallel constants usage: {len(merged)} functions from {len(source_files)} files")
        
        return merged

    @staticmethod
    def build_all_registries_parallel(repo_root, source_files, clang_args,
                                      use_parallel: Optional[bool] = None,
                                      max_workers: Optional[int] = None,
                                      macros: List[str] = None,
                                      expand_macros: bool = True,
                                      filter_external_calls: bool = False):
        """
        Build all registries (functions, data types, call graph) with optional parallel processing.
        This is the main entry point for parallel AST generation.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            use_parallel: If True, use parallel processing. If None, auto-detect based on file count.
            max_workers: Maximum number of worker processes. If None, use default.
            macros: Optional list of macros to expand
            expand_macros: If True, build AST twice (with and without macros) and merge results
            filter_external_calls: If True, filter callees to only repo-defined functions
            
        Returns:
            Tuple of (function_registry, data_types_registry, call_graph)
        """
        should_parallel, num_workers = CASTUtil._should_use_parallel(
            source_files, use_parallel, max_workers
        )
        
        if should_parallel:
            logging.info(f"[+] Using parallel AST generation with {num_workers} workers for {len(source_files)} files")
        else:
            logging.info(f"[+] Using sequential AST generation for {len(source_files)} files")
        
        # Determine macro flags
        if expand_macros:
            if macros is not None:
                if macros:
                    macro_flags = create_macro_flags(set(macros))
                else:
                    detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                    macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
            else:
                macro_flags = []
        else:
            macro_flags = []
        
        # Build function registry
        if should_parallel:
            if expand_macros:
                # Pass 1: Without macros
                registry1 = CASTUtil._build_function_registry_parallel(
                    repo_root, source_files, clang_args, [], num_workers
                )
                # Pass 2: With macros
                registry2 = CASTUtil._build_function_registry_parallel(
                    repo_root, source_files, clang_args, macro_flags, num_workers
                )
                function_registry = CASTUtil._merge_function_registries(registry1, registry2)
            else:
                function_registry = CASTUtil._build_function_registry_parallel(
                    repo_root, source_files, clang_args, macro_flags, num_workers
                )
        else:
            if expand_macros:
                function_registry = CASTUtil._build_function_registry_expand_macros(
                    repo_root, source_files, clang_args, macros
                )
            else:
                function_registry = CASTUtil._build_function_registry_single_pass(
                    repo_root, source_files, clang_args, macro_flags
                )
        
        defined_funcs = set(function_registry.keys())
        
        # Build data types registry
        if should_parallel:
            if expand_macros:
                dt_registry1 = CASTUtil._build_data_types_registry_parallel(
                    repo_root, source_files, clang_args, [], num_workers
                )
                dt_registry2 = CASTUtil._build_data_types_registry_parallel(
                    repo_root, source_files, clang_args, macro_flags, num_workers
                )
                data_types_registry = CASTUtil._merge_data_types_registries(dt_registry1, dt_registry2)
            else:
                data_types_registry = CASTUtil._build_data_types_registry_parallel(
                    repo_root, source_files, clang_args, macro_flags, num_workers
                )
        else:
            if expand_macros:
                data_types_registry = CASTUtil._build_data_types_registry_expand_macros(
                    repo_root, source_files, clang_args, macros
                )
            else:
                data_types_registry = CASTUtil._build_data_types_registry_single_pass(
                    repo_root, source_files, clang_args, macro_flags
                )
        
        # Build call graph
        if should_parallel:
            if expand_macros:
                cg1 = CASTUtil._build_forward_call_graph_parallel(
                    repo_root, source_files, clang_args, [],
                    filter_external_calls, defined_funcs, num_workers
                )
                cg2 = CASTUtil._build_forward_call_graph_parallel(
                    repo_root, source_files, clang_args, macro_flags,
                    filter_external_calls, defined_funcs, num_workers
                )
                call_graph = CASTUtil._merge_call_graphs(cg1, cg2)
            else:
                call_graph = CASTUtil._build_forward_call_graph_parallel(
                    repo_root, source_files, clang_args, macro_flags,
                    filter_external_calls, defined_funcs, num_workers
                )
        else:
            if expand_macros:
                call_graph = CASTUtil._build_forward_call_graph_expand_macros(
                    repo_root, source_files, clang_args, macros,
                    filter_external_calls, defined_funcs
                )
            else:
                call_graph = CASTUtil._build_forward_call_graph_single_pass(
                    repo_root, source_files, clang_args, macro_flags,
                    filter_external_calls, defined_funcs
                )
        
        logging.info(f"[+] All registries built: {len(function_registry)} functions, "
                    f"{len(data_types_registry)} data types, {len(call_graph)} callers")
        
        return function_registry, data_types_registry, call_graph

    # ------------------------ Naming Helpers ------------------------

    @staticmethod
    def get_fully_qualified_name(cursor):
        """Return fully qualified name for a function/method cursor, excluding file paths."""
        parts = []
        current = cursor

        # Valid parent kinds that should be included in qualified names
        valid_parent_kinds = {
            cindex.CursorKind.NAMESPACE,
            cindex.CursorKind.CLASS_DECL,
            cindex.CursorKind.STRUCT_DECL,
            cindex.CursorKind.ENUM_DECL,
            getattr(cindex.CursorKind, "CLASS_TEMPLATE", None),
            getattr(cindex.CursorKind, "OBJC_INTERFACE_DECL", None),
            getattr(cindex.CursorKind, "OBJC_IMPLEMENTATION_DECL", None),
            getattr(cindex.CursorKind, "OBJC_PROTOCOL_DECL", None),
        }
        # Remove None values
        valid_parent_kinds = {k for k in valid_parent_kinds if k is not None}

        while current:
            if getattr(current, "spelling", None):
                spelling = current.spelling
                if spelling and current.kind not in (
                    cindex.CursorKind.TRANSLATION_UNIT,
                    cindex.CursorKind.NAMESPACE_REF,
                    cindex.CursorKind.TEMPLATE_REF,
                    cindex.CursorKind.TYPE_REF,
                ):
                    # Check if this is an unnamed type with the old format
                    if (("(unnamed " in spelling) and " at " in spelling and spelling.endswith(")")):
                        # Generate a proper name for unnamed types
                        try:
                            start = current.extent.start
                            file_path = start.file.name if start.file else None
                            spelling = generate_unnamed_type_name(current, file_path)
                        except Exception:
                            # Fallback to a simple unnamed name if generation fails
                            spelling = "unnamed_unknown_unknown"
                    
                    # Only include this part if it's the function itself or a valid parent
                    if current == cursor or current.kind in valid_parent_kinds:
                        parts.append(spelling)

            if hasattr(current, "semantic_parent") and current.semantic_parent:
                current = current.semantic_parent
                if current.kind == cindex.CursorKind.TRANSLATION_UNIT:
                    break
            else:
                break

        parts.reverse()
        return "::".join(parts) if parts else None

    @staticmethod
    def format_function_name(cursor):
        """Return formatted function/method name with full qualification."""
        if cursor.kind not in ALLOWED_FUNCTION_KINDS:
            return None
        
        qualified_name = CASTUtil.get_fully_qualified_name(cursor)
        if qualified_name:
            return qualified_name
        
        # Check if cursor.spelling is an unnamed type with old format
        spelling = cursor.spelling
        if spelling and ("(unnamed " in spelling) and " at " in spelling and spelling.endswith(")"):
            # Generate a proper name for unnamed types
            try:
                start = cursor.extent.start
                file_path = start.file.name if start.file else None
                return generate_unnamed_type_name(cursor, file_path)
            except Exception:
                # Fallback to a simple unnamed name if generation fails
                return "unnamed_unknown_unknown"
        
        return spelling or None

    @staticmethod
    def format_name_with_signature(cursor):
        """Return qualified name + parameter type signature."""
        base = CASTUtil.format_function_name(cursor)
        if not base:
            return None

        param_types = []
        try:
            for arg in cursor.get_arguments():
                t = getattr(arg, "type", None)
                if t is None:
                    param_types.append("")
                else:
                    if hasattr(t, "get_canonical"):
                        t = t.get_canonical()
                    param_types.append(getattr(t, "spelling", "") or "")
        except Exception:
            pass
        signature = "(" + ",".join(param_types) + ")"

        try:
            if cursor.kind == getattr(cindex.CursorKind, "CXX_METHOD", None):
                if hasattr(cursor, "is_const_method") and cursor.is_const_method():
                    signature += " const"
        except Exception:
            pass

        return base + signature

    # ------------------------ Registry ------------------------

    @staticmethod
    def build_function_registry(repo_root, source_files, clang_args, out_path,
                                 macros: List[str] = None,
                                 expand_macros: bool = True,
                                 use_parallel: Optional[bool] = None,
                                 max_workers: Optional[int] = None):
        """
        Collect all function/method definitions with file + line/byte extents.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            out_path: Output path for registry JSON (can be None to skip writing)
            macros: Optional list of macros to expand. If provided (even empty list triggers auto-detect),
                    macro expansion is enabled. If None, no macro expansion.
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
            use_parallel: If True, use parallel processing. If None, auto-detect based on file count.
            max_workers: Maximum number of worker processes. If None, use default.
        
        Returns:
            Tuple of (set of function names, registry dict)
        """
        # Determine if parallel processing should be used
        should_parallel, num_workers = CASTUtil._should_use_parallel(
            source_files, use_parallel, max_workers
        )
        
        # Determine macro flags for parallel processing
        if expand_macros:
            if macros is not None:
                if macros:
                    macro_flags = create_macro_flags(set(macros))
                else:
                    detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                    macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
            else:
                macro_flags = []
        else:
            macro_flags = []
        
        if should_parallel:
            logging.info(f"[+] Building function registry in parallel with {num_workers} workers...")
            if expand_macros:
                # Pass 1: Without macros
                registry1 = CASTUtil._build_function_registry_parallel(
                    repo_root, source_files, clang_args, [], num_workers
                )
                # Pass 2: With macros
                registry2 = CASTUtil._build_function_registry_parallel(
                    repo_root, source_files, clang_args, macro_flags, num_workers
                )
                registry = CASTUtil._merge_function_registries(registry1, registry2)
            else:
                registry = CASTUtil._build_function_registry_parallel(
                    repo_root, source_files, clang_args, macro_flags, num_workers
                )
        elif expand_macros:
            logging.info(f"[+] Building function registry with expand macros mode (dual-pass)...")
            registry = CASTUtil._build_function_registry_expand_macros(
                repo_root, source_files, clang_args, macros
            )
        else:
            logging.info(f"[+] Building function registry with single-pass approach...")
            # Single pass: Build registry without macros defined
            registry = CASTUtil._build_function_registry_single_pass(
                repo_root, source_files, clang_args, []
            )

        logging.info(f"[+] Function registry completed: {len(registry)} functions found")

        # Convert to JSON output format with new schema - wrap in "function_to_location"
        function_data = {
            fn: [{
                "file_name": rec[0],
                "start": rec[1],
                "end": rec[2],
            } for rec in sorted(entries)]
            for fn, entries in registry.items()
        }

        json_output = {
            "function_to_location": function_data
        }

        # Only write to file if out_path is provided
        if out_path is not None:
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, indent=2, sort_keys=True)
            total_defs = sum(len(v) for v in json_output.values())
            logging.info(f"[+] Wrote {total_defs} entries for {len(registry)} functions to {out_path}")
        else:
            total_defs = sum(len(v) for v in json_output.values())
            logging.info(f"[+] Built function registry with {total_defs} entries for {len(registry)} functions (no output file specified)")

        return set(registry.keys()), registry

    @staticmethod
    def _build_function_registry_expand_macros(repo_root, source_files, clang_args, macros: List[str] = None):
        """
        Build function registry with dual-pass macro handling (expand macros mode).
        
        This method builds the AST twice:
        1. Pass 1: Without macro expansion - captures code in #else branches
        2. Pass 2: With macro expansion - captures code in #if branches
        
        The results are merged to capture all code paths regardless of macro state.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macros: Optional list of macros to expand. If empty list, auto-detect macros.
                    If None, skip pass 2.
        
        Returns:
            Merged registry dict
        """
        logging.info("=" * 60)
        logging.info("EXPAND MACROS MODE: Building function registry twice (with and without macros)")
        logging.info("=" * 60)
        
        # PASS 1: Without macro expansion
        logging.info("PASS 1: Building function registry WITHOUT macro expansion...")
        registry1 = CASTUtil._build_function_registry_single_pass(
            repo_root, source_files, clang_args, []
        )
        logging.info(f"Pass 1 found: {len(registry1)} functions")
        
        # PASS 2: With macro expansion
        logging.info("PASS 2: Building function registry WITH macro expansion...")
        if macros is not None:
            if macros:
                # Use provided macros
                macro_flags = create_macro_flags(set(macros))
                logging.info(f"[+] Using {len(macros)} provided macros for expansion")
            else:
                # Auto-detect macros, excluding derived ones
                detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
                logging.info(f"[+] Auto-detected {len(detected_macros)} macros for expansion ({len(derived_macros)} derived, excluded)")
        else:
            # No macros specified, use empty list for pass 2
            macro_flags = []
            logging.info("[+] No macros specified, pass 2 will use empty macro flags")
        
        registry2 = CASTUtil._build_function_registry_single_pass(
            repo_root, source_files, clang_args, macro_flags
        )
        logging.info(f"Pass 2 found: {len(registry2)} functions")
        
        # MERGE: Combine results from both passes
        logging.info("MERGING: Combining results from both passes...")
        merged_registry = CASTUtil._merge_function_registries(registry1, registry2)
        logging.info(f"Merged total: {len(merged_registry)} functions")
        
        return merged_registry

    @staticmethod
    def _build_function_registry_single_pass(repo_root, source_files, clang_args, macro_flags):
        """Helper method to build function registry in a single pass with given clang args."""
        index = cindex.Index.create()
        registry = {}  # base name -> set of (file, start_line, end_line, start_byte, end_byte)

        # Resolve repo_root to handle symlinks consistently with file discovery
        resolved_repo_root = Path(repo_root).resolve()

        for source_file in source_files:
            try:
                # Try to get relative path using resolved repo root
                rel = str(Path(source_file).relative_to(resolved_repo_root))
            except ValueError:
                # If that fails, try with the original repo_root
                try:
                    rel = str(Path(source_file).relative_to(repo_root))
                except ValueError:
                    # Final fallback: use just the filename
                    rel = str(Path(source_file).name)
            logging.debug(f"Scanning {source_file}")
            try:
                # Use file-specific flags instead of generic default flags
                base_flags = get_clang_flags_for_file(source_file) or []
                additional_args = clang_args or []
                additional_macros = macro_flags or []
                file_flags = base_flags + additional_args + additional_macros
                tu = index.parse(str(source_file), args=file_flags)
            except Exception as e:
                logging.error(f"⚠️ parse {rel}: {e}")
                continue

            def collect(root_cursor):
                """Iteratively collect function definitions using a stack to avoid deep recursion."""
                # Use a stack for iterative traversal instead of recursion
                # This prevents stack overflow on deeply nested AST structures (e.g., complex C++ templates)
                stack = [root_cursor]
                
                while stack:
                    cursor = stack.pop()
                    
                    try:
                        # Check cursor.kind safely - handle unknown cursor kinds from version mismatches
                        # between libclang library and Python bindings
                        try:
                            cursor_kind = cursor.kind
                        except ValueError as kind_error:
                            # Unknown cursor kind - this happens when libclang version differs from Python bindings
                            # Log at debug level since these are typically attribute cursors (OBJC_BOXABLE, FLAG_ENUM, etc.)
                            # that don't affect function/type extraction
                            error_str = str(kind_error)
                            if "Unknown template argument kind" in error_str:
                                # Extract the kind number for debugging
                                logging.debug(f"Skipping cursor with unknown kind (likely attribute cursor): {error_str}")
                            else:
                                logging.debug(f"Skipping cursor due to unknown kind: {error_str}")
                            # Still try to process children even if this cursor's kind is unknown
                            try:
                                children = list(cursor.get_children())
                                for child in reversed(children):
                                    stack.append(child)
                            except Exception:
                                pass
                            continue
                        
                        if cursor_kind in ALLOWED_FUNCTION_KINDS and cursor.is_definition():
                            name = CASTUtil.format_function_name(cursor)
                            if name:
                                start, end = cursor.extent.start, cursor.extent.end
                                start_line, end_line = getattr(start, 'line', None), getattr(end, 'line', None)
                                start_byte, end_byte = getattr(start, 'offset', None), getattr(end, 'offset', None)
                                file_path = start.file.name if start.file else rel
                                
                                # Skip cursors from system/SDK headers - they should not be registered
                                # as belonging to project files (fixes transitive system framework pollution)
                                if not _is_file_in_repo(file_path, repo_root, resolved_repo_root):
                                    logging.debug(f"Skipping system header cursor: {name} from {file_path}")
                                    # Still process children
                                    try:
                                        children = list(cursor.get_children())
                                        for child in reversed(children):
                                            stack.append(child)
                                    except Exception:
                                        pass
                                    continue
                                
                                try:
                                    # Try to get relative path using resolved repo root first
                                    file_rel = str(Path(file_path).relative_to(resolved_repo_root))
                                except ValueError:
                                    # If that fails, try with original repo_root
                                    try:
                                        file_rel = str(Path(file_path).relative_to(repo_root))
                                    except ValueError:
                                        # This should not happen after _is_file_in_repo check, but log if it does
                                        logging.warning(f"Unexpected: file {file_path} passed repo check but failed relative_to")
                                        # Still process children
                                        try:
                                            children = list(cursor.get_children())
                                            for child in reversed(children):
                                                stack.append(child)
                                        except Exception:
                                            pass
                                        continue
                                registry.setdefault(name, set()).add(
                                    (file_rel, start_line, end_line, start_byte, end_byte)
                                )
                    except ValueError as e:
                        # Handle other ValueError exceptions (not cursor kind related)
                        logging.debug(f"Skipping cursor due to ValueError: {e}")
                    except Exception as e:
                        # Handle any other unexpected errors when accessing cursor properties
                        logging.debug(f"Skipping cursor due to unexpected error: {e}")

                    # Always try to add children to stack, even if current cursor processing failed
                    try:
                        children = list(cursor.get_children())
                        # Add in reverse order so we process in the same order as recursive version
                        for child in reversed(children):
                            stack.append(child)
                    except Exception as e:
                        logging.debug(f"Error getting cursor children: {e}")
            try:
                collect(tu.cursor)
            except Exception as e:
                logging.error(f"⚠️ Error processing file {rel}: {e}")
                logging.error(f"Continuing with next file...")
                continue

        return registry

    @staticmethod
    def _merge_function_registries(registry1, registry2):
        """Merge two function registries, combining entries from both."""
        merged = {}

        # Add all entries from registry1
        for fn_name, entries in registry1.items():
            merged[fn_name] = set(entries)

        # Add entries from registry2, merging with existing entries
        for fn_name, entries in registry2.items():
            if fn_name in merged:
                # Merge entries (sets automatically handle duplicates)
                merged[fn_name].update(entries)
            else:
                # New function found in registry2
                merged[fn_name] = set(entries)

        return merged

    @staticmethod
    def build_data_types_registry(repo_root, source_files, clang_args, out_path,
                                   macros: List[str] = None,
                                   expand_macros: bool = True,
                                   use_parallel: Optional[bool] = None,
                                   max_workers: Optional[int] = None):
        """
        Collect all data type definitions (classes, structs, enums) with file paths and line numbers.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            out_path: Output path for registry JSON (can be None to skip writing)
            macros: Optional list of macros to expand. If provided (even empty list triggers auto-detect),
                    macro expansion is enabled. If None, no macro expansion.
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
            use_parallel: If True, use parallel processing. If None, auto-detect based on file count.
            max_workers: Maximum number of worker processes. If None, use default.
        
        Returns:
            Registry dict mapping data type names to file entries
        """
        # Determine if parallel processing should be used
        should_parallel, num_workers = CASTUtil._should_use_parallel(
            source_files, use_parallel, max_workers
        )
        
        # Determine macro flags for parallel processing
        if expand_macros:
            if macros is not None:
                if macros:
                    macro_flags = create_macro_flags(set(macros))
                else:
                    detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                    macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
            else:
                macro_flags = []
        else:
            macro_flags = []
        
        if should_parallel:
            logging.info(f"[+] Building data types registry in parallel with {num_workers} workers...")
            if expand_macros:
                # Pass 1: Without macros
                registry1 = CASTUtil._build_data_types_registry_parallel(
                    repo_root, source_files, clang_args, [], num_workers
                )
                # Pass 2: With macros
                registry2 = CASTUtil._build_data_types_registry_parallel(
                    repo_root, source_files, clang_args, macro_flags, num_workers
                )
                registry = CASTUtil._merge_data_types_registries(registry1, registry2)
            else:
                registry = CASTUtil._build_data_types_registry_parallel(
                    repo_root, source_files, clang_args, macro_flags, num_workers
                )
        elif expand_macros:
            logging.info(f"[+] Building data types registry with expand macros mode (dual-pass)...")
            registry = CASTUtil._build_data_types_registry_expand_macros(
                repo_root, source_files, clang_args, macros
            )
        else:
            logging.info(f"[+] Building data types registry with single-pass approach...")
            # Single pass: Build registry without macros defined
            registry = CASTUtil._build_data_types_registry_single_pass(
                repo_root, source_files, clang_args, []
            )

        logging.info(f"[+] Data types registry completed: {len(registry)} data types found")

        # Convert to the new dictionary schema with "data_type_to_location" key
        json_output_list = []
        for data_type_name, file_entries in registry.items():
            if file_entries:  # Only add if we have file entries
                data_type_entry = {
                    "data_type_name": data_type_name,
                    "files": sorted(file_entries, key=lambda x: (x["file_name"], x["start"]))
                }
                json_output_list.append(data_type_entry)

        # Wrap in the new dictionary schema
        json_output = {
            "data_type_to_location": json_output_list
        }

        # Only write to file if out_path is provided
        if out_path is not None:
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, indent=2, sort_keys=True)

            logging.info(f"[+] Wrote Data types registry with {len(json_output_list)} entries to {out_path}")
        else:
            logging.info(f"[+] Built Data types registry with {len(json_output_list)} entries (no output file specified)")

        return registry

    @staticmethod
    def _build_data_types_registry_single_pass(repo_root, source_files, clang_args, macro_flags):
        """Helper method to build data types registry in a single pass with given clang args."""
        index = cindex.Index.create()
        data_types_registry = {}  # data type name -> list of file entries with line numbers

        def get_field_name_for_anonymous_struct(struct_cursor):
            """Extract field name associated with anonymous struct by looking at the next sibling."""
            parent = struct_cursor.semantic_parent
            if parent and parent.kind in [cindex.CursorKind.STRUCT_DECL, cindex.CursorKind.CLASS_DECL]:
                # Get all children of the parent
                children = list(parent.get_children())
                struct_index = -1
                
                # Find the index of our struct cursor
                for i, child in enumerate(children):
                    if child == struct_cursor:
                        struct_index = i
                        break
                
                # Look for the next field declaration after the struct
                if struct_index >= 0 and struct_index + 1 < len(children):
                    next_child = children[struct_index + 1]
                    if next_child.kind == cindex.CursorKind.FIELD_DECL:
                        return next_child.spelling
                
                # Alternative: look for field declarations that might reference this struct
                for child in children:
                    if child.kind == cindex.CursorKind.FIELD_DECL:
                        # Check if this field comes after our struct in the source
                        if (hasattr(child, 'location') and hasattr(struct_cursor, 'location') and
                            child.location.line > struct_cursor.location.line):
                            return child.spelling
            return None

        def generate_nested_struct_name(cursor):
            """Generate qualified name for nested structs (both anonymous and named)."""
            if cursor.kind == cindex.CursorKind.STRUCT_DECL:
                parent = cursor.semantic_parent
                if parent and parent.kind in [cindex.CursorKind.STRUCT_DECL, cindex.CursorKind.CLASS_DECL]:
                    parent_name = CASTUtil.get_fully_qualified_name(parent) or parent.spelling
                    if parent_name:
                        # For named structs inside classes, use the struct name
                        if cursor.spelling:
                            return f"{parent_name}::{cursor.spelling}"
                        # For anonymous structs, try to find the field name
                        else:
                            field_name = get_field_name_for_anonymous_struct(cursor)
                            if field_name:
                                return f"{parent_name}.{field_name}"
                elif parent and parent.kind == cindex.CursorKind.VAR_DECL:
                    # Static anonymous struct - could be inside a function or at file scope
                    var_name = parent.spelling
                    if var_name:
                        # Check if the variable is inside a function
                        function_parent = parent.semantic_parent
                        while function_parent:
                            if function_parent.kind in ALLOWED_FUNCTION_KINDS:
                                # This is a static struct inside a function
                                function_name = CASTUtil.get_fully_qualified_name(function_parent) or function_parent.spelling
                                if function_name:
                                    return f"{function_name}::{var_name}"
                                break
                            elif function_parent.kind == cindex.CursorKind.TRANSLATION_UNIT:
                                # This is a file-scope static variable
                                break
                            function_parent = function_parent.semantic_parent
                        # Fallback to just the variable name
                        return var_name
                else:
                    # Check if this anonymous struct is inside a function (not via VAR_DECL)
                    # This handles cases like static constexpr struct { ... } array[] = { ... };
                    current_parent = parent
                    while current_parent:
                        if current_parent.kind in ALLOWED_FUNCTION_KINDS:
                            # This is an anonymous struct inside a function
                            function_name = CASTUtil.get_fully_qualified_name(current_parent) or current_parent.spelling
                            if function_name:
                                # Look for a variable declaration that uses this struct
                                # by checking siblings or nearby nodes
                                var_name = find_associated_variable_name(cursor)
                                if var_name:
                                    return f"{function_name}::{var_name}"
                            break
                        elif current_parent.kind == cindex.CursorKind.TRANSLATION_UNIT:
                            break
                        current_parent = current_parent.semantic_parent
            return None

        def find_associated_variable_name(struct_cursor):
            """Find the variable name associated with an anonymous struct."""
            # Look at the parent's children to find a variable declaration that might use this struct
            parent = struct_cursor.semantic_parent
            if parent:
                try:
                    children = list(parent.get_children())
                    struct_index = -1
                    
                    # Find the index of our struct cursor
                    for i, child in enumerate(children):
                        if (child.kind == cindex.CursorKind.STRUCT_DECL and
                            child.location.line == struct_cursor.location.line and
                            child.location.column == struct_cursor.location.column):
                            struct_index = i
                            break
                    
                    # Look for a variable declaration after the struct
                    if struct_index >= 0:
                        for i in range(struct_index + 1, len(children)):
                            child = children[i]
                            if child.kind == cindex.CursorKind.VAR_DECL and child.spelling:
                                return child.spelling
                except Exception:
                    pass
            return None

        def extract_anonymous_struct_from_var_decl(cursor):
            """Extract anonymous struct type from static variable declarations."""
            if cursor.kind == cindex.CursorKind.VAR_DECL and hasattr(cursor, 'type') and cursor.type:
                # Check if this is a static variable with storage class
                is_static = False
                try:
                    # Check for static storage class
                    if hasattr(cursor, 'storage_class'):
                        is_static = cursor.storage_class == cindex.StorageClass.STATIC
                    # Also check if the variable is at file scope (global)
                    elif cursor.semantic_parent and cursor.semantic_parent.kind == cindex.CursorKind.TRANSLATION_UNIT:
                        is_static = True
                except:
                    # Fallback: assume file-scope variables are static
                    if cursor.semantic_parent and cursor.semantic_parent.kind == cindex.CursorKind.TRANSLATION_UNIT:
                        is_static = True
                
                if is_static:
                    var_type = cursor.type
                    if hasattr(var_type, 'kind') and var_type.kind == cindex.TypeKind.RECORD:
                        # Get the struct declaration from the type
                        struct_decl = var_type.get_declaration()
                        if (struct_decl and
                            struct_decl.kind == cindex.CursorKind.STRUCT_DECL and
                            not struct_decl.spelling):  # Anonymous struct
                            
                            # Use variable name as the struct name
                            struct_name = cursor.spelling
                            if struct_name:
                                start, end = struct_decl.extent.start, struct_decl.extent.end
                                start_line, end_line = getattr(start, 'line', None), getattr(end, 'line', None)
                                file_path = start.file.name if start.file else None
                                
                                if file_path and start_line and end_line:
                                    try:
                                        file_rel = str(Path(file_path).relative_to(repo_root))
                                    except ValueError:
                                        try:
                                            file_rel = str(Path(file_path).resolve().relative_to(repo_root.resolve()))
                                        except Exception:
                                            file_rel = str(Path(file_path).name)
                                    
                                    file_entry = {
                                        "file_name": file_rel,
                                        "start": start_line,
                                        "end": end_line
                                    }
                                    
                                    if struct_name not in data_types_registry:
                                        data_types_registry[struct_name] = []
                                    
                                    if file_entry not in data_types_registry[struct_name]:
                                        data_types_registry[struct_name].append(file_entry)

        for source_file in source_files:
            # Resolve repo_root to handle symlinks consistently with file discovery
            resolved_repo_root = Path(repo_root).resolve()
            
            try:
                # Try to get relative path using resolved repo root
                rel = str(Path(source_file).relative_to(resolved_repo_root))
            except ValueError:
                # If that fails, try with the original repo_root
                try:
                    rel = str(Path(source_file).relative_to(repo_root))
                except ValueError:
                    # Final fallback: use just the filename
                    rel = str(Path(source_file).name)
            
            try:
                # Use file-specific flags instead of generic default flags
                base_flags = get_clang_flags_for_file(source_file) or []
                additional_args = clang_args or []
                additional_macros = macro_flags or []
                file_flags = base_flags + additional_args + additional_macros
                tu = index.parse(str(source_file), args=file_flags)
            except Exception as e:
                logging.error(f"⚠️ parse {rel}: {e}")
                continue

            def collect_data_types(root_cursor):
                """Iteratively collect data type definitions using a stack to avoid deep recursion."""
                # Use a stack for iterative traversal instead of recursion
                # This prevents stack overflow on deeply nested AST structures (e.g., complex C++ templates)
                stack = [root_cursor]
                
                while stack:
                    cursor = stack.pop()
                    
                    try:
                        # Check cursor.kind safely - handle unknown cursor kinds from version mismatches
                        try:
                            cursor_kind = cursor.kind
                        except ValueError as kind_error:
                            # Unknown cursor kind - log at debug level and continue with children
                            logging.debug(f"Skipping cursor with unknown kind: {kind_error}")
                            try:
                                children = list(cursor.get_children())
                                for child in reversed(children):
                                    stack.append(child)
                            except Exception:
                                pass
                            continue
                        
                        # Handle variable declarations that might contain anonymous structs
                        if cursor_kind == cindex.CursorKind.VAR_DECL:
                            extract_anonymous_struct_from_var_decl(cursor)
                        
                        # Handle typedef declarations that might define structs inside classes
                        if cursor_kind == cindex.CursorKind.TYPEDEF_DECL:
                            # Check if this typedef is inside a class and defines a struct
                            parent = cursor.semantic_parent
                            if parent and parent.kind in [cindex.CursorKind.CLASS_DECL, cindex.CursorKind.STRUCT_DECL]:
                                # Get the underlying type
                                underlying_type = cursor.underlying_typedef_type
                                if underlying_type and hasattr(underlying_type, 'get_declaration'):
                                    type_decl = underlying_type.get_declaration()
                                    if type_decl and type_decl.kind == cindex.CursorKind.STRUCT_DECL:
                                        # This is a typedef struct inside a class
                                        parent_name = CASTUtil.get_fully_qualified_name(parent) or parent.spelling
                                        typedef_name = cursor.spelling
                                        if parent_name and typedef_name:
                                            qualified_name = f"{parent_name}::{typedef_name}"
                                            
                                            start, end = cursor.extent.start, cursor.extent.end
                                            start_line, end_line = getattr(start, 'line', None), getattr(end, 'line', None)
                                            file_path = start.file.name if start.file else rel
                                            
                                            # Skip cursors from system/SDK headers - they should not be registered
                                            # as belonging to project files (fixes transitive system framework pollution)
                                            if not _is_file_in_repo(file_path, repo_root, resolved_repo_root):
                                                logging.debug(f"Skipping system header typedef cursor: {qualified_name} from {file_path}")
                                                # Continue to next cursor - don't add children here, they'll be added below
                                            elif start_line is not None and end_line is not None:
                                                try:
                                                    file_rel = str(Path(file_path).relative_to(repo_root))
                                                except ValueError:
                                                    try:
                                                        file_rel = str(Path(file_path).resolve().relative_to(repo_root.resolve()))
                                                    except Exception:
                                                        # This should not happen after _is_file_in_repo check, but log if it does
                                                        logging.warning(f"Unexpected: file {file_path} passed repo check but failed relative_to")
                                                        file_rel = None
                                                
                                                if file_rel is not None:
                                                    file_entry = {
                                                        "file_name": file_rel,
                                                        "start": start_line,
                                                        "end": end_line
                                                    }

                                                    if qualified_name not in data_types_registry:
                                                        data_types_registry[qualified_name] = []

                                                    if file_entry not in data_types_registry[qualified_name]:
                                                        data_types_registry[qualified_name].append(file_entry)
                                                        logging.debug(f"Added typedef struct inside class: {qualified_name}")
                        
                        if cursor_kind in ALLOWED_DATA_TYPE_KINDS:
                            # For Objective-C interfaces and protocols, we accept declarations (not just definitions)
                            # because @interface in .h files is the canonical class declaration,
                            # while @implementation in .m files is the definition
                            is_objc_interface = cursor_kind == getattr(cindex.CursorKind, "OBJC_INTERFACE_DECL", None)
                            is_objc_protocol = cursor_kind == getattr(cindex.CursorKind, "OBJC_PROTOCOL_DECL", None)
                            
                            # Only include actual definitions, not forward declarations
                            # Exception: Objective-C interfaces and protocols are accepted even as declarations
                            if not cursor.is_definition() and not is_objc_interface and not is_objc_protocol:
                                # Skip forward declarations
                                pass
                            else:
                                qualified_name = None
                                
                                # First check if this is a struct (anonymous or named) and try to generate a better name
                                if cursor.kind == cindex.CursorKind.STRUCT_DECL:
                                    nested_name = generate_nested_struct_name(cursor)
                                    if nested_name:
                                        qualified_name = nested_name
                                        logging.debug(f"Generated nested struct name: {qualified_name}")
                                
                                # If we don't have a name yet, use the standard method
                                if not qualified_name:
                                    qualified_name = CASTUtil.get_fully_qualified_name(cursor)
                                    if not qualified_name:
                                        # Check if cursor.spelling is an unnamed type with old format
                                        spelling = cursor.spelling
                                        if spelling and ("(unnamed " in spelling) and " at " in spelling and spelling.endswith(")"):
                                            # Generate a proper name for unnamed types
                                            try:
                                                start = cursor.extent.start
                                                file_path = start.file.name if start.file else None
                                                qualified_name = generate_unnamed_type_name(cursor, file_path)
                                            except Exception:
                                                # Fallback to a simple unnamed name if generation fails
                                                qualified_name = "unnamed_unknown_unknown"
                                        else:
                                            qualified_name = spelling

                                # Handle remaining anonymous structs that still have "unnamed" in the name
                                if qualified_name and ("unnamed class at" in qualified_name or "unnamed struct at" in qualified_name or "unnamed enum at" in qualified_name):
                                    # Try to generate a nested struct name
                                    nested_name = generate_nested_struct_name(cursor)
                                    if nested_name:
                                        qualified_name = nested_name
                                        logging.debug(f"Replaced unnamed struct with: {qualified_name}")
                                    else:
                                        # For anonymous structs inside functions, generate a descriptive name
                                        if cursor.kind == cindex.CursorKind.STRUCT_DECL and not cursor.spelling:
                                            # Check if this struct is inside a function
                                            current_parent = cursor.semantic_parent
                                            while current_parent:
                                                if current_parent.kind in ALLOWED_FUNCTION_KINDS:
                                                    # This is an anonymous struct inside a function
                                                    function_name = CASTUtil.get_fully_qualified_name(current_parent) or current_parent.spelling
                                                    if function_name:
                                                        # Generate a descriptive name based on file and line
                                                        start = cursor.extent.start
                                                        file_path = start.file.name if start.file else rel
                                                        line_number = getattr(start, 'line', 0)
                                                        
                                                        # Extract just the filename without path and extension
                                                        file_name = Path(file_path).stem if file_path else "unknown"
                                                        
                                                        qualified_name = f"{function_name}::anonymous_static_constexpr_struct_{file_name}_line_{line_number}"
                                                        logging.debug(f"Generated function-scoped struct name: {qualified_name}")
                                                        break
                                                    break
                                                elif current_parent.kind == cindex.CursorKind.TRANSLATION_UNIT:
                                                    break
                                                current_parent = current_parent.semantic_parent
                                        
                                        # Handle remaining unnamed types by generating a proper name
                                        if not qualified_name or ("unnamed" in qualified_name and not qualified_name.startswith("anonymous_")):
                                            # Generate a proper name for unnamed types
                                            start = cursor.extent.start
                                            file_path = start.file.name if start.file else rel
                                            qualified_name = generate_unnamed_type_name(cursor, file_path)
                                            logging.debug(f"Generated name for unnamed type: {qualified_name}")

                                if qualified_name:
                                    start, end = cursor.extent.start, cursor.extent.end
                                    start_line, end_line = getattr(start, 'line', None), getattr(end, 'line', None)
                                    file_path = start.file.name if start.file else rel
                                    
                                    # Skip cursors from system/SDK headers - they should not be registered
                                    # as belonging to project files (fixes transitive system framework pollution)
                                    if not _is_file_in_repo(file_path, repo_root, resolved_repo_root):
                                        logging.debug(f"Skipping system header data type cursor: {qualified_name} from {file_path}")
                                        # Continue to next cursor - children will be added below
                                    elif start_line is not None and end_line is not None:
                                        try:
                                            # Try to get relative path without resolving symbolic links first
                                            file_rel = str(Path(file_path).relative_to(repo_root))
                                        except ValueError:
                                            # If that fails, try with resolved paths
                                            try:
                                                file_rel = str(Path(file_path).resolve().relative_to(repo_root.resolve()))
                                            except Exception:
                                                # This should not happen after _is_file_in_repo check, but log if it does
                                                logging.warning(f"Unexpected: file {file_path} passed repo check but failed relative_to")
                                                file_rel = None
                                        
                                        # Only add if we have a valid relative path
                                        if file_rel is not None:
                                            file_entry = {
                                                "file_name": file_rel,
                                                "start": start_line,
                                                "end": end_line
                                            }

                                            if qualified_name not in data_types_registry:
                                                data_types_registry[qualified_name] = []

                                            # Avoid duplicates
                                            if file_entry not in data_types_registry[qualified_name]:
                                                data_types_registry[qualified_name].append(file_entry)
                    except ValueError as e:
                        # Handle other ValueError exceptions (not cursor kind related)
                        logging.debug(f"Skipping cursor due to ValueError: {e}")
                    except Exception as e:
                        # Handle any other unexpected errors when accessing cursor properties
                        logging.debug(f"Skipping cursor due to unexpected error: {e}")

                    # Always try to add children to stack, even if current cursor processing failed
                    try:
                        children = list(cursor.get_children())
                        # Add in reverse order so we process in the same order as recursive version
                        for child in reversed(children):
                            stack.append(child)
                    except Exception as e:
                        logging.debug(f"Error getting cursor children in collect_data_types: {e}")

            try:
                collect_data_types(tu.cursor)
            except Exception as e:
                logging.error(f"⚠️ Error processing data types in file {rel}: {e}")
                logging.error(f"Continuing with next file...")
                continue

        return data_types_registry

    @staticmethod
    def _build_data_types_registry_expand_macros(repo_root, source_files, clang_args, macros: List[str] = None):
        """
        Build data types registry with dual-pass macro handling (expand macros mode).
        
        This method builds the AST twice:
        1. Pass 1: Without macro expansion - captures code in #else branches
        2. Pass 2: With macro expansion - captures code in #if branches
        
        The results are merged to capture all code paths regardless of macro state.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macros: Optional list of macros to expand. If empty list, auto-detect macros.
                    If None, skip pass 2.
        
        Returns:
            Merged registry dict
        """
        logging.info("=" * 60)
        logging.info("EXPAND MACROS MODE: Building data types registry twice (with and without macros)")
        logging.info("=" * 60)
        
        # PASS 1: Without macro expansion
        logging.info("PASS 1: Building data types registry WITHOUT macro expansion...")
        registry1 = CASTUtil._build_data_types_registry_single_pass(
            repo_root, source_files, clang_args, []
        )
        logging.info(f"Pass 1 found: {len(registry1)} data types")
        
        # PASS 2: With macro expansion
        logging.info("PASS 2: Building data types registry WITH macro expansion...")
        if macros is not None:
            if macros:
                # Use provided macros
                macro_flags = create_macro_flags(set(macros))
                logging.info(f"[+] Using {len(macros)} provided macros for expansion")
            else:
                # Auto-detect macros, excluding derived ones
                detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
                logging.info(f"[+] Auto-detected {len(detected_macros)} macros for expansion ({len(derived_macros)} derived, excluded)")
        else:
            # No macros specified, use empty list for pass 2
            macro_flags = []
            logging.info("[+] No macros specified, pass 2 will use empty macro flags")
        
        registry2 = CASTUtil._build_data_types_registry_single_pass(
            repo_root, source_files, clang_args, macro_flags
        )
        logging.info(f"Pass 2 found: {len(registry2)} data types")
        
        # MERGE: Combine results from both passes
        logging.info("MERGING: Combining results from both passes...")
        merged_registry = CASTUtil._merge_data_types_registries(registry1, registry2)
        logging.info(f"Merged total: {len(merged_registry)} data types")
        
        return merged_registry

    @staticmethod
    def _merge_data_types_registries(registry1, registry2):
        """Merge two data types registries, combining entries from both."""
        merged = {}

        # Add all entries from registry1
        for type_name, file_entries in registry1.items():
            merged[type_name] = list(file_entries)

        # Add entries from registry2, merging with existing entries
        for type_name, file_entries in registry2.items():
            if type_name in merged:
                # Merge file entries, avoiding duplicates
                existing_entries = merged[type_name]
                for entry in file_entries:
                    if entry not in existing_entries:
                        existing_entries.append(entry)
            else:
                # New data type found in registry2
                merged[type_name] = list(file_entries)

        return merged

    @staticmethod
    def build_constants_registry(repo_root, source_files, clang_args, out_path,
                                  macros: List[str] = None,
                                  expand_macros: bool = True):
        """
        Collect all constant definitions (const variables, enum constants, macros) with file paths and line numbers.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            out_path: Output path for registry JSON (can be None to skip writing)
            macros: Optional list of macros to expand. If provided (even empty list triggers auto-detect),
                    macro expansion is enabled. If None, no macro expansion.
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
        
        Returns:
            Registry dict mapping constant names to file entries
        """
        if expand_macros:
            logging.info(f"[+] Building constants registry with expand macros mode (dual-pass)...")
            registry = CASTUtil._build_constants_registry_expand_macros(
                repo_root, source_files, clang_args, macros
            )
        else:
            logging.info(f"[+] Building constants registry with single-pass approach...")
            # Single pass: Build registry without macros defined
            registry = CASTUtil._build_constants_registry_single_pass(
                repo_root, source_files, clang_args, []
            )

        logging.info(f"[+] Constants registry completed: {len(registry)} constants found")

        # Convert to the new dictionary schema with "constants_to_location" key
        json_output_list = []
        for constant_name, file_entries in registry.items():
            if file_entries:  # Only add if we have file entries
                constant_entry = {
                    "constant_name": constant_name,
                    "files": sorted(file_entries, key=lambda x: (x["file_name"], x["start"]))
                }
                json_output_list.append(constant_entry)

        # Wrap in the new dictionary schema
        json_output = {
            "constants_to_location": json_output_list
        }

        # Only write to file if out_path is provided
        if out_path is not None:
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, indent=2, sort_keys=True)

            logging.info(f"[+] Wrote Constants registry with {len(json_output_list)} entries to {out_path}")
        else:
            logging.info(f"[+] Built Constants registry with {len(json_output_list)} entries (no output file specified)")

        return registry

    @staticmethod
    def _build_constants_registry_single_pass(repo_root, source_files, clang_args, macro_flags):
        """Helper method to build constants registry in a single pass with given clang args."""
        index = cindex.Index.create()
        constants_registry = {}  # constant name -> list of file entries with line numbers and values

        def extract_literal_value_from_cursor(cursor):
            """Extract literal value from different cursor types."""
            try:
                if cursor.kind == cindex.CursorKind.INTEGER_LITERAL:
                    # Get the token spelling for integer literals
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        try:
                            # Handle different integer formats (decimal, hex, octal, binary)
                            if token_spelling.startswith('0x') or token_spelling.startswith('0X'):
                                return int(token_spelling, 16)
                            elif token_spelling.startswith('0b') or token_spelling.startswith('0B'):
                                return int(token_spelling, 2)
                            elif token_spelling.startswith('0') and len(token_spelling) > 1 and token_spelling.isdigit():
                                return int(token_spelling, 8)
                            else:
                                # Try integer parsing first
                                return int(token_spelling)
                        except ValueError:
                            # If integer parsing fails, try float parsing for scientific notation
                            try:
                                float_val = float(token_spelling)
                                # If it's a whole number, return as int
                                if float_val.is_integer():
                                    return int(float_val)
                                else:
                                    return float_val
                            except ValueError:
                                return token_spelling
                elif cursor.kind == cindex.CursorKind.FLOATING_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        try:
                            # Handle scientific notation like 1.0e6, 10e6, 1E-3, etc.
                            return float(token_spelling)
                        except ValueError:
                            # If float parsing fails, try to handle special cases
                            # Remove common suffixes like 'f', 'F', 'l', 'L'
                            clean_spelling = token_spelling.rstrip('fFlL')
                            try:
                                return float(clean_spelling)
                            except ValueError:
                                return token_spelling
                elif cursor.kind == cindex.CursorKind.STRING_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        return tokens[0].spelling
                elif cursor.kind == cindex.CursorKind.CHARACTER_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        return tokens[0].spelling
                elif cursor.kind == cindex.CursorKind.CXX_BOOL_LITERAL_EXPR:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        return token_spelling.lower() == 'true'
                elif cursor.kind == cindex.CursorKind.CXX_NULL_PTR_LITERAL_EXPR:
                    return None
                elif hasattr(cindex.CursorKind, 'OBJC_STRING_LITERAL') and cursor.kind == cindex.CursorKind.OBJC_STRING_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        return tokens[0].spelling
            except Exception:
                pass
            return None

        for source_file in source_files:
            # Resolve repo_root to handle symlinks consistently with file discovery
            resolved_repo_root = Path(repo_root).resolve()
            
            try:
                # Try to get relative path using resolved repo root
                rel = str(Path(source_file).relative_to(resolved_repo_root))
            except ValueError:
                # If that fails, try with the original repo_root
                try:
                    rel = str(Path(source_file).relative_to(repo_root))
                except ValueError:
                    # Final fallback: use just the filename
                    rel = str(Path(source_file).name)
            try:
                # Use file-specific flags instead of generic default flags
                base_flags = get_clang_flags_for_file(source_file) or []
                additional_args = clang_args or []
                additional_macros = macro_flags or []
                file_flags = base_flags + additional_args + additional_macros
                tu = index.parse(str(source_file), args=file_flags)
            except Exception as e:
                logging.error(f"⚠️ parse {rel}: {e}")
                continue

            def collect_constants(root_cursor):
                """Iteratively collect constant definitions using a stack to avoid deep recursion."""
                # Use a stack for iterative traversal instead of recursion
                # This prevents stack overflow on deeply nested AST structures (e.g., complex C++ templates)
                stack = [root_cursor]
                
                while stack:
                    cursor = stack.pop()
                    
                    try:
                        # Check cursor.kind safely - handle unknown cursor kinds from version mismatches
                        try:
                            cursor_kind = cursor.kind
                        except ValueError as kind_error:
                            # Unknown cursor kind - log at debug level and continue with children
                            logging.debug(f"Skipping cursor with unknown kind: {kind_error}")
                            try:
                                children = list(cursor.get_children())
                                for child in reversed(children):
                                    stack.append(child)
                            except Exception:
                                pass
                            continue
                        
                        # Handle const variables
                        if cursor_kind == cindex.CursorKind.VAR_DECL:
                            if hasattr(cursor, 'type') and cursor.type:
                                type_spelling = getattr(cursor.type, 'spelling', '')
                                # Only include if it's explicitly const or static
                                if ('const' in type_spelling or 'static' in type_spelling):
                                    constant_name = cursor.spelling
                                    if constant_name:
                                        # Try to extract the constant value
                                        constant_value = None
                                        for child in cursor.get_children():
                                            value = extract_literal_value_from_cursor(child)
                                            if value is not None:
                                                constant_value = value
                                                break
                                        
                                        start, end = cursor.extent.start, cursor.extent.end
                                        start_line, end_line = getattr(start, 'line', None), getattr(end, 'line', None)
                                        file_path = start.file.name if start.file else rel
                                        
                                        # Skip cursors from system/SDK headers - they should not be registered
                                        # as belonging to project files (fixes transitive system framework pollution)
                                        if not _is_file_in_repo(file_path, repo_root, resolved_repo_root):
                                            logging.debug(f"Skipping system header VAR_DECL cursor: {constant_name} from {file_path}")
                                            # Continue to next cursor - children will be added below
                                        else:
                                            try:
                                                # Try to get relative path without resolving symbolic links first
                                                file_rel = str(Path(file_path).relative_to(repo_root))
                                            except ValueError:
                                                # If that fails, try with resolved paths
                                                try:
                                                    file_rel = str(Path(file_path).resolve().relative_to(repo_root.resolve()))
                                                except Exception:
                                                    # This should not happen after _is_file_in_repo check, but log if it does
                                                    logging.warning(f"Unexpected: file {file_path} passed repo check but failed relative_to")
                                                    file_rel = None

                                            # Only add if we have valid line numbers and file_rel
                                            if file_rel is not None and start_line is not None and end_line is not None:
                                                file_entry = {
                                                    "file_name": file_rel,
                                                    "start": start_line,
                                                    "end": end_line
                                                }
                                                
                                                # Add value if we found one
                                                if constant_value is not None:
                                                    file_entry["value"] = constant_value

                                                if constant_name not in constants_registry:
                                                    constants_registry[constant_name] = []

                                                # Avoid duplicates
                                                if file_entry not in constants_registry[constant_name]:
                                                    constants_registry[constant_name].append(file_entry)
                        
                        # Handle enum constants
                        elif cursor_kind == cindex.CursorKind.ENUM_CONSTANT_DECL:
                            constant_name = cursor.spelling
                            if constant_name:
                                # Get enum constant value
                                constant_value = None
                                try:
                                    enum_value = cursor.enum_value
                                    if isinstance(enum_value, (int, float)):
                                        constant_value = enum_value
                                except:
                                    pass
                                
                                start, end = cursor.extent.start, cursor.extent.end
                                start_line, end_line = getattr(start, 'line', None), getattr(end, 'line', None)
                                file_path = start.file.name if start.file else rel
                                
                                # Skip cursors from system/SDK headers - they should not be registered
                                # as belonging to project files (fixes transitive system framework pollution)
                                if not _is_file_in_repo(file_path, repo_root, resolved_repo_root):
                                    logging.debug(f"Skipping system header ENUM_CONSTANT_DECL cursor: {constant_name} from {file_path}")
                                    # Continue to next cursor - children will be added below
                                else:
                                    try:
                                        # Try to get relative path without resolving symbolic links first
                                        file_rel = str(Path(file_path).relative_to(repo_root))
                                    except ValueError:
                                        # If that fails, try with resolved paths
                                        try:
                                            file_rel = str(Path(file_path).resolve().relative_to(repo_root.resolve()))
                                        except Exception:
                                            # This should not happen after _is_file_in_repo check, but log if it does
                                            logging.warning(f"Unexpected: file {file_path} passed repo check but failed relative_to")
                                            file_rel = None

                                    # Only add if we have valid line numbers and file_rel
                                    if file_rel is not None and start_line is not None and end_line is not None:
                                        file_entry = {
                                            "file_name": file_rel,
                                            "start": start_line,
                                            "end": end_line
                                        }
                                        
                                        # Add value if we found one
                                        if constant_value is not None:
                                            file_entry["value"] = constant_value

                                        if constant_name not in constants_registry:
                                            constants_registry[constant_name] = []

                                        # Avoid duplicates
                                        if file_entry not in constants_registry[constant_name]:
                                            constants_registry[constant_name].append(file_entry)
                        
                        # Handle macro definitions
                        elif cursor_kind == cindex.CursorKind.MACRO_DEFINITION:
                            constant_name = cursor.spelling
                            if constant_name and not constant_name.startswith('_'):  # Skip internal macros
                                start, end = cursor.extent.start, cursor.extent.end
                                start_line, end_line = getattr(start, 'line', None), getattr(end, 'line', None)
                                file_path = start.file.name if start.file else rel
                                
                                # Skip cursors from system/SDK headers - they should not be registered
                                # as belonging to project files (fixes transitive system framework pollution)
                                if not _is_file_in_repo(file_path, repo_root, resolved_repo_root):
                                    logging.debug(f"Skipping system header MACRO_DEFINITION cursor: {constant_name} from {file_path}")
                                    # Continue to next cursor - children will be added below
                                else:
                                    try:
                                        # Try to get relative path without resolving symbolic links first
                                        file_rel = str(Path(file_path).relative_to(repo_root))
                                    except ValueError:
                                        # If that fails, try with resolved paths
                                        try:
                                            file_rel = str(Path(file_path).resolve().relative_to(repo_root.resolve()))
                                        except Exception:
                                            # This should not happen after _is_file_in_repo check, but log if it does
                                            logging.warning(f"Unexpected: file {file_path} passed repo check but failed relative_to")
                                            file_rel = None

                                    # Only add if we have valid line numbers and file_rel
                                    if file_rel is not None and start_line is not None and end_line is not None:
                                        file_entry = {
                                            "file_name": file_rel,
                                            "start": start_line,
                                            "end": end_line,
                                            "type": "macro"
                                        }

                                        if constant_name not in constants_registry:
                                            constants_registry[constant_name] = []

                                        # Avoid duplicates
                                        if file_entry not in constants_registry[constant_name]:
                                            constants_registry[constant_name].append(file_entry)

                    except ValueError as e:
                        # Handle other ValueError exceptions (not cursor kind related)
                        logging.debug(f"Skipping cursor due to ValueError: {e}")
                    except Exception as e:
                        # Handle any other unexpected errors when accessing cursor properties
                        logging.debug(f"Skipping cursor due to unexpected error: {e}")

                    # Always try to add children to stack, even if current cursor processing failed
                    try:
                        children = list(cursor.get_children())
                        # Add in reverse order so we process in the same order as recursive version
                        for child in reversed(children):
                            stack.append(child)
                    except Exception as e:
                        logging.debug(f"Error getting cursor children in collect_constants: {e}")

            try:
                collect_constants(tu.cursor)
            except Exception as e:
                logging.error(f"⚠️ Error processing constants in file {rel}: {e}")
                logging.error(f"Continuing with next file...")
                continue

        return constants_registry

    @staticmethod
    def _build_constants_registry_expand_macros(repo_root, source_files, clang_args, macros: List[str] = None):
        """
        Build constants registry with dual-pass macro handling (expand macros mode).
        
        This method builds the AST twice:
        1. Pass 1: Without macro expansion - captures code in #else branches
        2. Pass 2: With macro expansion - captures code in #if branches
        
        The results are merged to capture all code paths regardless of macro state.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macros: Optional list of macros to expand. If empty list, auto-detect macros.
                    If None, skip pass 2.
        
        Returns:
            Merged registry dict
        """
        logging.info("=" * 60)
        logging.info("EXPAND MACROS MODE: Building constants registry twice (with and without macros)")
        logging.info("=" * 60)
        
        # PASS 1: Without macro expansion
        logging.info("PASS 1: Building constants registry WITHOUT macro expansion...")
        registry1 = CASTUtil._build_constants_registry_single_pass(
            repo_root, source_files, clang_args, []
        )
        logging.info(f"Pass 1 found: {len(registry1)} constants")
        
        # PASS 2: With macro expansion
        logging.info("PASS 2: Building constants registry WITH macro expansion...")
        if macros is not None:
            if macros:
                # Use provided macros
                macro_flags = create_macro_flags(set(macros))
                logging.info(f"[+] Using {len(macros)} provided macros for expansion")
            else:
                # Auto-detect macros, excluding derived ones
                detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
                logging.info(f"[+] Auto-detected {len(detected_macros)} macros for expansion ({len(derived_macros)} derived, excluded)")
        else:
            # No macros specified, use empty list for pass 2
            macro_flags = []
            logging.info("[+] No macros specified, pass 2 will use empty macro flags")
        
        registry2 = CASTUtil._build_constants_registry_single_pass(
            repo_root, source_files, clang_args, macro_flags
        )
        logging.info(f"Pass 2 found: {len(registry2)} constants")
        
        # MERGE: Combine results from both passes
        logging.info("MERGING: Combining results from both passes...")
        merged_registry = CASTUtil._merge_constants_registries(registry1, registry2)
        logging.info(f"Merged total: {len(merged_registry)} constants")
        
        return merged_registry

    @staticmethod
    def _merge_constants_registries(registry1, registry2):
        """Merge two constants registries, combining entries from both."""
        merged = {}

        # Add all entries from registry1
        for constant_name, file_entries in registry1.items():
            merged[constant_name] = list(file_entries)

        # Add entries from registry2, merging with existing entries
        for constant_name, file_entries in registry2.items():
            if constant_name in merged:
                # Merge file entries, avoiding duplicates
                existing_entries = merged[constant_name]
                for entry in file_entries:
                    if entry not in existing_entries:
                        existing_entries.append(entry)
            else:
                # New constant found in registry2
                merged[constant_name] = list(file_entries)

        return merged

    # ------------------------ Call Graph ------------------------

    @staticmethod
    def _resolve_callable_expr(cursor):
        """Resolve a cursor to a callable function name if possible."""
        if cursor.kind == cindex.CursorKind.CALL_EXPR:
            # For call expressions, get the referenced function
            ref = cursor.referenced
            if ref and ref.kind in ALLOWED_FUNCTION_KINDS:
                return CASTUtil.format_function_name(ref)
        elif cursor.kind == cindex.CursorKind.DECL_REF_EXPR:
            # For declaration references, check if it's a function
            ref = cursor.referenced
            if ref and ref.kind in ALLOWED_FUNCTION_KINDS:
                return CASTUtil.format_function_name(ref)
        elif cursor.kind == cindex.CursorKind.MEMBER_REF_EXPR:
            # For member references (method calls)
            ref = cursor.referenced
            if ref and ref.kind in ALLOWED_FUNCTION_KINDS:
                return CASTUtil.format_function_name(ref)
        elif hasattr(cindex.CursorKind, 'OBJC_MESSAGE_EXPR') and cursor.kind == cindex.CursorKind.OBJC_MESSAGE_EXPR:
            # For Objective-C message expressions like [self methodName]
            ref = cursor.referenced
            if ref and ref.kind in ALLOWED_FUNCTION_KINDS:
                return CASTUtil.format_function_name(ref)
        return None

    @staticmethod
    def build_forward_call_graph(repo_root, source_files, clang_args,
                                 filter_external_calls=False, registry=None,
                                 macros: List[str] = None,
                                 expand_macros: bool = True,
                                 use_parallel: Optional[bool] = None,
                                 max_workers: Optional[int] = None):
        """
        Build caller → callee adjacency (forward call graph).
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            filter_external_calls: If True, filter callees to only repo-defined functions
            registry: Optional set of known function names to filter against
            macros: Optional list of macros to expand. If provided (even empty list triggers auto-detect),
                    macro expansion is enabled. If None, no macro expansion.
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
            use_parallel: If True, use parallel processing. If None, auto-detect based on file count.
            max_workers: Maximum number of worker processes. If None, use default.
        
        Returns:
            Dict mapping caller names to lists of callee names
        """
        # Determine if parallel processing should be used
        should_parallel, num_workers = CASTUtil._should_use_parallel(
            source_files, use_parallel, max_workers
        )
        
        # Determine macro flags for parallel processing
        if expand_macros:
            if macros is not None:
                if macros:
                    macro_flags = create_macro_flags(set(macros))
                else:
                    detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                    macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
            else:
                macro_flags = []
        else:
            macro_flags = []
        
        if should_parallel:
            logging.info(f"[+] Building call graph in parallel with {num_workers} workers...")
            if expand_macros:
                # Pass 1: Without macros
                cg1 = CASTUtil._build_forward_call_graph_parallel(
                    repo_root, source_files, clang_args, [],
                    filter_external_calls, registry, num_workers
                )
                # Pass 2: With macros
                cg2 = CASTUtil._build_forward_call_graph_parallel(
                    repo_root, source_files, clang_args, macro_flags,
                    filter_external_calls, registry, num_workers
                )
                forward_map = CASTUtil._merge_call_graphs(cg1, cg2)
            else:
                forward_map = CASTUtil._build_forward_call_graph_parallel(
                    repo_root, source_files, clang_args, macro_flags,
                    filter_external_calls, registry, num_workers
                )
        elif expand_macros:
            logging.info(f"[+] Building call graph with expand macros mode (dual-pass)...")
            forward_map = CASTUtil._build_forward_call_graph_expand_macros(
                repo_root, source_files, clang_args, macros,
                filter_external_calls, registry
            )
        else:
            logging.info(f"[+] Building call graph with single-pass approach...")
            # Single pass: Build call graph without macros defined
            forward_map = CASTUtil._build_forward_call_graph_single_pass(
                repo_root, source_files, clang_args, [],
                filter_external_calls, registry
            )

        total_callers = len(forward_map)
        total_relationships = sum(len(callees) for callees in forward_map.values())
        logging.info(f"[+] Call graph completed: {total_callers} callers, {total_relationships} total call relationships")

        return forward_map

    @staticmethod
    def _build_forward_call_graph_expand_macros(repo_root, source_files, clang_args, macros: List[str] = None,
                                              filter_external_calls=False, registry=None):
        """
        Build forward call graph with dual-pass macro handling (expand macros mode).
        
        This method builds the AST twice:
        1. Pass 1: Without macro expansion - captures code in #else branches
        2. Pass 2: With macro expansion - captures code in #if branches
        
        The results are merged to capture all code paths regardless of macro state.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macros: Optional list of macros to expand. If empty list, auto-detect macros.
                    If None, skip pass 2.
            filter_external_calls: If True, filter callees to only repo-defined functions
            registry: Optional set of known function names to filter against
        
        Returns:
            Merged call graph dict
        """
        logging.info("=" * 60)
        logging.info("EXPAND MACROS MODE: Building call graph twice (with and without macros)")
        logging.info("=" * 60)
        
        # PASS 1: Without macro expansion
        logging.info("PASS 1: Building call graph WITHOUT macro expansion...")
        graph1 = CASTUtil._build_forward_call_graph_single_pass(
            repo_root, source_files, clang_args, [],
            filter_external_calls, registry
        )
        logging.info(f"Pass 1 found: {len(graph1)} callers")
        
        # PASS 2: With macro expansion
        logging.info("PASS 2: Building call graph WITH macro expansion...")
        if macros is not None:
            if macros:
                # Use provided macros
                macro_flags = create_macro_flags(set(macros))
                logging.info(f"[+] Using {len(macros)} provided macros for expansion")
            else:
                # Auto-detect macros, excluding derived ones
                detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
                logging.info(f"[+] Auto-detected {len(detected_macros)} macros for expansion ({len(derived_macros)} derived, excluded)")
        else:
            # No macros specified, use empty list for pass 2
            macro_flags = []
            logging.info("[+] No macros specified, pass 2 will use empty macro flags")
        
        graph2 = CASTUtil._build_forward_call_graph_single_pass(
            repo_root, source_files, clang_args, macro_flags,
            filter_external_calls, registry
        )
        logging.info(f"Pass 2 found: {len(graph2)} callers")
        
        # MERGE: Combine results from both passes
        logging.info("MERGING: Combining results from both passes...")
        merged_graph = CASTUtil._merge_call_graphs(graph1, graph2)
        logging.info(f"Merged total: {len(merged_graph)} callers")
        
        return merged_graph

    @staticmethod
    def _build_forward_call_graph_single_pass(repo_root, source_files, clang_args, macro_flags,
                                            filter_external_calls=False, registry=None):
        """Helper method to build forward call graph in a single pass with given clang args."""
        index = cindex.Index.create()
        forward_map = {}
        defined_funcs = registry if filter_external_calls else None

        def base_name_only(name: str) -> str:
            idx = name.find('(')
            return name[:idx] if idx != -1 else name

        def keep_callee(name):
            if not defined_funcs:
                return True
            b = base_name_only(name)
            if b in defined_funcs:
                return True
            candidates = [d for d in defined_funcs if d.endswith(f"::{b}")]
            return len(candidates) == 1

        def add_edge(caller, callee):
            if keep_callee(callee):
                forward_map.setdefault(caller, set()).add(callee)

        def traverse_body(node, current_fn):
            """Iteratively traverse function body to find call expressions using a stack to avoid deep recursion."""
            # Use a stack for iterative traversal instead of recursion
            # This prevents stack overflow on deeply nested AST structures (e.g., complex C++ templates)
            stack = [node]
            
            while stack:
                current = stack.pop()
                
                target = CASTUtil._resolve_callable_expr(current)
                if target and keep_callee(target):
                    add_edge(current_fn, target)
                
                # Add children to stack for processing (in reverse order to maintain traversal order)
                try:
                    children = list(current.get_children())
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    continue

        for source_file in source_files:
            # Resolve repo_root to handle symlinks consistently with file discovery
            resolved_repo_root = Path(repo_root).resolve()
            
            try:
                # Try to get relative path using resolved repo root
                rel = str(Path(source_file).relative_to(resolved_repo_root))
            except ValueError:
                # If that fails, try with the original repo_root
                try:
                    rel = str(Path(source_file).relative_to(repo_root))
                except ValueError:
                    # Final fallback: use just the filename
                    rel = str(Path(source_file).name)
            try:
                # Use file-specific flags instead of generic default flags
                base_flags = get_clang_flags_for_file(source_file) or []
                additional_args = clang_args or []
                additional_macros = macro_flags or []
                file_flags = base_flags + additional_args + additional_macros
                tu = index.parse(str(source_file), args=file_flags)
            except Exception as e:
                logging.error(f"⚠️ parse {rel}: {e}")
                continue

            def collect(root_cursor):
                """Iteratively collect function definitions and build call graph using a stack to avoid deep recursion."""
                # Use a stack for iterative traversal instead of recursion
                # This prevents stack overflow on deeply nested AST structures (e.g., complex C++ templates)
                stack = [root_cursor]
                
                while stack:
                    cursor = stack.pop()
                    
                    try:
                        # Check cursor.kind safely - handle unknown cursor kinds from version mismatches
                        try:
                            cursor_kind = cursor.kind
                        except ValueError as kind_error:
                            # Unknown cursor kind - log at debug level and continue with children
                            logging.debug(f"Skipping cursor with unknown kind: {kind_error}")
                            try:
                                children = list(cursor.get_children())
                                for child in reversed(children):
                                    stack.append(child)
                            except Exception:
                                pass
                            continue
                        
                        if cursor_kind in ALLOWED_FUNCTION_KINDS and cursor.is_definition():
                            fn_name = CASTUtil.format_function_name(cursor)
                            if fn_name:
                                # Process children: traverse_body for call graph AND add to stack for further collection
                                try:
                                    children = list(cursor.get_children())
                                    for child in children:
                                        traverse_body(child, fn_name)
                                    # Add children to stack in reverse order for correct traversal order
                                    for child in reversed(children):
                                        stack.append(child)
                                except Exception as e:
                                    logging.debug(f"Error processing function children: {e}")
                            else:
                                # No function name, just add children to stack
                                try:
                                    children = list(cursor.get_children())
                                    for child in reversed(children):
                                        stack.append(child)
                                except Exception as e:
                                    logging.debug(f"Error getting cursor children: {e}")
                        else:
                            # Not a function definition, add children to stack
                            try:
                                children = list(cursor.get_children())
                                for child in reversed(children):
                                    stack.append(child)
                            except Exception as e:
                                logging.debug(f"Error getting cursor children: {e}")
                                
                    except ValueError as e:
                        # Handle other ValueError exceptions (not cursor kind related)
                        logging.debug(f"Skipping cursor due to ValueError: {e}")
                    except Exception as e:
                        # Handle any other unexpected errors when accessing cursor properties
                        logging.debug(f"Skipping cursor due to unexpected error: {e}")

            try:
                collect(tu.cursor)
            except Exception as e:
                logging.error(f"⚠️ Error processing call graph in file {rel}: {e}")
                logging.error(f"Continuing with next file...")
                continue

        # Deduplicate: prefer decorated names with params over plain base names
        result = {}
        for caller, callees in forward_map.items():
            pick = {}
            for callee in callees:
                b = base_function_name(callee)
                cur = pick.get(b)
                if cur is None or ('(' not in cur and '(' in callee):
                    pick[b] = callee
            result[caller] = sorted(pick.values())

        return result

    @staticmethod
    def _merge_call_graphs(graph1, graph2):
        """Merge two call graphs, combining entries from both."""
        merged = {}

        # Add all entries from graph1
        for caller, callees in graph1.items():
            merged[caller] = set(callees)

        # Add entries from graph2, merging with existing entries
        for caller, callees in graph2.items():
            if caller in merged:
                # Merge callees (sets automatically handle duplicates)
                merged[caller].update(callees)
            else:
                # New caller found in graph2
                merged[caller] = set(callees)

        # Convert back to sorted lists
        result = {}
        for caller, callees in merged.items():
            pick = {}
            for callee in callees:
                b = base_function_name(callee)
                cur = pick.get(b)
                if cur is None or ('(' not in cur and '(' in callee):
                    pick[b] = callee
            result[caller] = sorted(pick.values())

        total_relationships = sum(len(callees) for callees in result.values())
        logging.info(f"[+] Built merged call graph adjacency with {len(result)} callers and {total_relationships} total call relationships")
        return result

    # ------------------------ Data Type Usage ------------------------

    @staticmethod
    @lru_cache(maxsize=1)
    def _std_consts():
        return {
            "STD_PREFIXES": ("std::", "__gnu_cxx::", "::std::", "__NS", "objc_", "dispatch_", "__", "_", "NS", "CF", "CG", "CA", "UI", "GL"),
            "STD_TYPES": {"int", "long", "short", "char", "wchar_t", "bool", "float", "double", "size_t", "ptrdiff_t", "void", "id", "char16_t", "char32_t", "signed", "unsigned", "nullptr_t", "auto"},
            "RE_CHAR_ARR": re.compile(r'^(const\s+)?char\[\d+\]$'),
            "RE_PRIM_ARR": re.compile(r'^(const\s+)?[A-Za-z_]\w*\[\d+\]$'),
            "RE_VOID_FUNC": re.compile(r'^void\s*\(\s*\)$'),
            "RE_VOID_FUNC_PTR": re.compile(r'^void\s*\(\s*\*\s*\)\s*\(\s*\)$'),
            "RE_FUNC_PTR": re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*\s*\(\s*\*\s*\)\s*\(.*\)$'),
            "RE_FUNC": re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*\s*\(\s*\)$'),
            "SYSTEM_SNIPS": tuple(s.lower() for s in (
                "string", "vector", "map", "set", "list", "array", "shared_ptr", "unique_ptr",
                "function", "thread", "mutex", "atomic", "optional", "variant",
                "NSString", "NSArray", "NSDictionary", "NSObject", "NSNumber",
                "dispatch_", "os_", "xpc_", "IOKit", "CoreFoundation"
            )),
        }

    @staticmethod
    def _is_standard_library_type(type_name: str) -> bool:
        """Check if a type name belongs to standard library, system types, or primitive types."""
        if not type_name:
            return True

        # Get pre-computed constants
        consts = CASTUtil._std_consts()

        # Remove template parameters and qualifiers for checking
        clean_name = strip_template_params(type_name).strip()

        # Remove common qualifiers
        for qualifier in ['const', 'volatile', 'static', 'extern', 'inline', '*', '&', '&&']:
            clean_name = clean_name.replace(qualifier, '').strip()

        # Check if it's a basic type
        if clean_name in consts["STD_TYPES"]:
            return True

        # Check for standard library prefixes
        for prefix in consts["STD_PREFIXES"]:
            if clean_name.startswith(prefix):
                return True

        # Check for primitive array types like "const char[32]", "const char[58]"
        if consts["RE_CHAR_ARR"].match(clean_name):
            return True
        if consts["RE_PRIM_ARR"].match(clean_name):
            # This catches primitive array types
            base_type = re.sub(r'\[\d+\]$', '', clean_name).replace('const', '').strip()
            if base_type in consts["STD_TYPES"]:
                return True

        # Check for function pointer types like "void ()" and "void (*)()"
        if consts["RE_VOID_FUNC"].match(clean_name):
            return True
        if consts["RE_VOID_FUNC_PTR"].match(clean_name):
            return True
        if consts["RE_FUNC_PTR"].match(clean_name):
            return True
        if consts["RE_FUNC"].match(clean_name):
            # Check if the return type is primitive
            return_type = re.sub(r'\s*\(\s*\)$', '', clean_name).strip()
            if return_type in consts["STD_TYPES"]:
                return True

        # Check for common system/framework types (case-insensitive for some)
        clean_lower = clean_name.lower()
        for pattern in consts["SYSTEM_SNIPS"]:
            if pattern in clean_lower:
                return True

        return False

    @staticmethod
    def _extract_type_from_cursor(cursor):
        """Extract type information from a cursor, handling various cursor types."""
        type_names = set()

        # Handle different cursor kinds that can have types
        if hasattr(cursor, 'type') and cursor.type:
            type_obj = cursor.type
            if hasattr(type_obj, 'get_canonical'):
                type_obj = type_obj.get_canonical()

            type_spelling = getattr(type_obj, 'spelling', '')
            if type_spelling and not CASTUtil._is_standard_library_type(type_spelling):
                # Check if this is an unnamed type with the old format
                if ("(unnamed " in type_spelling) and " at " in type_spelling and type_spelling.endswith(")"):
                    # Generate a proper name for unnamed types
                    try:
                        # Try to get the cursor from the type declaration
                        type_decl = type_obj.get_declaration()
                        if type_decl:
                            start = type_decl.extent.start
                            file_path = start.file.name if start.file else None
                            clean_type = generate_unnamed_type_name(type_decl, file_path)
                        else:
                            # Fallback to a simple unnamed name if we can't get the declaration
                            clean_type = "unnamed_unknown_unknown"
                    except Exception:
                        # Fallback to a simple unnamed name if generation fails
                        clean_type = "unnamed_unknown_unknown"
                else:
                    # Clean up the type name but preserve namespace qualification
                    clean_type = strip_template_params(type_spelling).strip()
                
                if clean_type:
                    type_names.add(clean_type)

        # Handle type references
        if cursor.kind == cindex.CursorKind.TYPE_REF:
            ref = cursor.referenced
            if ref and ref.kind in ALLOWED_DATA_TYPE_KINDS:
                qualified_name = CASTUtil.get_fully_qualified_name(ref)
                if qualified_name and not CASTUtil._is_standard_library_type(qualified_name):
                    # Keep the fully qualified name
                    type_names.add(qualified_name)

        # Handle declaration references that might be to custom types
        elif cursor.kind == cindex.CursorKind.DECL_REF_EXPR:
            ref = cursor.referenced
            if ref and ref.kind in ALLOWED_DATA_TYPE_KINDS:
                qualified_name = CASTUtil.get_fully_qualified_name(ref)
                if qualified_name and not CASTUtil._is_standard_library_type(qualified_name):
                    # Keep the fully qualified name
                    type_names.add(qualified_name)

        return type_names

    @staticmethod
    def build_data_type_use(repo_root, source_files, clang_args, custom_types_registry=None,
                            macros: List[str] = None,
                            expand_macros: bool = True):
        """
        Build function → custom data types mapping.

        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Clang compilation arguments
            custom_types_registry: Optional set of known custom type names to filter against
            macros: Optional list of macros to expand. If provided (even empty list triggers auto-detect),
                    macro expansion is enabled. If None, no macro expansion.
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)

        Returns:
            Dict mapping function names to sets of custom data types they use
        """
        return CASTUtil.build_data_type_use_with_macros(
            repo_root, source_files, clang_args, custom_types_registry, None,
            macros, expand_macros
        )


    @staticmethod
    def build_data_type_use_with_macros(repo_root, source_files, clang_args, custom_types_registry=None,
                                        detected_macros=None,
                                        macros: List[str] = None,
                                        expand_macros: bool = True,
                                        use_parallel: Optional[bool] = None,
                                        max_workers: Optional[int] = None):
        """
        Build function → custom data types mapping.
        The detected_macros parameter is kept for API compatibility but is ignored.

        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Clang compilation arguments
            custom_types_registry: Optional set of known custom type names to filter against
            detected_macros: Ignored - kept for API compatibility
            macros: Optional list of macros to expand. If provided (even empty list triggers auto-detect),
                    macro expansion is enabled. If None, no macro expansion.
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
            use_parallel: If True, use parallel processing. If None, auto-detect based on file count.
            max_workers: Maximum number of worker processes. If None, use default.

        Returns:
            Dict mapping function names to sets of custom data types they use
        """
        # Determine if parallel processing should be used
        should_parallel, num_workers = CASTUtil._should_use_parallel(
            source_files, use_parallel, max_workers
        )
        
        # Determine macro flags for parallel processing
        if expand_macros:
            if macros is not None:
                if macros:
                    macro_flags = create_macro_flags(set(macros))
                else:
                    detected_macros_set, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                    macro_flags = create_macro_flags_excluding_derived(detected_macros_set, derived_macros)
            else:
                macro_flags = []
        else:
            macro_flags = []
        
        if should_parallel:
            logging.info(f"[+] Building data type usage in parallel with {num_workers} workers...")
            if expand_macros:
                # Pass 1: Without macros
                usage1 = CASTUtil._build_data_type_use_parallel(
                    repo_root, source_files, clang_args, [], custom_types_registry, num_workers
                )
                # Pass 2: With macros
                usage2 = CASTUtil._build_data_type_use_parallel(
                    repo_root, source_files, clang_args, macro_flags, custom_types_registry, num_workers
                )
                usage = CASTUtil._merge_data_type_usage(usage1, usage2)
            else:
                usage = CASTUtil._build_data_type_use_parallel(
                    repo_root, source_files, clang_args, macro_flags, custom_types_registry, num_workers
                )
        elif expand_macros:
            logging.info(f"[+] Building data type usage with expand macros mode (dual-pass)...")
            usage = CASTUtil._build_data_type_use_expand_macros(
                repo_root, source_files, clang_args, macros, custom_types_registry
            )
        else:
            logging.info(f"[+] Building data type usage with single-pass approach...")
            # Single pass: Build data type usage without macros defined
            usage = CASTUtil._build_data_type_use_single_pass(
                repo_root, source_files, clang_args, [], custom_types_registry
            )

        logging.info(f"[+] Data type usage completed: {len(usage)} functions with type usage found")

        return usage

    @staticmethod
    def _build_data_type_use_expand_macros(repo_root, source_files, clang_args, macros: List[str] = None,
                                         custom_types_registry=None):
        """
        Build data type usage with dual-pass macro handling (expand macros mode).
        
        This method builds the AST twice:
        1. Pass 1: Without macro expansion - captures code in #else branches
        2. Pass 2: With macro expansion - captures code in #if branches
        
        The results are merged to capture all code paths regardless of macro state.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macros: Optional list of macros to expand. If empty list, auto-detect macros.
                    If None, skip pass 2.
            custom_types_registry: Optional set of known custom type names to filter against
        
        Returns:
            Merged usage dict
        """
        logging.info("=" * 60)
        logging.info("EXPAND MACROS MODE: Building data type usage twice (with and without macros)")
        logging.info("=" * 60)
        
        # PASS 1: Without macro expansion
        logging.info("PASS 1: Building data type usage WITHOUT macro expansion...")
        usage1 = CASTUtil._build_data_type_use_single_pass(
            repo_root, source_files, clang_args, [], custom_types_registry
        )
        logging.info(f"Pass 1 found: {len(usage1)} functions with type usage")
        
        # PASS 2: With macro expansion
        logging.info("PASS 2: Building data type usage WITH macro expansion...")
        if macros is not None:
            if macros:
                # Use provided macros
                macro_flags = create_macro_flags(set(macros))
                logging.info(f"[+] Using {len(macros)} provided macros for expansion")
            else:
                # Auto-detect macros, excluding derived ones
                detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
                logging.info(f"[+] Auto-detected {len(detected_macros)} macros for expansion ({len(derived_macros)} derived, excluded)")
        else:
            # No macros specified, use empty list for pass 2
            macro_flags = []
            logging.info("[+] No macros specified, pass 2 will use empty macro flags")
        
        usage2 = CASTUtil._build_data_type_use_single_pass(
            repo_root, source_files, clang_args, macro_flags, custom_types_registry
        )
        logging.info(f"Pass 2 found: {len(usage2)} functions with type usage")
        
        # MERGE: Combine results from both passes
        logging.info("MERGING: Combining results from both passes...")
        merged_usage = CASTUtil._merge_data_type_usage(usage1, usage2)
        logging.info(f"Merged total: {len(merged_usage)} functions with type usage")
        
        return merged_usage

    @staticmethod
    def _build_data_type_use_single_pass(repo_root, source_files, clang_args, macro_flags, custom_types_registry=None):
        """Helper method to build data type usage in a single pass with given clang args."""
        index = cindex.Index.create()
        data_type_map = {}
        
        # Progress tracking
        total_files = len(source_files)
        progress_interval = max(1, total_files // 20)  # Log every 5% of files

        def traverse_for_types(node, current_fn, collected_types):
            """Iteratively traverse AST nodes to find type usage using a stack to avoid deep recursion."""
            # Use a stack for iterative traversal instead of recursion
            stack = [node]

            while stack:
                current = stack.pop()

                # Extract types from current node
                node_types = CASTUtil._extract_type_from_cursor(current)
                collected_types.update(node_types)

                # Add children to stack for processing (in reverse order to maintain traversal order)
                try:
                    children = list(current.get_children())
                    # Add in reverse order so we process in the same order as recursive version
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    # Some nodes might not support get_children(), continue anyway
                    continue

        for file_idx, source_file in enumerate(source_files):
            # Log progress periodically
            if file_idx % progress_interval == 0 or file_idx == total_files - 1:
                progress_pct = (file_idx + 1) * 100 // total_files
                logging.info(f"[+] Data type usage progress: {file_idx + 1}/{total_files} files ({progress_pct}%)")
            
            # Resolve repo_root to handle symlinks consistently with file discovery
            resolved_repo_root = Path(repo_root).resolve()
            
            try:
                # Try to get relative path using resolved repo root
                rel = str(Path(source_file).relative_to(resolved_repo_root))
            except ValueError:
                # If that fails, try with the original repo_root
                try:
                    rel = str(Path(source_file).relative_to(repo_root))
                except ValueError:
                    # Final fallback: use just the filename
                    rel = str(Path(source_file).name)
            try:
                # Use file-specific flags instead of generic args
                base_flags = get_clang_flags_for_file(source_file) or []
                additional_args = clang_args or []
                additional_macros = macro_flags or []
                file_flags = base_flags + additional_args + additional_macros
                tu = index.parse(str(source_file), args=file_flags)
            except Exception as e:
                logging.error(f"⚠️ parse {rel}: {e}")
                continue

            def collect_function_type_usage(root_cursor):
                """Iteratively collect type usage for each function definition using a stack to avoid deep recursion."""
                # Use a stack for iterative traversal instead of recursion
                # This prevents stack overflow on deeply nested AST structures (e.g., complex C++ templates)
                stack = [root_cursor]
                
                while stack:
                    cursor = stack.pop()
                    
                    try:
                        # Check cursor.kind safely - handle unknown cursor kinds from version mismatches
                        try:
                            cursor_kind = cursor.kind
                        except ValueError as kind_error:
                            # Unknown cursor kind - log at debug level and continue with children
                            logging.debug(f"Skipping cursor with unknown kind: {kind_error}")
                            try:
                                children = list(cursor.get_children())
                                for child in reversed(children):
                                    stack.append(child)
                            except Exception:
                                pass
                            continue
                        
                        if cursor_kind in ALLOWED_FUNCTION_KINDS and cursor.is_definition():
                            fn_name = CASTUtil.format_function_name(cursor)
                            if fn_name:
                                collected_types = set()

                                # Analyze function parameters for types
                                try:
                                    for arg in cursor.get_arguments():
                                        arg_types = CASTUtil._extract_type_from_cursor(arg)
                                        collected_types.update(arg_types)
                                except Exception:
                                    pass

                                # Analyze function return type
                                if hasattr(cursor, 'result_type') and cursor.result_type:
                                    return_type = cursor.result_type
                                    if hasattr(return_type, 'get_canonical'):
                                        return_type = return_type.get_canonical()
                                    return_type_spelling = getattr(return_type, 'spelling', '')
                                    if return_type_spelling and not CASTUtil._is_standard_library_type(return_type_spelling):
                                        # Check if this is an unnamed type with the old format
                                        if ("(unnamed " in return_type_spelling) and " at " in return_type_spelling and return_type_spelling.endswith(")"):
                                            # Generate a proper name for unnamed types
                                            try:
                                                # Try to get the cursor from the return type declaration
                                                return_type_decl = return_type.get_declaration()
                                                if return_type_decl:
                                                    start = return_type_decl.extent.start
                                                    file_path = start.file.name if start.file else None
                                                    clean_type = generate_unnamed_type_name(return_type_decl, file_path)
                                                else:
                                                    # Fallback to a simple unnamed name if we can't get the declaration
                                                    clean_type = "unnamed_unknown_unknown"
                                            except Exception:
                                                # Fallback to a simple unnamed name if generation fails
                                                clean_type = "unnamed_unknown_unknown"
                                        else:
                                            clean_type = strip_template_params(return_type_spelling).strip()
                                        
                                        if clean_type:
                                            collected_types.add(clean_type)

                                # Analyze function body for types
                                try:
                                    for child in cursor.get_children():
                                        traverse_for_types(child, fn_name, collected_types)
                                except Exception as e:
                                    logging.debug(f"Error analyzing function body for types: {e}")

                                # Filter to only custom types if registry provided
                                if custom_types_registry:
                                    collected_types = {t for t in collected_types if t in custom_types_registry}

                                # Store results
                                if collected_types:
                                    data_type_map[fn_name] = sorted(collected_types)

                    except ValueError as e:
                        # Handle other ValueError exceptions (not cursor kind related)
                        logging.debug(f"Skipping cursor due to ValueError: {e}")
                    except Exception as e:
                        # Handle any other unexpected errors when accessing cursor properties
                        logging.debug(f"Skipping cursor due to unexpected error: {e}")

                    # Always try to add children to stack, even if current cursor processing failed
                    try:
                        children = list(cursor.get_children())
                        # Add in reverse order so we process in the same order as recursive version
                        for child in reversed(children):
                            stack.append(child)
                    except Exception as e:
                        logging.debug(f"Error getting cursor children in collect_function_type_usage: {e}")

            try:
                collect_function_type_usage(tu.cursor)
            except Exception as e:
                logging.error(f"⚠️ Error processing data type usage in file {rel}: {e}")
                logging.error(f"Continuing with next file...")
                continue

        return data_type_map

    @staticmethod
    def _merge_data_type_usage(usage1, usage2):
        """Merge two data type usage mappings, combining entries from both."""
        merged = {}

        # Add all entries from usage1
        for fn_name, types in usage1.items():
            merged[fn_name] = set(types)

        # Add entries from usage2, merging with existing entries
        for fn_name, types in usage2.items():
            if fn_name in merged:
                # Merge types (sets automatically handle duplicates)
                merged[fn_name].update(types)
            else:
                # New function found in usage2
                merged[fn_name] = set(types)

        # Convert back to sorted lists
        result = {}
        for fn_name, types in merged.items():
            result[fn_name] = sorted(types)

        return result

    # ------------------------ Constants Detection ------------------------

    @staticmethod
    def _build_file_constant_registry(translation_unit, repo_root=None, resolved_repo_root=None):
        """
        OPTIMIZATION 2: Pre-build a complete constant registry for a file in a single pass.
        This eliminates the need for repeated searches through the AST.
        
        Args:
            translation_unit: The parsed translation unit
            repo_root: Optional repository root path (string) for filtering system headers
            resolved_repo_root: Optional resolved (absolute) repository root path for filtering
            
        Returns:
            Dict mapping constant names to their values
        """
        registry = {}
        
        def extract_literal_value(cursor):
            """Extract literal value from different cursor types."""
            try:
                if cursor.kind == cindex.CursorKind.INTEGER_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        try:
                            if token_spelling.startswith('0x') or token_spelling.startswith('0X'):
                                return int(token_spelling, 16)
                            elif token_spelling.startswith('0b') or token_spelling.startswith('0B'):
                                return int(token_spelling, 2)
                            elif token_spelling.startswith('0') and len(token_spelling) > 1 and token_spelling.isdigit():
                                return int(token_spelling, 8)
                            else:
                                return int(token_spelling)
                        except ValueError:
                            try:
                                float_val = float(token_spelling)
                                if float_val.is_integer():
                                    return int(float_val)
                                else:
                                    return float_val
                            except ValueError:
                                return token_spelling
                elif cursor.kind == cindex.CursorKind.FLOATING_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        try:
                            return float(token_spelling)
                        except ValueError:
                            clean_spelling = token_spelling.rstrip('fFlL')
                            try:
                                return float(clean_spelling)
                            except ValueError:
                                return token_spelling
                elif cursor.kind == cindex.CursorKind.CXX_BOOL_LITERAL_EXPR:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        return token_spelling.lower() == 'true'
            except Exception:
                pass
            return None
        
        def collect_constants(cursor):
            """Collect all constant definitions in a single pass."""
            stack = [cursor]
            
            while stack:
                current = stack.pop()
                
                try:
                    # OPTIMIZATION: Skip cursors from system/SDK headers
                    # This significantly reduces processing time by avoiding expensive
                    # get_tokens() calls on system headers
                    if repo_root is not None and resolved_repo_root is not None:
                        cursor_file = None
                        try:
                            if current.location and current.location.file:
                                cursor_file = current.location.file.name
                        except Exception:
                            pass
                        
                        # Skip processing if cursor is from a system/SDK header
                        if cursor_file and not _is_file_in_repo(cursor_file, repo_root, resolved_repo_root):
                            # Don't process this cursor or its children - they're from system headers
                            continue
                    
                    # Collect const/static variables
                    if current.kind == cindex.CursorKind.VAR_DECL:
                        if hasattr(current, 'type') and current.type:
                            type_spelling = getattr(current.type, 'spelling', '')
                            if 'const' in type_spelling or 'static' in type_spelling:
                                constant_name = current.spelling
                                if constant_name:
                                    for child in current.get_children():
                                        value = extract_literal_value(child)
                                        if value is not None and isinstance(value, (int, float)):
                                            registry[constant_name] = value
                                            break
                    
                    # Collect enum constants
                    elif current.kind == cindex.CursorKind.ENUM_CONSTANT_DECL:
                        constant_name = current.spelling
                        if constant_name:
                            try:
                                enum_value = current.enum_value
                                if isinstance(enum_value, (int, float)):
                                    registry[constant_name] = enum_value
                            except:
                                pass
                    
                    # Add children to stack
                    try:
                        children = list(current.get_children())
                        for child in reversed(children):
                            stack.append(child)
                    except Exception:
                        continue
                        
                except Exception:
                    pass
        
        collect_constants(translation_unit.cursor)
        return registry

    @staticmethod
    def _extract_constants_from_cursor_optimized(cursor, file_constants_registry):
        """
        OPTIMIZATION 1: Extract constants using pre-built registry instead of searching.
        This replaces the expensive search_for_constant() calls.
        
        Args:
            cursor: The cursor to extract constants from
            file_constants_registry: Pre-built registry of constants for this file
            
        Returns:
            Dict of constants found in this cursor
        """
        constants = {}
        
        try:
            # Check for variable references that might be constants
            if cursor.kind == cindex.CursorKind.DECL_REF_EXPR:
                ref = cursor.referenced
                if ref and ref.kind == cindex.CursorKind.VAR_DECL:
                    constant_name = ref.spelling
                    if constant_name in file_constants_registry:
                        constants[constant_name] = file_constants_registry[constant_name]
            
            # Check for enum constants
            elif cursor.kind == cindex.CursorKind.DECL_REF_EXPR:
                ref = cursor.referenced
                if ref and ref.kind == cindex.CursorKind.ENUM_CONSTANT_DECL:
                    constant_name = ref.spelling
                    if constant_name in file_constants_registry:
                        constants[constant_name] = file_constants_registry[constant_name]
            
            # Handle UNEXPOSED_EXPR nodes
            elif cursor.kind == cindex.CursorKind.UNEXPOSED_EXPR:
                try:
                    tokens = list(cursor.get_tokens())
                    if tokens and len(tokens) == 1:
                        token_spelling = tokens[0].spelling
                        if token_spelling and token_spelling.isidentifier():
                            if token_spelling in file_constants_registry:
                                constants[token_spelling] = file_constants_registry[token_spelling]
                except:
                    pass
                    
        except Exception:
            pass
        
        return constants

    @staticmethod
    def _extract_constants_from_cursor(cursor, translation_unit=None):
        """Extract constant values from a cursor, handling various cursor types."""
        constants = {}

        # Cache for constant definitions found in the translation unit
        _constant_definitions_cache = {}

        def find_constant_definition_in_tu(target_name):
            """Find a constant definition by name in the translation unit."""
            if target_name in _constant_definitions_cache:
                return _constant_definitions_cache[target_name]

            if not translation_unit:
                return None

            def search_for_constant(cursor):
                """Iteratively search for a constant definition using a stack to avoid deep recursion."""
                # Use a stack for iterative traversal instead of recursion
                stack = [cursor]

                while stack:
                    current = stack.pop()

                    # Check if current cursor matches our target
                    if (current.kind == cindex.CursorKind.VAR_DECL and
                        current.spelling == target_name):
                        if hasattr(current, 'type') and current.type:
                            type_spelling = getattr(current.type, 'spelling', '')
                            if ('const' in type_spelling or 'static' in type_spelling):
                                # Try to get the constant value
                                for child in current.get_children():
                                    value = extract_literal_value(child)
                                    if value is not None and isinstance(value, (int, float)):
                                        return value
                                    # Handle UNEXPOSED_EXPR that might contain the literal value
                                    elif child.kind == cindex.CursorKind.UNEXPOSED_EXPR:
                                        tokens = list(child.get_tokens())
                                        if tokens and len(tokens) == 1:
                                            token_spelling = tokens[0].spelling
                                            try:
                                                value = float(token_spelling)
                                                if isinstance(value, (int, float)):
                                                    return value
                                            except ValueError:
                                                pass

                    # Add children to stack for processing (in reverse order to maintain traversal order)
                    try:
                        children = list(current.get_children())
                        # Add in reverse order so we process in the same order as recursive version
                        for child in reversed(children):
                            stack.append(child)
                    except Exception:
                        # Some cursors might not support get_children()
                        continue

                return None

            result = search_for_constant(translation_unit.cursor)
            _constant_definitions_cache[target_name] = result
            return result

        def extract_literal_value(cursor):
            """Extract literal value from different cursor types."""
            try:
                if cursor.kind == cindex.CursorKind.INTEGER_LITERAL:
                    # Get the token spelling for integer literals
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        try:
                            # Handle different integer formats (decimal, hex, octal, binary)
                            if token_spelling.startswith('0x') or token_spelling.startswith('0X'):
                                return int(token_spelling, 16)
                            elif token_spelling.startswith('0b') or token_spelling.startswith('0B'):
                                return int(token_spelling, 2)
                            elif token_spelling.startswith('0') and len(token_spelling) > 1 and token_spelling.isdigit():
                                return int(token_spelling, 8)
                            else:
                                # Try integer parsing first
                                return int(token_spelling)
                        except ValueError:
                            # If integer parsing fails, try float parsing for scientific notation
                            try:
                                float_val = float(token_spelling)
                                # If it's a whole number, return as int
                                if float_val.is_integer():
                                    return int(float_val)
                                else:
                                    return float_val
                            except ValueError:
                                return token_spelling
                elif cursor.kind == cindex.CursorKind.FLOATING_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        try:
                            # Handle scientific notation like 1.0e6, 10e6, 1E-3, etc.
                            return float(token_spelling)
                        except ValueError:
                            # If float parsing fails, try to handle special cases
                            # Remove common suffixes like 'f', 'F', 'l', 'L'
                            clean_spelling = token_spelling.rstrip('fFlL')
                            try:
                                return float(clean_spelling)
                            except ValueError:
                                return token_spelling
                elif cursor.kind == cindex.CursorKind.STRING_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        return tokens[0].spelling
                elif cursor.kind == cindex.CursorKind.CHARACTER_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        return tokens[0].spelling
                elif cursor.kind == cindex.CursorKind.CXX_BOOL_LITERAL_EXPR:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        token_spelling = tokens[0].spelling
                        return token_spelling.lower() == 'true'
                elif cursor.kind == cindex.CursorKind.CXX_NULL_PTR_LITERAL_EXPR:
                    return None
                elif hasattr(cindex.CursorKind, 'OBJC_STRING_LITERAL') and cursor.kind == cindex.CursorKind.OBJC_STRING_LITERAL:
                    tokens = list(cursor.get_tokens())
                    if tokens:
                        return tokens[0].spelling
            except Exception:
                pass
            return None

        def traverse_for_constants(node):
            """Iteratively traverse AST nodes to find constant usage using a stack to avoid deep recursion."""
            # Use a stack for iterative traversal instead of recursion
            stack = [node]

            while stack:
                current = stack.pop()

                try:
                    # Check for variable references that might be constants
                    if current.kind == cindex.CursorKind.DECL_REF_EXPR:
                        ref = current.referenced
                        if ref and ref.kind == cindex.CursorKind.VAR_DECL:
                            # Check if it's a const or static variable
                            if hasattr(ref, 'type') and ref.type:
                                type_spelling = getattr(ref.type, 'spelling', '')
                                # Only include if it's explicitly const or static
                                if ('const' in type_spelling or 'static' in type_spelling):
                                    # Try to get the constant value from the definition
                                    for child in ref.get_children():
                                        value = extract_literal_value(child)
                                        if value is not None and isinstance(value, (int, float)):
                                            constants[ref.spelling] = value
                                            break

                    # Check for enum constants
                    elif current.kind == cindex.CursorKind.DECL_REF_EXPR:
                        ref = current.referenced
                        if ref and ref.kind == cindex.CursorKind.ENUM_CONSTANT_DECL:
                            # Get enum constant value
                            try:
                                enum_value = ref.enum_value
                                if isinstance(enum_value, (int, float)):
                                    constants[ref.spelling] = enum_value
                            except:
                                pass

                    # Handle UNEXPOSED_EXPR nodes that might contain constant references
                    elif current.kind == cindex.CursorKind.UNEXPOSED_EXPR:
                        try:
                            tokens = list(current.get_tokens())
                            if tokens and len(tokens) == 1:
                                # Single token might be a constant reference
                                token_spelling = tokens[0].spelling
                                if token_spelling and token_spelling.isidentifier():
                                    # Try to find this constant in the translation unit
                                    value = find_constant_definition_in_tu(token_spelling)
                                    if value is not None:
                                        constants[token_spelling] = value
                        except:
                            pass

                    # Check for macro references (preprocessor constants)
                    elif current.kind == cindex.CursorKind.MACRO_INSTANTIATION:
                        tokens = list(current.get_tokens())
                        if tokens:
                            macro_name = tokens[0].spelling
                            # Only include macros that follow constant naming conventions
                            # We can't easily determine the value of macros, so we skip them
                            # unless we can somehow evaluate them to numeric values
                            pass

                    # REMOVED: Direct literal values are no longer included
                    # We only want declared constants with numeric values

                    # Add children to stack for processing (in reverse order to maintain traversal order)
                    try:
                        children = list(current.get_children())
                        # Add in reverse order so we process in the same order as recursive version
                        for child in reversed(children):
                            stack.append(child)
                    except Exception:
                        # Some nodes might not support get_children(), continue anyway
                        continue
                except Exception:
                    pass

        traverse_for_constants(cursor)
        return constants

    @staticmethod
    def build_constants_usage(repo_root, source_files, clang_args, function_registry=None,
                              macros: List[str] = None,
                              expand_macros: bool = True):
        """
        Build function → constants mapping.

        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Clang compilation arguments
            function_registry: Optional set of known function names to filter against
            macros: Optional list of macros to expand. If provided (even empty list triggers auto-detect),
                    macro expansion is enabled. If None, no macro expansion.
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)

        Returns:
            Dict mapping function names to dicts of constants they use
        """
        return CASTUtil.build_constants_usage_with_macros(
            repo_root, source_files, clang_args, function_registry, None,
            macros, expand_macros
        )

    @staticmethod
    def build_constants_usage_with_macros(repo_root, source_files, clang_args, function_registry=None,
                                          detected_macros=None,
                                          macros: List[str] = None,
                                          expand_macros: bool = True,
                                          use_parallel: Optional[bool] = None,
                                          max_workers: Optional[int] = None):
        """
        Build function → constants mapping.
        The detected_macros parameter is kept for API compatibility but is ignored.

        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Clang compilation arguments
            function_registry: Optional set of known function names to filter against
            detected_macros: Ignored - kept for API compatibility
            macros: Optional list of macros to expand. If provided (even empty list triggers auto-detect),
                    macro expansion is enabled. If None, no macro expansion.
            expand_macros: If True, build AST twice (with and without macros) and merge results.
                                  This captures all code paths regardless of macro state. (default: False)
            use_parallel: If True, use parallel processing. If None, auto-detect based on file count.
            max_workers: Maximum number of worker processes. If None, use default.

        Returns:
            Dict mapping function names to dicts of constants they use
        """
        # Determine if parallel processing should be used
        should_parallel, num_workers = CASTUtil._should_use_parallel(
            source_files, use_parallel, max_workers
        )
        
        # Determine macro flags for parallel processing
        if expand_macros:
            if macros is not None:
                if macros:
                    macro_flags = create_macro_flags(set(macros))
                else:
                    detected_macros_set, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                    macro_flags = create_macro_flags_excluding_derived(detected_macros_set, derived_macros)
            else:
                macro_flags = []
        else:
            macro_flags = []
        
        if should_parallel:
            logging.info(f"[+] Building constants usage in parallel with {num_workers} workers...")
            if expand_macros:
                # Pass 1: Without macros
                usage1 = CASTUtil._build_constants_usage_parallel(
                    repo_root, source_files, clang_args, [], function_registry, num_workers
                )
                # Pass 2: With macros
                usage2 = CASTUtil._build_constants_usage_parallel(
                    repo_root, source_files, clang_args, macro_flags, function_registry, num_workers
                )
                usage = CASTUtil._merge_constants_usage(usage1, usage2)
            else:
                usage = CASTUtil._build_constants_usage_parallel(
                    repo_root, source_files, clang_args, macro_flags, function_registry, num_workers
                )
        elif expand_macros:
            logging.info(f"[+] Building constants usage with expand macros mode (dual-pass)...")
            usage = CASTUtil._build_constants_usage_expand_macros(
                repo_root, source_files, clang_args, macros, function_registry
            )
        else:
            logging.info(f"[+] Building constants usage with single-pass approach...")
            # Single pass: Build constants usage without macros defined
            usage = CASTUtil._build_constants_usage_single_pass(
                repo_root, source_files, clang_args, [], function_registry
            )

        logging.info(f"[+] Constants usage completed: {len(usage)} functions with constants usage found")

        return usage

    @staticmethod
    def _build_constants_usage_expand_macros(repo_root, source_files, clang_args, macros: List[str] = None,
                                           function_registry=None):
        """
        Build constants usage with dual-pass macro handling (expand macros mode).
        
        This method builds the AST twice:
        1. Pass 1: Without macro expansion - captures code in #else branches
        2. Pass 2: With macro expansion - captures code in #if branches
        
        The results are merged to capture all code paths regardless of macro state.
        
        Args:
            repo_root: Path to repository root
            source_files: List of source files to analyze
            clang_args: Base clang arguments
            macros: Optional list of macros to expand. If empty list, auto-detect macros.
                    If None, skip pass 2.
            function_registry: Optional set of known function names to filter against
        
        Returns:
            Merged usage dict
        """
        logging.info("=" * 60)
        logging.info("EXPAND MACROS MODE: Building constants usage twice (with and without macros)")
        logging.info("=" * 60)
        
        # PASS 1: Without macro expansion
        logging.info("PASS 1: Building constants usage WITHOUT macro expansion...")
        usage1 = CASTUtil._build_constants_usage_single_pass(
            repo_root, source_files, clang_args, [], function_registry
        )
        logging.info(f"Pass 1 found: {len(usage1)} functions with constants usage")
        
        # PASS 2: With macro expansion
        logging.info("PASS 2: Building constants usage WITH macro expansion...")
        if macros is not None:
            if macros:
                # Use provided macros
                macro_flags = create_macro_flags(set(macros))
                logging.info(f"[+] Using {len(macros)} provided macros for expansion")
            else:
                # Auto-detect macros, excluding derived ones
                detected_macros, derived_macros = CASTUtil.get_cached_preprocessor_macros_with_derived(source_files)
                macro_flags = create_macro_flags_excluding_derived(detected_macros, derived_macros)
                logging.info(f"[+] Auto-detected {len(detected_macros)} macros for expansion ({len(derived_macros)} derived, excluded)")
        else:
            # No macros specified, use empty list for pass 2
            macro_flags = []
            logging.info("[+] No macros specified, pass 2 will use empty macro flags")
        
        usage2 = CASTUtil._build_constants_usage_single_pass(
            repo_root, source_files, clang_args, macro_flags, function_registry
        )
        logging.info(f"Pass 2 found: {len(usage2)} functions with constants usage")
        
        # MERGE: Combine results from both passes
        logging.info("MERGING: Combining results from both passes...")
        merged_usage = CASTUtil._merge_constants_usage(usage1, usage2)
        logging.info(f"Merged total: {len(merged_usage)} functions with constants usage")
        
        return merged_usage

    @staticmethod
    def _build_constants_usage_single_pass(repo_root, source_files, clang_args, macro_flags, function_registry=None):
        """Helper method to build constants usage in a single pass with given clang args."""
        index = cindex.Index.create()
        constants_map = {}
        
        # Progress tracking
        total_files = len(source_files)
        progress_interval = max(1, total_files // 20)  # Log every 5% of files
        
        # OPTIMIZATION 1 & 2: Global constant registry cache per translation unit
        # Build complete constant registries upfront for each file to avoid redundant searches
        file_constant_registries = {}
        
        # Resolve repo_root once for all files
        resolved_repo_root = Path(repo_root).resolve()
        repo_root_str = str(repo_root)

        def traverse_for_constants(node, current_fn, collected_constants, tu, file_constants_registry):
            """Iteratively traverse AST nodes to find constants usage using a stack to avoid deep recursion."""
            # Use a stack for iterative traversal instead of recursion
            stack = [node]

            while stack:
                current = stack.pop()

                # OPTIMIZATION: Use pre-built registry instead of searching on-demand
                node_constants = CASTUtil._extract_constants_from_cursor_optimized(current, file_constants_registry)
                collected_constants.update(node_constants)

                # Add children to stack for processing (in reverse order to maintain traversal order)
                try:
                    children = list(current.get_children())
                    # Add in reverse order so we process in the same order as recursive version
                    for child in reversed(children):
                        stack.append(child)
                except Exception:
                    # Some nodes might not support get_children(), continue anyway
                    continue

        for file_idx, source_file in enumerate(source_files):
            # Log progress periodically
            if file_idx % progress_interval == 0 or file_idx == total_files - 1:
                progress_pct = (file_idx + 1) * 100 // total_files
                logging.info(f"[+] Constants usage progress: {file_idx + 1}/{total_files} files ({progress_pct}%)")
            
            try:
                # Try to get relative path using resolved repo root
                rel = str(Path(source_file).relative_to(resolved_repo_root))
            except ValueError:
                # If that fails, try with the original repo_root
                try:
                    rel = str(Path(source_file).relative_to(repo_root))
                except ValueError:
                    # Final fallback: use just the filename
                    rel = str(Path(source_file).name)
            try:
                # Use file-specific flags instead of generic args
                base_flags = get_clang_flags_for_file(source_file) or []
                additional_args = clang_args or []
                additional_macros = macro_flags or []
                file_flags = base_flags + additional_args + additional_macros
                tu = index.parse(str(source_file), args=file_flags)
            except Exception as e:
                logging.error(f"⚠️ parse {rel}: {e}")
                continue

            # OPTIMIZATION 2: Pre-build constant registry for this file
            # Pass repo_root parameters to filter out system/SDK headers
            file_constants_registry = CASTUtil._build_file_constant_registry(tu, repo_root_str, resolved_repo_root)
            file_constant_registries[str(source_file)] = file_constants_registry

            def collect_function_constants_usage(root_cursor):
                """Iteratively collect constants usage for each function definition using a stack to avoid deep recursion."""
                # Use a stack for iterative traversal instead of recursion
                # This prevents stack overflow on deeply nested AST structures (e.g., complex C++ templates)
                stack = [root_cursor]
                
                while stack:
                    cursor = stack.pop()
                    
                    try:
                        # OPTIMIZATION: Skip cursors from system/SDK headers
                        # This significantly reduces processing time by avoiding expensive
                        # get_tokens() calls on system headers
                        cursor_file = None
                        try:
                            if cursor.location and cursor.location.file:
                                cursor_file = cursor.location.file.name
                        except Exception:
                            pass
                        
                        # Skip processing if cursor is from a system/SDK header
                        if cursor_file and not _is_file_in_repo(cursor_file, repo_root_str, resolved_repo_root):
                            continue
                        
                        # Check cursor.kind safely - handle unknown cursor kinds from version mismatches
                        try:
                            cursor_kind = cursor.kind
                        except ValueError as kind_error:
                            # Unknown cursor kind - log at debug level and continue with children
                            logging.debug(f"Skipping cursor with unknown kind: {kind_error}")
                            try:
                                children = list(cursor.get_children())
                                for child in reversed(children):
                                    stack.append(child)
                            except Exception:
                                pass
                            continue
                        
                        if cursor_kind in ALLOWED_FUNCTION_KINDS and cursor.is_definition():
                            fn_name = CASTUtil.format_function_name(cursor)
                            if fn_name:
                                collected_constants = {}

                                # Analyze function body for constants
                                try:
                                    for child in cursor.get_children():
                                        traverse_for_constants(child, fn_name, collected_constants, tu, file_constants_registry)
                                except Exception as e:
                                    logging.debug(f"Error analyzing function body for constants: {e}")

                                # Store results
                                if collected_constants:
                                    constants_map[fn_name] = collected_constants

                    except ValueError as e:
                        # Handle other ValueError exceptions (not cursor kind related)
                        logging.debug(f"Skipping cursor due to ValueError: {e}")
                    except Exception as e:
                        # Handle any other unexpected errors when accessing cursor properties
                        logging.debug(f"Skipping cursor due to unexpected error: {e}")

                    # Always try to add children to stack, even if current cursor processing failed
                    try:
                        children = list(cursor.get_children())
                        # Add in reverse order so we process in the same order as recursive version
                        for child in reversed(children):
                            stack.append(child)
                    except Exception as e:
                        logging.debug(f"Error getting cursor children in collect_function_constants_usage: {e}")

            try:
                collect_function_constants_usage(tu.cursor)
            except Exception as e:
                logging.error(f"⚠️ Error processing constants usage in file {rel}: {e}")
                logging.error(f"Continuing with next file...")
                continue

        # OPTIMIZATION: Clear cache to free memory after processing all files
        file_constant_registries.clear()
        
        return constants_map

    @staticmethod
    def _merge_constants_usage(usage1, usage2):
        """Merge two constants usage mappings, combining entries from both."""
        merged = {}

        # Add all entries from usage1
        for fn_name, constants in usage1.items():
            merged[fn_name] = dict(constants)

        # Add entries from usage2, merging with existing entries
        for fn_name, constants in usage2.items():
            if fn_name in merged:
                # Merge constants (dict update handles duplicates)
                merged[fn_name].update(constants)
            else:
                # New function found in usage2
                merged[fn_name] = dict(constants)

        return merged

    # ------------------------ Nested Graph ------------------------

    @staticmethod
    def build_nested_call_graph(definitions_map, adjacency, max_depth, out_path, data_type_usage=None, constants_usage=None):
        """Build nested JSON tree of caller→callees with context (line/byte ranges), data type usage, and constants usage, grouped by file."""
        sys.setrecursionlimit(10000)

        sig_hint = {}
        for _, to_list in adjacency.items():
            for callee in to_list:
                b = base_function_name(callee)
                sig_hint.setdefault(b, callee)

        def context_for(base_fn):
            entries = definitions_map.get(base_fn)
            if not entries:
                return None  # Return None instead of dict with null values
            rec = sorted(entries, key=lambda t: (
                t[0],
                t[1] if len(t) > 1 and t[1] is not None else -1,
                t[2] if len(t) > 2 and t[2] is not None else -1
            ))[0]

            # Check if we have valid file path and line numbers
            file_path = rec[0] if len(rec) > 0 else None
            start_line = rec[1] if len(rec) > 1 else None
            end_line = rec[2] if len(rec) > 2 else None

            # Return None if any required field is missing
            if not file_path or start_line is None or end_line is None:
                return None

            return {
                "file": file_path,
                "start": start_line,
                "end": end_line
            }

        def display_name(fn_name):
            # Extract base name if it's a fully qualified name
            base_fn = base_function_name(fn_name)
            if base_fn in sig_hint:
                return sig_hint[base_fn]
            return fn_name

        def build_node(fn, depth, seen):
            base_fn = base_function_name(fn)
            context = context_for(base_fn)

            # Skip this node entirely if context is None (missing file/line info)
            if context is None:
                return None

            # Prepare function_context with only line numbers (file will be at parent level)
            function_context = {
                "start": context["start"],
                "end": context["end"]
            }

            # Add data type usage if available
            function_data_types = []
            if data_type_usage:
                # Look for data types used by this function
                # Try exact match first, then try with different function name formats

                # Try exact match with display name
                display_fn = display_name(base_fn)
                if display_fn in data_type_usage:
                    function_data_types = data_type_usage[display_fn]
                # Try base function name
                elif base_fn in data_type_usage:
                    function_data_types = data_type_usage[base_fn]
                # Try to find a match by checking if any key ends with the base function name
                else:
                    for key in data_type_usage:
                        if key.endswith("::" + base_fn) or key.endswith("::" + base_fn + "()"):
                            function_data_types = data_type_usage[key]
                            break

            # Add constants usage if available
            function_constants = {}
            if constants_usage:
                # Look for constants used by this function
                # Try exact match first, then try with different function name formats

                # Try exact match with display name
                display_fn = display_name(base_fn)
                if display_fn in constants_usage:
                    function_constants = constants_usage[display_fn]
                # Try base function name
                elif base_fn in constants_usage:
                    function_constants = constants_usage[base_fn]
                # Try to find a match by checking if any key ends with the base function name
                else:
                    for key in constants_usage:
                        if key.endswith("::" + base_fn) or key.endswith("::" + base_fn + "()"):
                            function_constants = constants_usage[key]
                            break

            node = {
                "function": display_name(base_fn),
                "context": function_context,
                "data_types_used": function_data_types if function_data_types else [],
                "file_path": context["file"]  # Store file path temporarily for grouping
            }

            # Only add constants_used if there are constants
            if function_constants:
                node["constants_used"] = function_constants
            if depth >= max_depth:
                return node

            unique_children = {}
            for callee in adjacency.get(base_fn, []):
                b = base_function_name(callee)
                cur = unique_children.get(b)
                if cur is None or ('(' not in cur and '(' in callee):
                    unique_children[b] = callee

            functions_invoked = []
            for decorated in sorted(unique_children.values()):
                b = base_function_name(decorated)
                if b in seen:
                    continue
                
                seen.add(b)
                
                # Get context for the invoked function if available
                invoked_context = context_for(b)
                
                # Always use dict format for consistency
                # Context is included when available (function has implementation in repo)
                # Context is omitted when not available (system functions, excluded files)
                invoked_entry = {
                    "function": display_name(decorated)
                }
                if invoked_context:
                    invoked_entry["context"] = invoked_context
                
                functions_invoked.append(invoked_entry)
                
                seen.remove(b)

            if functions_invoked:
                node["functions_invoked"] = functions_invoked

            return node

        # Build all root nodes first
        all_root_nodes = []
        for caller in sorted(adjacency.keys()):
            root_node = build_node(caller, 0, seen={caller})
            if root_node is not None:  # Only add valid root nodes
                all_root_nodes.append(root_node)

        # Group functions by file
        files_map = {}

        def process_node_for_grouping(node):
            """Recursively process nodes to group by file and clean up structure."""
            file_path = node.pop("file_path")  # Remove temporary file_path

            # Process functions_invoked (no recursive processing needed since it's just function names)
            if "functions_invoked" in node:
                # Keep the functions_invoked list as-is since it's just function names
                pass

            # Add to files_map
            if file_path not in files_map:
                files_map[file_path] = []

            # Check if this function is already in the file's function list
            function_exists = False
            for existing_func in files_map[file_path]:
                if existing_func["function"] == node["function"]:
                    function_exists = True
                    break

            if not function_exists:
                func_entry = {
                    "function": node["function"],
                    "context": node["context"],
                    "functions_invoked": node.get("functions_invoked", []),
                    "data_types_used": node.get("data_types_used", [])
                }

                # Only add constants_used if it exists and has content
                if "constants_used" in node and node["constants_used"]:
                    func_entry["constants_used"] = node["constants_used"]

                files_map[file_path].append(func_entry)

            return node

        # Process all root nodes to populate files_map
        for root_node in all_root_nodes:
            process_node_for_grouping(root_node)

        # Convert files_map to the desired output format
        result = []
        for file_path in sorted(files_map.keys()):
            functions = files_map[file_path]
            # Clean up the function structure
            cleaned_functions = []
            for func in functions:
                cleaned_func = {
                    "function": func["function"],
                    "context": {
                        "start": func["context"]["start"],
                        "end": func["context"]["end"]
                    },
                    "functions_invoked": func["functions_invoked"],
                    "data_types_used": func["data_types_used"]
                }

                # Only add constants_used if it exists and has content
                if "constants_used" in func and func["constants_used"]:
                    cleaned_func["constants_used"] = func["constants_used"]
                cleaned_functions.append(cleaned_func)

            result.append({
                "file": file_path,
                "functions": cleaned_functions
            })

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=False)

        total_functions = sum(len(file_entry["functions"]) for file_entry in result)
        logging.info(f"[+] Wrote nested call graph ({len(result)} files, {total_functions} functions) to {out_path}")


    @staticmethod
    def set_clang_path_from_brew_or_pip():
        """
        Set CLang path from brew or pip
        """
        # Call the centralized implementation in Environment class
        Environment.set_clang_path_from_brew_or_pip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=str, required=True, help="Path to repo root")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Directories to exclude")
    parser.add_argument("--clang-arg", dest="clang_args", action="append", default=[],
                        help="Extra clang args (repeatable)")
    parser.add_argument("--dump-function", default=None, help="Function to dump calls from")
    parser.add_argument("--filter-external-calls", action="store_true",
                        help="If set, filter callees to only repo-defined functions")
    parser.add_argument("--max-depth", type=int, default=1,
                        help="Max depth for expanding call tree")
    parser.add_argument("--functions-out", default="/tmp/clang_defined_functions.json",
                        help="Path to write function definitions JSON")
    parser.add_argument("--nested-out", default="/tmp/clang_nested_call_graph.json",
                        help="Path to write nested call graph JSON")
    parser.add_argument("--defined-data-types-out", default="/tmp/defined_data_types.json",
                        help="Path to write data type definitions JSON with header and implementation files")
    parser.add_argument("--defined-constants-out", default="/tmp/defined_constants.json",
                        help="Path to write constants definitions JSON")
    parser.add_argument("--data-type-use-out", default="/tmp/data_type_usage.json",
                        help="Path to write function data type usage JSON")
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    exclude_set = set(args.exclude)

    # Add project root to Python path for imports
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))

    # Use centralized logging
    setup_default_logging()

    Environment.initialize_libclang()


    source_files = CASTUtil.find_source_files(repo_root, exclude_set)

    # Build function registry
    defined_funcs, definitions_map = CASTUtil.build_function_registry(
        repo_root=repo_root,
        source_files=source_files,
        clang_args=args.clang_args,
        out_path=args.functions_out
    )

    # Build adjacency (caller→callees)
    adjacency = CASTUtil.build_forward_call_graph(
        repo_root=repo_root,
        source_files=source_files,
        clang_args=args.clang_args,
        filter_external_calls=args.filter_external_calls,
        registry=defined_funcs
    )

    # Build data types registry
    data_types_registry = CASTUtil.build_data_types_registry(
        repo_root=repo_root,
        source_files=source_files,
        clang_args=args.clang_args,
        out_path=args.defined_data_types_out
    )

    # Build constants registry
    constants_registry = CASTUtil.build_constants_registry(
        repo_root=repo_root,
        source_files=source_files,
        clang_args=args.clang_args,
        out_path=args.defined_constants_out
    )

    # Build data type usage mapping
    custom_types = set(data_types_registry.keys()) if data_types_registry else None
    data_type_usage = CASTUtil.build_data_type_use(
        repo_root=repo_root,
        source_files=source_files,
        clang_args=args.clang_args,
        custom_types_registry=custom_types
    )

    # Build constants usage mapping
    constants_usage = CASTUtil.build_constants_usage(
        repo_root=repo_root,
        source_files=source_files,
        clang_args=args.clang_args,
        function_registry=defined_funcs
    )

    # Write data type usage
    with open(args.data_type_use_out, 'w', encoding='utf-8') as f:
        json.dump(data_type_usage, f, indent=2, sort_keys=True)

    total_functions = len(data_type_usage)
    total_type_usages = sum(len(types) for types in data_type_usage.values())
    logging.info(f"[+] Wrote data type usage for {total_functions} functions "
                f"({total_type_usages} total type usages) to {args.data_type_use_out}")

    # Write constants usage
    constants_out = args.data_type_use_out.replace('data_type_usage.json', 'constants_usage.json')
    with open(constants_out, 'w', encoding='utf-8') as f:
        json.dump(constants_usage, f, indent=2, sort_keys=True)

    total_constants_functions = len(constants_usage)
    total_constants_usages = sum(len(constants) for constants in constants_usage.values())
    logging.info(f"[+] Wrote constants usage for {total_constants_functions} functions "
                f"({total_constants_usages} total constants usages) to {constants_out}")

    # Build nested call graph JSON
    CASTUtil.build_nested_call_graph(
        definitions_map=definitions_map,
        adjacency=adjacency,
        max_depth=args.max_depth,
        out_path=args.nested_out,
        data_type_usage=data_type_usage,
        constants_usage=constants_usage
    )


if __name__ == "__main__":
    main()

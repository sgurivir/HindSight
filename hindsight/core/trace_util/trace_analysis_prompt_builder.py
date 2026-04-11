#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
TraceAnalysisPromptBuilder - Main class for trace analysis functionality.
Handles configuration, AST generation, file processing, and callstack analysis.
"""

# Standard library imports
import os
import sys
import time
import json
import traceback
import shutil
import random
from pathlib import Path

# Third-party imports

# Local imports
from ...utils.output_directory_provider import get_output_directory_provider
from ..lang_util.code_context_pruner import CodeContextPruner
from ..lang_util.cast_util import CASTUtil
from ..ast_index import RepoAstIndex
from ...utils.config_util import load_config_tolerant
from ...utils.file_util import read_json_file
from ...utils.log_util import get_logger

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

class TraceAnalysisPromptBuilder:
    """
    Main class for trace analysis functionality.
    Handles configuration, AST generation, file processing, and callstack analysis.
    """

    def __init__(self, file_content_provider=None, ast_files_config=None):
        """
        Initialize the TraceAnalysisPromptBuilder with FileContentProvider and AST file paths.

        Args:
            file_content_provider: FileContentProvider instance (optional, will create if None)
            ast_files_config: Dictionary containing AST file paths (optional)
                - merged_functions_file: Path to merged functions JSON
                - merged_graph_file: Path to merged call graph JSON
                - merged_data_types_file: Path to merged data types JSON
                - output_dir: Directory for trace analysis outputs
        """
        self.logger = get_logger(__name__)

        # Configuration data
        self.config = None
        self.repo_path = None
        self.project_name = None
        self.ignore_dirs = None
        self.hotspot_file = None
        self.output_dir = None

        # Generated files and data - now provided externally
        self.processed_hotspot_file = None
        self.merged_functions_file = None
        self.merged_graph_file = None
        self.merged_data_types_file = None
        self.file_content_provider = file_content_provider  # Provided externally
        self.function_lookup = None

        # Store AST files configuration if provided
        self.ast_files_config = ast_files_config or {}

        # Initialize centralized AST index for lazy loading
        self.ast_index = RepoAstIndex()

        # Analysis results
        self.results = None
        self.files_not_found = None

        # AnalyzedRecordsRegistry for tracking analyzed callstacks
        self.analyzed_records_registry = None

        # Number of traces to analyze (limits callstack processing)
        self.num_traces_to_analyze = None

    def _get_file_mapping_paths(self):
        """
        Get the file mapping paths for pickle and JSON files.

        Returns:
            tuple: (file_mapping_index_path, file_mapping_json_path)
        """
        output_provider = get_output_directory_provider()
        artifacts_dir = output_provider.get_repo_artifacts_dir()
        file_mapping_index = f"{artifacts_dir}/code_insights/file_mapping.pkl"
        file_mapping_json = f"{artifacts_dir}/code_insights/file_mapping.json"
        return file_mapping_index, file_mapping_json

    def load_and_validate_configuration(self, config_path, hotspot_path, repo_path):
        """Load and validate configuration and paths."""
        # Load configuration using tolerant mode for hotspot analysis configs
        self.config = load_config_tolerant(config_path)

        # Extract configuration values - repo_path is now always provided via command line
        self.repo_path = Path(repo_path)
        self.project_name = self.config.get("project_name", "unknown_project")
        self.ignore_dirs = set(self.config.get("exclude_directories", []))

        # Validate paths
        if not self.repo_path.exists():
            self.logger.error(f"Repository path does not exist: {self.repo_path}")
            return False

        self.hotspot_file = Path(hotspot_path)
        if not self.hotspot_file.exists():
            self.logger.error(f"Hotspot file does not exist: {self.hotspot_file}")
            return False

        return True

    def setup_output_directory(self):
        """Create and return output directory path."""
        # Use provided output directory from ast_files_config, or create default
        if self.ast_files_config.get('output_dir'):
            self.output_dir = Path(self.ast_files_config['output_dir'])
        else:
            # Fallback to default if not provided - use results/trace_analysis structure
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            self.output_dir = Path(f"{artifacts_dir}/trace_analysis")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Created output directory: {self.output_dir}")

        return self.output_dir

    def log_reuse_summary(self):
        """Log a summary of what files are being reused vs regenerated."""
        self.logger.info("=== File Reuse Summary ===")

        # Check each type of file using the configured paths
        files_to_check = [
            ("Processed hotspots", self.output_dir / "processed_hotspots.json"),
            ("Merged functions", self.merged_functions_file if self.merged_functions_file else self.output_dir / "merged_functions.json"),
            ("Merged call graph", self.merged_graph_file if self.merged_graph_file else self.output_dir / "merged_call_graph.json"),
            ("Merged data types", self.merged_data_types_file if self.merged_data_types_file else self.output_dir / "merged_defined_data_types.json"),
            ("Trace analysis results", self.output_dir / "trace_analysis" / "trace_analysis_results.json"),
        ]

        reused_count = 0
        total_count = len(files_to_check)

        for file_desc, file_path in files_to_check:
            if file_path and Path(file_path).exists():
                self.logger.info(f"  ✓ REUSING: {file_desc}")
                reused_count += 1
            else:
                self.logger.info(f"  ✗ GENERATING: {file_desc}")

        self.logger.info(f"Reusing {reused_count}/{total_count} existing files ({reused_count/total_count*100:.1f}%)")
        self.logger.info("=== End Reuse Summary ===")

    def process_hotspot_data(self):
        """Process callstack format file and return processed file path."""
        # Process callstack file using new format
        self.processed_hotspot_file = self.output_dir / "processed_hotspots.json"

        # Check if processed hotspot file already exists
        should_reprocess = False
        if self.processed_hotspot_file.exists():
            # Check if the existing file has enough traces for the requested analysis
            try:
                existing_data = read_json_file(str(self.processed_hotspot_file))
                existing_count = len(existing_data) if existing_data else 0
                
                # If num_traces_to_analyze is set and existing file has fewer traces, reprocess
                if hasattr(self, 'num_traces_to_analyze') and self.num_traces_to_analyze:
                    if existing_count < self.num_traces_to_analyze:
                        self.logger.info(f"Existing processed file has {existing_count} traces, but {self.num_traces_to_analyze} requested")
                        self.logger.info("Reprocessing hotspot file to get more traces...")
                        should_reprocess = True
                    else:
                        self.logger.info(f"Reusing existing processed hotspot file: {self.processed_hotspot_file}")
                        self.logger.info(f"File contains {existing_count} traces (>= {self.num_traces_to_analyze} requested)")
                else:
                    self.logger.info(f"Reusing existing processed hotspot file: {self.processed_hotspot_file}")
                    self.logger.info(f"File contains {existing_count} traces")
            except Exception as e:
                self.logger.warning(f"Error reading existing processed file: {e}")
                self.logger.info("Reprocessing hotspot file...")
                should_reprocess = True
        else:
            self.logger.info("Processing callstack format file...")
            should_reprocess = True

        if should_reprocess:
            self._process_callstack_format()

        return self.processed_hotspot_file

    def _process_callstack_format(self):
        """
        Process the new callstack format file and convert to processed JSON format.
        Supports batching based on num_traces_to_analyze and batch_index.
        """
        try:
            # Parse the callstack file into individual traces
            all_traces = self._parse_callstack_file(self.hotspot_file)
            
            if not all_traces:
                self.logger.error("No traces found in callstack file")
                return False
            
            self.logger.info(f"Parsed {len(all_traces)} traces from callstack file")
            
            # Apply batching if num_traces_to_analyze is set
            if hasattr(self, 'num_traces_to_analyze') and self.num_traces_to_analyze:
                num_traces = self.num_traces_to_analyze
                batch_index = getattr(self, 'batch_index', 0)
                
                # Calculate batch boundaries
                start_idx = batch_index * num_traces
                end_idx = start_idx + num_traces
                
                # Check if batch is out of range
                if start_idx >= len(all_traces):
                    self.logger.warning(f"Batch {batch_index} is out of range. Total traces available: {len(all_traces)}, requested start: {start_idx}")
                    self.logger.info(f"Using all available traces instead of batch {batch_index}")
                    selected_traces = all_traces
                else:
                    # Select the requested batch
                    selected_traces = all_traces[start_idx:end_idx]
                    self.logger.info(f"Selected batch {batch_index}: traces {start_idx}-{min(end_idx-1, len(all_traces)-1)} ({len(selected_traces)} traces)")
            else:
                selected_traces = all_traces
                self.logger.info(f"Processing all {len(selected_traces)} traces")
            
            # Convert to the expected processed format (list of callstack groups)
            processed_data = []
            for trace in selected_traces:
                # Each trace becomes a callstack group
                callstack_group = []
                for frame in trace:
                    # Convert frame to expected format
                    entry = {
                        "path": frame.get('function_name', ''),
                        "ownerName": frame.get('library_name', ''),
                        "sourcePath": frame.get('source_file', ''),
                        "cost": int(float(frame.get('raw_cost', 0))),
                        "normalizedCost": float(frame.get('percentage', 0))
                    }
                    callstack_group.append(entry)
                
                if callstack_group:
                    processed_data.append(callstack_group)
            
            # Save processed data to JSON file
            with open(self.processed_hotspot_file, 'w', encoding='utf-8') as f:
                json.dump(processed_data, f, indent=2)
            
            self.logger.info(f"Processed callstack data saved to: {self.processed_hotspot_file}")
            self.logger.info(f"Generated {len(processed_data)} callstack groups")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error processing callstack format: {e}")
            return False

    def _parse_callstack_file(self, file_path):
        """
        Parse the callstack file and extract individual traces.
        
        Args:
            file_path: Path to the callstack file
            
        Returns:
            list: List of traces, where each trace is a list of frame dictionaries
        """
        traces = []
        current_trace = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    
                    # Skip empty lines
                    if not line:
                        continue
                    
                    # Check for trace separator
                    if line == "=====":
                        # End of current trace
                        if current_trace:
                            traces.append(current_trace)
                            current_trace = []
                        continue
                    
                    # Parse callstack frame
                    # Format: "2% (2.05) libsystem_kernel.dylib mach_msg2_trap"
                    # or: "2% (2.05) libsystem_kernel.dylib mach_msg_overwrite (mach_msg.c)"
                    frame = self._parse_callstack_frame(line)
                    if frame:
                        current_trace.append(frame)
                
                # Add the last trace if it exists
                if current_trace:
                    traces.append(current_trace)
            
            self.logger.info(f"Parsed {len(traces)} traces from callstack file")
            return traces
            
        except Exception as e:
            self.logger.error(f"Error parsing callstack file {file_path}: {e}")
            return []

    def _parse_callstack_frame(self, line):
        """
        Parse a single callstack frame line.
        
        Args:
            line: Line from callstack file
            
        Returns:
            dict: Frame information or None if parsing fails
        """
        try:
            # Format: "2% (2.05) libsystem_kernel.dylib mach_msg2_trap"
            # or: "2% (2.05) libsystem_kernel.dylib mach_msg_overwrite (mach_msg.c)"
            
            # Extract percentage
            if not line or not line[0].isdigit():
                return None
            
            # Find the percentage part
            pct_end = line.find('%')
            if pct_end == -1:
                return None
            
            percentage_str = line[:pct_end]
            percentage = float(percentage_str)
            
            # Find the raw cost in parentheses
            raw_start = line.find('(', pct_end)
            raw_end = line.find(')', raw_start)
            if raw_start == -1 or raw_end == -1:
                return None
            
            raw_cost_str = line[raw_start+1:raw_end]
            raw_cost = float(raw_cost_str)
            
            # Extract the rest (library and function)
            rest = line[raw_end+1:].strip()
            
            # Split into library and function parts
            parts = rest.split(' ', 1)
            if len(parts) < 2:
                library_name = ""
                function_part = rest
            else:
                library_name = parts[0]
                function_part = parts[1]
            
            # Check if there's a source file in parentheses at the end
            source_file = ""
            if function_part.endswith(')') and '(' in function_part:
                # Find the last occurrence of '('
                last_paren = function_part.rfind('(')
                source_file = function_part[last_paren+1:-1]
                function_name = function_part[:last_paren].strip()
            else:
                function_name = function_part
            
            return {
                'percentage': percentage,
                'raw_cost': raw_cost,
                'library_name': library_name,
                'function_name': function_name,
                'source_file': source_file
            }
            
        except Exception as e:
            self.logger.debug(f"Error parsing callstack frame '{line}': {e}")
            return None

    def set_batch_parameters(self, num_traces_to_analyze, batch_index):
        """
        Set batching parameters for callstack processing.
        
        Args:
            num_traces_to_analyze: Number of traces to analyze per batch
            batch_index: Batch index (0-based)
        """
        self.num_traces_to_analyze = num_traces_to_analyze
        self.batch_index = batch_index

    def setup_ast_files(self):
        """Setup AST file paths from provided configuration or check if they exist."""
        # AST file paths must be provided by the calling analyzer
        if not self.ast_files_config:
            self.logger.error("AST files configuration not provided by analyzer")
            return False

        # Get AST file paths from the provided configuration
        merged_functions_file = self.ast_files_config.get('merged_functions_file')
        merged_graph_file = self.ast_files_config.get('merged_graph_file')
        merged_data_types_file = self.ast_files_config.get('merged_data_types_file')

        if not all([merged_functions_file, merged_graph_file, merged_data_types_file]):
            self.logger.error("Incomplete AST files configuration provided by analyzer")
            self.logger.error("Required keys: merged_functions_file, merged_graph_file, merged_data_types_file")
            return False

        self.merged_functions_file = Path(merged_functions_file)
        self.merged_graph_file = Path(merged_graph_file)
        self.merged_data_types_file = Path(merged_data_types_file)

        # Check if AST files already exist
        if (self.merged_functions_file.exists() and self.merged_graph_file.exists() and
            self.merged_data_types_file.exists()):
            self.logger.info("Using existing AST files:")
            self.logger.info(f"  - {self.merged_functions_file}")
            self.logger.info(f"  - {self.merged_graph_file}")
            self.logger.info(f"  - {self.merged_data_types_file}")
            return True
        else:
            self.logger.warning("Required AST files not found:")
            if not self.merged_functions_file.exists():
                self.logger.warning(f"  - Missing: {self.merged_functions_file}")
            if not self.merged_graph_file.exists():
                self.logger.warning(f"  - Missing: {self.merged_graph_file}")
            if not self.merged_data_types_file.exists():
                self.logger.warning(f"  - Missing: {self.merged_data_types_file}")
            self.logger.error("AST files must be generated first by running code analysis")
            return False

    def setup_file_content_provider(self):
        """
        Setup FileContentProvider - use provided instance or create new one.
        """
        if self.file_content_provider is None:
            # Fallback: try to get existing FileContentProvider singleton
            from ...utils.file_content_provider import FileContentProvider

            self.logger.warning("FileContentProvider not provided, attempting to get existing singleton")

            try:
                self.file_content_provider = FileContentProvider.get()
                self.logger.info("Successfully retrieved existing FileContentProvider singleton")
            except RuntimeError:
                self.logger.error("FileContentProvider singleton not initialized and no instance provided")
                self.logger.error("FileContentProvider should be created in AnalysisRunner.create_file_content_provider() first")
                raise RuntimeError(
                    "FileContentProvider not available. It should be initialized in AnalysisRunner first."
                )
        else:
            self.logger.info("Using provided FileContentProvider instance")

        return self.file_content_provider

    def process_callstacks(self):
        """Process callstacks from the processed JSON and return results."""
        # Check if existing callstack results can be reused
        results_file = self.output_dir / "trace_analysis" / "trace_analysis_results.json"

        if results_file.exists():
            self.logger.info("Reusing existing callstack processing results:")
            self.logger.info(f"  - {results_file}")

            try:
                # Load existing results
                self.results = read_json_file(str(results_file))
                if self.results:
                    # Initialize files_not_found as empty since we're reusing results
                    self.files_not_found = []
                    self.logger.info(f"Successfully loaded {len(self.results)} callstack groups from existing results")
                    return self.results, self.files_not_found
                else:
                    self.logger.warning("Existing results file is empty, reprocessing...")
            except Exception as e:
                self.logger.warning(f"Failed to load existing results: {e}")
                self.logger.info("Reprocessing callstacks...")
        else:
            self.logger.info("Processing callstacks...")

        # Load processed hotspot data
        processed_data = read_json_file(str(self.processed_hotspot_file))

        if not processed_data:
            self.logger.error("Failed to load processed hotspot data")
            return None, None

        # Process each callstack entry
        self.results = []
        self.files_not_found = []  # Track files that are not found

        # Note: Batch selection and limiting is already handled in _process_callstack_format()
        # No need to apply num_traces_to_analyze limit again here
        total_groups = len(processed_data)
        groups_to_process = total_groups
        self.logger.info(f"Processing all {groups_to_process} callstack groups (batch selection already applied)")

        for i, callstack_group in enumerate(processed_data):
            self.logger.info(f"Processing callstack group {i+1}/{groups_to_process}")

            group_results = []
            for entry in callstack_group:
                # Handle case where entry might be a string instead of a dictionary
                if isinstance(entry, str):
                    # If entry is a string, treat it as the path
                    path = entry
                    owner_name = ""
                    source_path = ""
                elif isinstance(entry, dict):
                    # If entry is a dictionary, extract fields normally
                    path = entry.get("path", "")
                    owner_name = entry.get("ownerName", "")
                    source_path = entry.get("sourcePath", "")
                else:
                    # Skip invalid entries
                    self.logger.warning(f"Skipping invalid entry type: {type(entry)}")
                    continue


                # Extract filename from path if it contains file information
                filename = None
                if source_path and source_path != "":
                    # Extract just the filename from the source path
                    filename = os.path.basename(source_path)
                elif "/" in path:
                    # Try to extract filename from path
                    potential_filename = path.split("/")[-1]
                    if "." in potential_filename:
                        filename = potential_filename

                result_entry = {
                    "path": path,
                    "ownerName": owner_name,
                    "sourcePath": source_path,
                    "cost": entry.get("cost", 0) if isinstance(entry, dict) else 0,
                    "normalizedCost": entry.get("normalizedCost", 0.0) if isinstance(entry, dict) else 0.0
                }

                if filename:
                    # Try to get full path from FileContentProvider
                    try:
                        filename_probable_path = self.file_content_provider.guess_path(filename)
                        content = self.file_content_provider.read_text(filename_probable_path)
                        if content:
                            line_count = len(content.split('\n'))
                            result_entry["fullFilePath"] = f"Found file: {filename_probable_path}"
                            result_entry["fileLineCount"] = line_count
                            # Don't log found files - only log files not found
                        else:
                            result_entry["fullFilePath"] = "File not found"
                            if filename not in self.files_not_found:
                                self.files_not_found.append(filename)
                                self.logger.info(f"File not found: {filename}")
                    except Exception as e:
                        result_entry["fullFilePath"] = f"Error accessing file: {filename} - {str(e)}"
                        self.logger.warning(f"Error accessing file {filename}: {e}")
                else:
                    result_entry["fullFilePath"] = "No file information available"

                group_results.append(result_entry)

            if group_results:
                self.results.append(group_results)

        return self.results, self.files_not_found

    def save_results_and_summary(self):
        """Save results to file and print summary using TraceResultRepository."""
        # Save results using TraceResultRepository
        results_file = self.output_dir / "trace_analysis" / "trace_analysis_results.json"

        # Check if we need to save (only save if results were newly processed)
        if results_file.exists() and self.results:
            # Results were loaded from existing file, no need to save again
            self.logger.info(f"Results already exist at: {results_file}")
            self.logger.info(f"Using {len(self.results)} callstack groups from existing results")
        else:
            # Save newly processed results using TraceResultRepository
            try:
                from .trace_result_repository import TraceAnalysisResultRepository

                # Get the singleton instance
                trace_result_repository = TraceAnalysisResultRepository.get_instance()

                # Save the results using the repository
                success = trace_result_repository.save_trace_result(
                    output_file=str(results_file),
                    results_data=self.results,
                    metadata={
                        'callstack_groups': len(self.results) if self.results else 0,
                        'files_not_found': len(self.files_not_found) if hasattr(self, 'files_not_found') and self.files_not_found else 0,
                        'timestamp': time.time()
                    }
                )

                if success:
                    self.logger.info(f"Trace analysis results saved to: {results_file}")
                    self.logger.info(f"Processed {len(self.results)} callstack groups")
                else:
                    self.logger.error("Failed to save trace analysis results using TraceResultRepository")
                    return False

            except Exception as e:
                self.logger.error(f"Error saving results using TraceResultRepository: {e}")
                return False

        # Print summary of files not found (only if we processed callstacks)
        if hasattr(self, 'files_not_found') and self.files_not_found:
            self.logger.info(f"\nSummary: {len(self.files_not_found)} files were not found:")
            self.logger.info("  - " + ", ".join(sorted(set(self.files_not_found))))
        elif hasattr(self, 'files_not_found'):
            self.logger.info("All referenced files were found successfully!")

        self.logger.info("Trace analysis completed successfully!")
        return True

    def get_callstack_data_for_embedding(self, callstack):
        """
        Extract callstack data in a format suitable for embedding in analysis results.

        Args:
            callstack: JSON callstack data (list of callstack entries)

        Returns:
            dict: Structured callstack data for embedding
        """
        if not callstack or not isinstance(callstack, list):
            return None

        callstack_data = {
            'type': 'trace_callstack',
            'entries': [],
            'summary': {
                'total_functions': len(callstack),
                'libraries_involved': set(),
                'files_involved': set()
            }
        }

        for entry in callstack:
            if not isinstance(entry, dict):
                continue

            # Extract function information
            path = entry.get("path", "")
            owner_name = entry.get("ownerName", "")
            source_path = entry.get("sourcePath", "")
            cost = entry.get("cost", 0)
            normalized_cost = entry.get("normalizedCost", 0.0)

            # Add to summary
            if owner_name:
                callstack_data['summary']['libraries_involved'].add(owner_name)
            if source_path:
                filename = os.path.basename(source_path)
                callstack_data['summary']['files_involved'].add(filename)

            # Create entry
            entry_data = {
                'function_path': path,
                'owner_name': owner_name,
                'source_path': source_path,
                'cost': cost,
                'normalized_cost': normalized_cost
            }

            # Add filename if available
            if source_path:
                entry_data['filename'] = os.path.basename(source_path)

            callstack_data['entries'].append(entry_data)

        # Convert sets to lists for JSON serialization
        callstack_data['summary']['libraries_involved'] = list(callstack_data['summary']['libraries_involved'])
        callstack_data['summary']['files_involved'] = list(callstack_data['summary']['files_involved'])

        return callstack_data

    def set_analyzed_records_registry(self, registry):
        """Set the AnalyzedRecordsRegistry instance for tracking analyzed callstacks."""
        self.analyzed_records_registry = registry

    def set_num_traces_to_analyze(self, num_traces):
        """Set the number of traces to analyze (limits callstack processing)."""
        self.num_traces_to_analyze = num_traces

    def _convert_callstack_to_text_format(self, callstack):
        """
        Convert callstack to text format (one line per function) for registry storage.
        NOTE: This method PRESERVES costs for registry tracking purposes.

        Args:
            callstack: JSON callstack data (list of callstack entries)

        Returns:
            str: Callstack in text format, one function per line, with percentage information
        """
        if not callstack or not isinstance(callstack, list):
            return ""

        callstack_lines = []
        for entry in callstack:
            if not isinstance(entry, dict):
                continue

            # Extract function information (INCLUDING costs for registry tracking)
            path = entry.get("path", "")
            owner_name = entry.get("ownerName", "")
            source_path = entry.get("sourcePath", "")
            normalized_cost = entry.get("normalizedCost", 0.0)
            cost = entry.get("cost", 0)

            # Build the function line WITH percentage information (for registry tracking)
            # Format: "percentage% (raw_cost) library function_name (filename)"
            percentage_str = f"{normalized_cost:.0f}%" if normalized_cost > 0 else "0%"
            cost_str = f"({cost:.2f})" if cost > 0 else "(0.00)"
            
            # Use the original path as the function line (keep original format)
            function_name = path if path else owner_name
            
            # Build the line with percentage and cost information
            if owner_name:
                function_line = f"{percentage_str} {cost_str} {owner_name} {function_name}"
            else:
                function_line = f"{percentage_str} {cost_str} {function_name}"

            # Add filename information if available
            if source_path:
                filename = os.path.basename(source_path)
                function_line += f" ({filename})"

            callstack_lines.append(function_line)

        return "\n".join(callstack_lines)

    def create_context_for(self, callstack, prompt_filename=None):
        """
        Create context for LLM to analyze callstack.

        Args:
            callstack: JSON callstack data (list of callstack entries)
            prompt_filename: Optional filename for logging purposes

        Returns:
            tuple: (formatted_context_string, callstack_data_for_embedding)
        """
        try:
            # Start with callstack data only (no analysis instruction)
            # The analysis instruction will be added by the TracePromptBuilder template
            # NOTE: Cost and normalized cost information is excluded from LLM input
            # but preserved in callstack_data for user reports
            context = ""

            # Process callstack entries to build readable callstack
            if not callstack or not isinstance(callstack, list):
                self.logger.warning("Invalid or empty callstack provided")
                return context + "No valid callstack data provided\n====Use this additional context if needed===\n", None

            # Use cached function_lookup or load it if not available
            if self.function_lookup is None:
                self.function_lookup = self._load_function_lookup()

            # Build callstack line by line (one function per line) - WITHOUT costs for LLM
            callstack_lines = []
            relevant_function_contexts = []  # Track function contexts for line-specific content lookup
            relevant_files = set()  # Track files for content lookup

            for entry in callstack:
                if not isinstance(entry, dict):
                    continue

                # Extract function information (excluding costs for LLM)
                path = entry.get("path", "")
                owner_name = entry.get("ownerName", "")
                source_path = entry.get("sourcePath", "")

                # Build the function line WITHOUT cost/percentage information for LLM
                # Format: "library function_name (filename)" or just "function_name (filename)"
                function_name = path if path else owner_name
                
                # Build the line without cost information
                if owner_name and path and owner_name != path:
                    function_line = f"{owner_name} {function_name}"
                else:
                    function_line = function_name

                # Add filename information if available
                if source_path:
                    filename = os.path.basename(source_path)
                    function_line += f" ({filename})"

                callstack_lines.append(function_line)

                # Try to find function definition in merged_functions.json
                func_context = self._find_function_context(path, self.function_lookup) if self.function_lookup else None
                if func_context:
                    # Found specific function context - use line numbers
                    relevant_function_contexts.append({
                        'function_name': path,
                        'context': func_context
                    })
                else:
                    # Function not found in merged_functions.json - use entire file
                    if source_path:
                        filename = os.path.basename(source_path)
                        relevant_files.add(filename)

                # Look up caller information from invoked_by attribute
                caller_info = self._find_caller_information(path)
                if caller_info:
                    # Add caller context to relevant function contexts
                    for caller in caller_info:
                        caller_func_name = caller.get('function', '')
                        caller_context = self._find_function_context(caller_func_name, self.function_lookup) if self.function_lookup else None
                        if caller_context:
                            relevant_function_contexts.append({
                                'function_name': caller_func_name,
                                'context': caller_context,
                                'is_caller': True,
                                'calls_function': path
                            })

            # Add entire callstack to context (no filtering)
            if callstack_lines:
                context += "\n".join(callstack_lines)
            else:
                context += "No callstack entries found"

            # Add separator for additional context
            context += "\n\n====Use this additional context if needed===\n\n"

            # Load file content - either specific function ranges or entire files
            if relevant_function_contexts or relevant_files:
                try:
                    context += "\nFile content:\n"

                    # First, get content for functions with specific line ranges
                    for func_info in relevant_function_contexts:
                        func_name = func_info['function_name']
                        func_context = func_info['context']
                        is_caller = func_info.get('is_caller', False)
                        calls_function = func_info.get('calls_function', '')

                        try:
                            file_path = func_context.get('file')
                            start_line = func_context.get('start')
                            end_line = func_context.get('end')

                            if file_path and start_line is not None and end_line is not None:
                                filename = os.path.basename(file_path)
                                # Get specific line range content
                                line_range_content = self._get_file_line_range(filename, start_line, end_line)
                                if line_range_content:
                                    # Add caller information to the header if this is a caller function
                                    if is_caller:
                                        context += f"\n--- CALLER: {func_name} ({filename}:{start_line}-{end_line}) [calls {calls_function}] ---\n"
                                    else:
                                        context += f"\n--- {func_name} ({filename}:{start_line}-{end_line}) ---\n"
                                    context += line_range_content
                                    if is_caller:
                                        context += f"\n--- End of CALLER: {func_name} ---\n"
                                    else:
                                        context += f"\n--- End of {func_name} ---\n"
                                    self.logger.debug(f"Added line range content for {func_name}: lines {start_line}-{end_line}")
                                else:
                                    # Try to get more specific error information
                                    file_path = self.file_content_provider.guess_path(filename)
                                    if not file_path:
                                        caller_prefix = "CALLER: " if is_caller else ""
                                        context += f"\n--- {caller_prefix}{func_name} ({filename}:{start_line}-{end_line}) (file not found in repository) ---\n"
                                        self.logger.debug(f"File not found in repository: {filename}")
                                    else:
                                        caller_prefix = "CALLER: " if is_caller else ""
                                        context += f"\n--- {caller_prefix}{func_name} ({filename}:{start_line}-{end_line}) (could not extract line range) ---\n"
                                        self.logger.debug(f"Could not extract line range {start_line}-{end_line} from {filename}")
                            else:
                                caller_prefix = "CALLER: " if is_caller else ""
                                context += f"\n--- {caller_prefix}{func_name} (incomplete context info) ---\n"
                        except Exception as e:
                            caller_prefix = "CALLER: " if is_caller else ""
                            context += f"\n--- {caller_prefix}{func_name} (error reading: {str(e)}) ---\n"
                            self.logger.warning(f"Error reading function context for {func_name}: {e}")

                    # Add information for files that would have been included but are skipped to prevent token limit issues
                    for filename in sorted(relevant_files):
                        # Check if file exists in repository and get its information
                        try:
                            file_path = self.file_content_provider.guess_path(filename)
                            if file_path:
                                # File exists in repository - get size information
                                try:
                                    file_size = os.path.getsize(file_path)
                                    # Convert to relative path for display
                                    if self.repo_path:
                                        from pathlib import Path
                                        repo_path_obj = Path(self.repo_path).resolve()
                                        abs_path_obj = Path(file_path).resolve()
                                        try:
                                            rel_path = abs_path_obj.relative_to(repo_path_obj)
                                            display_path = str(rel_path)
                                        except ValueError:
                                            display_path = file_path
                                    else:
                                        display_path = file_path
                                    
                                    context += f"\n--- {filename} (file is at {display_path}, size: {file_size} bytes) ---\n"
                                    self.logger.debug(f"File {filename} found at {display_path}, size: {file_size} bytes")
                                except Exception as e:
                                    context += f"\n--- {filename} (file is at {file_path}, size: unknown) ---\n"
                                    self.logger.debug(f"File {filename} found at {file_path}, but could not get size: {e}")
                            else:
                                # File not found in repository
                                context += f"\n--- {filename} (file is not from this repository) ---\n"
                                self.logger.debug(f"File {filename} not found in repository")
                        except Exception as e:
                            # Error checking file - assume not from repository
                            context += f"\n--- {filename} (file is not from this repository) ---\n"
                            self.logger.debug(f"Error checking file {filename}: {e}")

                except Exception as e:
                    self.logger.warning(f"Failed to load file content: {e}")
                    context += f"\nNote: Could not load file content: {str(e)}\n"
            else:
                context += "\nNo relevant files found for content lookup.\n"

            log_message = f"Created context for callstack with {len(callstack_lines)} function calls, {len(relevant_function_contexts)} function contexts, and {len(relevant_files)} files"
            if prompt_filename:
                log_message += f" -> {prompt_filename}"
            self.logger.info(log_message)

            # Get callstack data for embedding
            callstack_data = self.get_callstack_data_for_embedding(callstack)

            return context, callstack_data

        except Exception as e:
            self.logger.error(f"Error creating context for callstack: {e}")
            error_context = f"Analyze this callstack\n======\nError processing callstack: {str(e)}\n====Use this additional context if needed===\n"
            return error_context, None

    def _normalize_function_name(self, func_name):
        """
        Normalize function name to handle mismatches like 'CLGnssProvider::stopLocation()' vs 'CLGnssProvider::stopLocation'
        """
        if not func_name:
            return func_name

        # Remove parentheses and everything after them (parameters)
        if '(' in func_name:
            func_name = func_name.split('(')[0]

        # Strip whitespace
        return func_name.strip()

    def _find_function_context(self, callstack_function_name, function_lookup):
        """
        Find function context in the lookup map, handling name variations.

        Args:
            callstack_function_name: Function name from callstack
            function_lookup: Dictionary mapping function names to context info

        Returns:
            dict: Function context with file, start, end or None
        """
        if not callstack_function_name or not function_lookup:
            return None

        # Try exact match first
        if callstack_function_name in function_lookup:
            return function_lookup[callstack_function_name]

        # Try normalized version
        normalized_name = self._normalize_function_name(callstack_function_name)
        if normalized_name in function_lookup:
            return function_lookup[normalized_name]

        # Try partial matching - look for functions that contain the normalized name
        for func_name, context in function_lookup.items():
            if normalized_name in func_name or func_name in normalized_name:
                return context

        return None

    def _get_file_line_range(self, filename, start_line, end_line):
        """
        Get specific line range from a file using FileContentProvider.

        Args:
            filename: Name of the file
            start_line: Starting line number (1-based)
            end_line: Ending line number (1-based)

        Returns:
            str: Content of the specified line range with line numbers, or None if error
        """
        try:
            # First resolve the filename to a full path
            file_path = self.file_content_provider.guess_path(filename)
            if not file_path:
                # Try alternative resolution method
                file_path = self.file_content_provider.resolve_file_path(filename)

            if not file_path:
                self.logger.debug(f"Could not resolve file path for: {filename}")
                # Try to get all candidates to help with debugging
                candidates = self.file_content_provider.all_candidates_for(filename)
                if candidates:
                    self.logger.debug(f"Available candidates for {filename}: {candidates[:3]}...")  # Show first 3
                else:
                    self.logger.debug(f"No candidates found for {filename} in file index")
                return None

            # Get full file content using the resolved path
            full_content = self.file_content_provider.read_text(file_path)
            if not full_content:
                self.logger.debug(f"Could not read content from resolved path: {file_path}")
                return None

            self.logger.debug(f"Successfully resolved {filename} to {file_path}")

            # Split into lines
            lines = full_content.split('\n')

            # Validate line numbers
            if start_line < 1 or end_line < 1 or start_line > len(lines) or end_line > len(lines):
                return None

            # Extract the specified range (convert to 0-based indexing)
            range_lines = lines[start_line-1:end_line]

            # Check if function is less than 300 lines - if so, include the full function body
            function_line_count = end_line - start_line + 1
            if function_line_count < 300:
                self.logger.debug(f"Function has {function_line_count} lines (< 300), including full function body")
                # Add line numbers
                numbered_lines = []
                for i, line in enumerate(range_lines):
                    line_num = start_line + i
                    numbered_lines.append(f"{line_num:4d} | {line}")

                # Apply comment pruning to the numbered content
                numbered_content = '\n'.join(numbered_lines)
                pruned_content = CodeContextPruner.prune_comments(numbered_content)

                return pruned_content
            else:
                # Function is >= 300 lines, apply more aggressive pruning or summarization
                self.logger.debug(f"Function has {function_line_count} lines (>= 300), applying selective extraction")
                # For now, still return the full function but log that it's large
                # In the future, this could be enhanced to extract only key parts
                numbered_lines = []
                for i, line in enumerate(range_lines):
                    line_num = start_line + i
                    numbered_lines.append(f"{line_num:4d} | {line}")

                # Apply comment pruning to the numbered content
                numbered_content = '\n'.join(numbered_lines)
                pruned_content = CodeContextPruner.prune_comments(numbered_content)

                return pruned_content

        except Exception as e:
            self.logger.warning(f"Error extracting line range {start_line}-{end_line} from {filename}: {e}")
            return None

    def _load_function_lookup(self):
        """
        Load and build function lookup map using centralized RepoAstIndex.

        Returns:
            dict: Function lookup map: function_name -> context_info
        """
        function_lookup = {}

        try:
            # Use centralized AST index to get merged functions data
            merged_functions_data = self.ast_index.merged_functions
            
            if merged_functions_data:
                # Handle new format: "function_to_location" wrapper
                if isinstance(merged_functions_data, dict) and 'function_to_location' in merged_functions_data:
                    function_data = merged_functions_data['function_to_location']
                    if not isinstance(function_data, dict):
                        self.logger.warning(f"function_to_location is not a dict, got {type(function_data).__name__}")
                        return function_lookup
                    
                    for func_name, locations in function_data.items():
                        if isinstance(locations, list) and locations:
                            # Take the first location if multiple exist
                            location = locations[0]
                            if isinstance(location, dict):
                                # Convert to the expected context format
                                context_info = {
                                    'file': location.get('file_name', ''),
                                    'start': location.get('start', 0),
                                    'end': location.get('end', 0)
                                }
                                # Store both original and normalized versions
                                function_lookup[func_name] = context_info
                                # Also store normalized version (remove parentheses and parameters)
                                normalized_name = self._normalize_function_name(func_name)
                                function_lookup[normalized_name] = context_info

                    self.logger.info(f"Loaded {len(function_lookup)} function definitions using RepoAstIndex (new format)")

                # Handle old format: direct list of symbols with name and context
                elif isinstance(merged_functions_data, list):
                    for symbol in merged_functions_data:
                        if isinstance(symbol, dict) and 'name' in symbol and 'context' in symbol:
                            func_name = symbol['name']
                            context = symbol['context']
                            if self._is_valid_context(context):
                                context_info = {
                                    'file': context.get('file', ''),
                                    'start': context.get('start', 0),
                                    'end': context.get('end', 0)
                                }
                                # Store both original and normalized versions
                                function_lookup[func_name] = context_info
                                normalized_name = self._normalize_function_name(func_name)
                                function_lookup[normalized_name] = context_info
                    
                    self.logger.info(f"Loaded {len(function_lookup)} function definitions using RepoAstIndex (old list format)")
                
                else:
                    self.logger.warning(f"merged_functions.json has unexpected format - expected dict with 'function_to_location' or list, got {type(merged_functions_data).__name__}")
                    if isinstance(merged_functions_data, dict):
                        self.logger.warning(f"Available keys: {list(merged_functions_data.keys())}")
            else:
                self.logger.debug("No merged functions data available from RepoAstIndex")
                
        except Exception as e:
            self.logger.warning(f"Failed to load function lookup using RepoAstIndex: {e}")

        return function_lookup

    def _find_caller_information(self, function_name):
        """
        Find caller information for a function using the invoked_by attribute from the call graph.
        
        Args:
            function_name: Name of the function to find callers for
            
        Returns:
            list: List of caller information dictionaries, or None if not found
        """
        try:
            # Use centralized AST index to get merged call graph data
            merged_call_graph_data = self.ast_index.merged_call_graph
            
            if not merged_call_graph_data:
                return None
                
            # Handle the call graph structure: {"call_graph": [file_entries]}
            if isinstance(merged_call_graph_data, dict) and 'call_graph' in merged_call_graph_data:
                call_graph = merged_call_graph_data['call_graph']
                
                # Search through all files and functions for the target function
                for file_entry in call_graph:
                    if not isinstance(file_entry, dict) or 'functions' not in file_entry:
                        continue
                        
                    functions = file_entry.get('functions', [])
                    for func_entry in functions:
                        if not isinstance(func_entry, dict):
                            continue
                            
                        # Check if this is the function we're looking for
                        func_name = func_entry.get('function', '')
                        if func_name == function_name:
                            # Found the function, return its invoked_by information
                            invoked_by = func_entry.get('invoked_by', [])
                            if invoked_by:
                                self.logger.debug(f"Found {len(invoked_by)} callers for function {function_name}")
                                return invoked_by
                            else:
                                self.logger.debug(f"Function {function_name} has no callers")
                                return []
                                
            self.logger.debug(f"Function {function_name} not found in call graph")
            return None
            
        except Exception as e:
            self.logger.warning(f"Error finding caller information for {function_name}: {e}")
            return None

    def generate_prompt_files(self):
        """Generate 1000 random prompt files in /tmp/<repo_name>/prompts/ directory."""
        try:
            # Load processed hotspot data
            self.logger.info("Loading processed hotspot data for prompt generation...")
            processed_data = read_json_file(str(self.processed_hotspot_file))

            if not processed_data:
                self.logger.error("Failed to load processed hotspot data for prompt generation")
                return False

            self.logger.info(f"Loaded {len(processed_data)} callstack groups for prompt generation")

            # Create prompts directory using the configured output directory
            prompts_dir = self.output_dir / "prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Created prompts directory: {prompts_dir}")

            # Determine how many prompts to generate (up to 1000 or total available)
            num_prompts = min(1000, len(processed_data))
            self.logger.info(f"Generating {num_prompts} prompt files...")

            # Randomly select callstack groups
            selected_indices = random.sample(range(len(processed_data)), num_prompts)

            # Load function lookup once and reuse it for all prompts
            self.logger.info("Loading function lookup data once for reuse...")
            if self.function_lookup is None:
                self.function_lookup = self._load_function_lookup()
            self.logger.info(f"Function lookup loaded with {len(self.function_lookup)} entries")

            # Generate prompt files
            successful_prompts = 0
            failed_prompts = 0

            for i, callstack_index in enumerate(selected_indices):
                try:
                    callstack = processed_data[callstack_index]

                    # Create filename with zero-padded index
                    prompt_filename = f"prompt_{i+1:04d}.txt"
                    prompt_filepath = prompts_dir / prompt_filename

                    # Generate context using our enhanced function
                    context, callstack_data = self.create_context_for(
                        callstack=callstack,
                        prompt_filename=prompt_filename
                    )

                    # Write context to file
                    with open(prompt_filepath, 'w', encoding='utf-8') as f:
                        f.write(context)

                    # Optionally save callstack data alongside prompt (for debugging/reference)
                    if callstack_data:
                        callstack_filepath = prompts_dir / f"callstack_{i+1:04d}.json"
                        with open(callstack_filepath, 'w', encoding='utf-8') as f:
                            json.dump(callstack_data, f, indent=2)

                    successful_prompts += 1

                    # Log progress every 100 prompts
                    if (i + 1) % 100 == 0:
                        self.logger.info(f"Generated {i + 1}/{num_prompts} prompt files...")

                except Exception as e:
                    self.logger.warning(f"Failed to generate prompt {i+1}: {e}")
                    failed_prompts += 1
                    continue

            # Log final results
            self.logger.info(f"Prompt generation completed!")
            self.logger.info(f"  Successfully generated: {successful_prompts} prompts")
            self.logger.info(f"  Failed: {failed_prompts} prompts")
            self.logger.info(f"  Output directory: {prompts_dir}")

            # Calculate and log some statistics
            if successful_prompts > 0:
                # Get file sizes for statistics
                total_size = 0
                file_sizes = []

                for prompt_file in prompts_dir.glob("prompt_*.txt"):
                    try:
                        size = prompt_file.stat().st_size
                        total_size += size
                        file_sizes.append(size)
                    except Exception:
                        continue

                if file_sizes:
                    avg_size = total_size / len(file_sizes)
                    min_size = min(file_sizes)
                    max_size = max(file_sizes)

                    self.logger.info(f"  Total size: {total_size / 1024 / 1024:.2f} MB")
                    self.logger.info(f"  Average file size: {avg_size / 1024:.2f} KB")
                    self.logger.info(f"  Size range: {min_size / 1024:.2f} KB - {max_size / 1024:.2f} KB")

            return successful_prompts > 0

        except Exception as e:
            self.logger.error(f"Error during prompt file generation: {e}")
            traceback.print_exc()
            return False

    def generate_prompts(self, config_path, hotspot_path, prompts_dir, num_prompts=1000, batch_index=0, dry_run=False):
        """
        Generate prompts for trace analysis.

        Args:
            config_path: Path to configuration file
            hotspot_path: Path to hotspot JSON file
            prompts_dir: Directory where prompts will be generated
            num_prompts: Number of prompts to generate (default: 1000)
            batch_index: Batch index for selecting traces by normalized cost (0=top aggressors, 1=next batch, etc.)
            dry_run: If True, only dump selected traces to file without generating prompts

        Returns:
            int: Exit code (0 for success, 1 for failure)
        """
        try:
            # Load and validate configuration - repo_path should be set by the caller
            if not hasattr(self, 'repo_path') or not self.repo_path:
                raise ValueError("Repository path must be set before calling generate_prompts")

            if not self.load_and_validate_configuration(config_path, hotspot_path, str(self.repo_path)):
                return 1

            # Setup output directory
            self.setup_output_directory()

            # Log what files can be reused vs need to be regenerated
            self.log_reuse_summary()

            # Process hotspot data
            self.process_hotspot_data()

            # Setup AST files (check if they exist)
            if not self.setup_ast_files():
                return 1

            # Setup FileContentProvider
            self.setup_file_content_provider()

            # Process callstacks
            results, _files_not_found = self.process_callstacks()
            if results is None:
                return 1

            # Save results and print summary
            success = self.save_results_and_summary()
            if not success:
                return 1

            # Generate prompt files in the specified directory or dump traces for dry run
            if dry_run:
                success = self.dump_selected_traces_to_file(num_prompts, batch_index)
            else:
                success = self.generate_prompt_files_to_directory(prompts_dir, num_prompts, batch_index)
            if not success:
                return 1

            return 0

        except Exception as e:
            self.logger.error(f"Error during trace analysis: {e}")
            traceback.print_exc()
            return 1

    def generate_prompt_files_to_directory(self, prompts_dir, num_prompts=1000, batch_index=0):
        """Generate prompt files in the specified directory, filtering out already analyzed traces."""
        try:
            # Load processed hotspot data
            self.logger.info("Loading processed hotspot data for prompt generation...")
            processed_data = read_json_file(str(self.processed_hotspot_file))

            if not processed_data:
                self.logger.error("Failed to load processed hotspot data for prompt generation")
                return False

            self.logger.info(f"Loaded {len(processed_data)} callstack groups for prompt generation")

            # Use the provided prompts_dir parameter instead of creating our own path
            prompts_path = Path(prompts_dir)

            # Always delete existing prompts directory if it exists, then create it fresh
            if prompts_path.exists():
                self.logger.info(f"Deleting existing prompts directory: {prompts_path}")
                shutil.rmtree(prompts_path)

            prompts_path.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Created prompts directory: {prompts_path}")

            # Load function lookup once and reuse it for all prompts
            self.logger.info("Loading function lookup data once for reuse...")
            if self.function_lookup is None:
                self.function_lookup = self._load_function_lookup()
            self.logger.info(f"Function lookup loaded with {len(self.function_lookup)} entries")

            # Find unanalyzed callstacks by checking the registry
            unanalyzed_indices = []
            analyzed_count = 0

            self.logger.info("Filtering out already analyzed callstacks...")
            for i, callstack in enumerate(processed_data):
                # Convert callstack to text format for registry check
                callstack_text = self._convert_callstack_to_text_format(callstack)

                # Check if this callstack has already been analyzed
                if self.analyzed_records_registry and callstack_text and self.analyzed_records_registry.is_analyzed(callstack_text):
                    analyzed_count += 1
                    continue

                unanalyzed_indices.append(i)

            self.logger.info(f"Found {analyzed_count} already analyzed callstacks, {len(unanalyzed_indices)} unanalyzed callstacks")

            if not unanalyzed_indices:
                self.logger.warning("No unanalyzed callstacks found - all traces have already been processed")
                return False

            # Select from unanalyzed callstacks
            actual_num_prompts = min(num_prompts, len(unanalyzed_indices))

            if batch_index >= 0:
                self.logger.info(f"Selecting {actual_num_prompts} unanalyzed callstacks using batch index {batch_index} by normalized cost...")
                # Create a subset of processed_data with only unanalyzed callstacks
                unanalyzed_data = [processed_data[i] for i in unanalyzed_indices]
                # Select batch from unanalyzed data
                selected_batch_indices = self._select_batch_by_normalized_cost(unanalyzed_data, actual_num_prompts, batch_index)
                # Map back to original indices
                selected_indices = [unanalyzed_indices[i] for i in selected_batch_indices]
            else:
                self.logger.info(f"Selecting {actual_num_prompts} unanalyzed callstacks using random selection...")
                # Randomly select from unanalyzed indices
                selected_indices = random.sample(unanalyzed_indices, actual_num_prompts)

            # Generate prompt files
            successful_prompts = 0
            failed_prompts = 0

            for i, callstack_index in enumerate(selected_indices):
                try:
                    callstack = processed_data[callstack_index]

                    # Create filename with zero-padded index
                    prompt_filename = f"prompt_{i+1:04d}.txt"
                    prompt_filepath = prompts_path / prompt_filename

                    # Generate context using our enhanced function
                    context, callstack_data = self.create_context_for(
                        callstack=callstack,
                        prompt_filename=prompt_filename
                    )

                    # Write context to file
                    with open(prompt_filepath, 'w', encoding='utf-8') as f:
                        f.write(context)

                    # Optionally save callstack data alongside prompt (for debugging/reference)
                    if callstack_data:
                        callstack_filepath = prompts_path / f"callstack_{i+1:04d}.json"
                        with open(callstack_filepath, 'w', encoding='utf-8') as f:
                            json.dump(callstack_data, f, indent=2)

                    successful_prompts += 1

                    # Log progress every 100 prompts
                    if (i + 1) % 100 == 0:
                        self.logger.info(f"Generated {i + 1}/{actual_num_prompts} prompt files...")

                except Exception as e:
                    self.logger.warning(f"Failed to generate prompt {i+1}: {e}")
                    failed_prompts += 1
                    continue

            # Log final results
            self.logger.info(f"Prompt generation completed!")
            self.logger.info(f"  Successfully generated: {successful_prompts} prompts")
            self.logger.info(f"  Skipped (already analyzed): {analyzed_count} prompts")
            self.logger.info(f"  Failed: {failed_prompts} prompts")
            self.logger.info(f"  Output directory: {prompts_path}")

            # Calculate and log some statistics
            if successful_prompts > 0:
                # Get file sizes for statistics
                total_size = 0
                file_sizes = []

                for prompt_file in prompts_path.glob("prompt_*.txt"):
                    try:
                        size = prompt_file.stat().st_size
                        total_size += size
                        file_sizes.append(size)
                    except Exception:
                        continue

                if file_sizes:
                    avg_size = total_size / len(file_sizes)
                    min_size = min(file_sizes)
                    max_size = max(file_sizes)

                    self.logger.info(f"  Total size: {total_size / 1024 / 1024:.2f} MB")
                    self.logger.info(f"  Average file size: {avg_size / 1024:.2f} KB")
                    self.logger.info(f"  Size range: {min_size / 1024:.2f} KB - {max_size / 1024:.2f} KB")

            return successful_prompts > 0

        except Exception as e:
            self.logger.error(f"Error during prompt file generation: {e}")
            traceback.print_exc()
            return False

    def _select_batch_by_normalized_cost(self, processed_data, num_prompts, batch_index):
        """
        Select a batch of callstack groups by maximum normalized cost.

        Args:
            processed_data: List of callstack groups (each group is a list of callstack entries)
            num_prompts: Number of traces to select per batch
            batch_index: Batch index (0=top aggressors, 1=next batch, etc.)

        Returns:
            list: Indices of selected callstack groups for the specified batch
        """
        try:
            # Calculate maximum normalized cost for each callstack group
            group_costs = []
            for i, callstack_group in enumerate(processed_data):
                max_normalized_cost = 0.0

                # Find the maximum normalized cost in this callstack group
                for entry in callstack_group:
                    if isinstance(entry, dict):
                        normalized_cost = entry.get("normalizedCost", 0.0)
                        if isinstance(normalized_cost, (int, float)):
                            max_normalized_cost = max(max_normalized_cost, float(normalized_cost))

                group_costs.append((i, max_normalized_cost))

            # Sort by normalized cost in descending order (highest cost first)
            group_costs.sort(key=lambda x: x[1], reverse=True)

            # Calculate batch start and end indices
            start_idx = batch_index * num_prompts
            end_idx = start_idx + num_prompts

            # Select the batch
            batch_groups = group_costs[start_idx:end_idx]
            selected_indices = [index for index, cost in batch_groups]

            # Log some statistics about the selection
            if batch_groups:
                top_cost = batch_groups[0][1]
                bottom_cost = batch_groups[-1][1]
                self.logger.info(f"Selected batch {batch_index} with {len(selected_indices)} traces by normalized cost")
                self.logger.info(f"Batch range: indices {start_idx}-{end_idx-1}, normalized cost range: {bottom_cost:.3f}% - {top_cost:.3f}%")
            elif group_costs:
                self.logger.warning(f"Batch {batch_index} is out of range. Total available batches: {(len(group_costs) + num_prompts - 1) // num_prompts}")
                # Fallback to last available batch
                last_batch_start = ((len(group_costs) - 1) // num_prompts) * num_prompts
                batch_groups = group_costs[last_batch_start:last_batch_start + num_prompts]
                selected_indices = [index for index, cost in batch_groups]
                self.logger.info(f"Using last available batch with {len(selected_indices)} traces")

            return selected_indices

        except Exception as e:
            self.logger.error(f"Error selecting top aggressors by normalized cost: {e}")
            # Fallback to random selection
            return random.sample(range(len(processed_data)), min(num_prompts, len(processed_data)))

    def dump_selected_traces_to_file(self, num_prompts=1000, batch_index=0):
        """
        Dump selected traces to /tmp/<repo_name>/traces_to_be_analyzed.txt for dry run.

        Args:
            num_prompts: Number of traces to select
            batch_index: Batch index for selecting traces by normalized cost (0=top aggressors, 1=next batch, etc.)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Load processed hotspot data
            self.logger.info("Loading processed hotspot data for trace dump...")
            processed_data = read_json_file(str(self.processed_hotspot_file))

            if not processed_data:
                self.logger.error("Failed to load processed hotspot data for trace dump")
                return False

            self.logger.info(f"Loaded {len(processed_data)} callstack groups for trace dump")

            # Determine how many traces to select
            actual_num_prompts = min(num_prompts, len(processed_data))

            if batch_index >= 0:
                self.logger.info(f"Selecting {actual_num_prompts} traces using batch index {batch_index} by normalized cost...")
                selected_indices = self._select_batch_by_normalized_cost(processed_data, actual_num_prompts, batch_index)
            else:
                self.logger.info(f"Selecting {actual_num_prompts} traces using random selection...")
                selected_indices = random.sample(range(len(processed_data)), actual_num_prompts)

            # Create output file path under trace_analysis
            output_provider = get_output_directory_provider()
            artifacts_dir = output_provider.get_repo_artifacts_dir()
            output_file = Path(f"{artifacts_dir}/trace_analysis/traces_to_be_analyzed.txt")
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # Write selected traces to file
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(f"SELECTED TRACES FOR ANALYSIS\n")
                f.write(f"{'=' * 50}\n")
                f.write(f"Repository: {self.repo_path}\n")
                f.write(f"Selection method: {'Batch ' + str(batch_index) + ' by normalized cost' if batch_index >= 0 else 'Random selection'}\n")
                f.write(f"Number of traces selected: {len(selected_indices)}\n")
                f.write(f"{'=' * 50}\n\n")

                for i, callstack_index in enumerate(selected_indices):
                    callstack = processed_data[callstack_index]

                    f.write(f"TRACE {i+1}/{len(selected_indices)}\n")
                    f.write(f"{'-' * 30}\n")

                    # Calculate max normalized cost for this callstack
                    max_normalized_cost = 0.0
                    for entry in callstack:
                        if isinstance(entry, dict):
                            normalized_cost = entry.get("normalizedCost", 0.0)
                            if isinstance(normalized_cost, (int, float)):
                                max_normalized_cost = max(max_normalized_cost, float(normalized_cost))

                    f.write(f"Max Normalized Cost: {max_normalized_cost:.3f}%\n")

                    # Write each callstack entry - simple format: frame name followed by normalized cost
                    for entry in callstack:
                        if isinstance(entry, dict):
                            path = entry.get("path", "")
                            owner_name = entry.get("ownerName", "")
                            source_path = entry.get("sourcePath", "")
                            normalized_cost = entry.get("normalizedCost", 0.0)

                            # Format the entry - just function name and normalized cost
                            function_line = path if path else owner_name
                            if source_path:
                                filename = os.path.basename(source_path)
                                function_line += f" ({filename})"

                            f.write(f"{function_line} - {normalized_cost:.3f}%\n")

                    f.write(f"\n\n")  # Two newlines to separate callstacks

            self.logger.info(f"Selected traces dumped to: {output_file}")
            self.logger.info(f"Dumped {len(selected_indices)} traces for analysis")

            return True

        except Exception as e:
            self.logger.error(f"Error during trace dump: {e}")
            traceback.print_exc()
            return False

    # File summary functionality removed - ProjectSummaryGenerator no longer used

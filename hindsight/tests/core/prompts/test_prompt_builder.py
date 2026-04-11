"""
Unit tests for PromptBuilder class in hindsight/core/prompts/prompt_builder.py

Tests context building functionality including:
- File path extraction from JSON
- Summary context building
- System prompt building
- User prompt building
- JSON to comment format conversion
- Function and data type lookups
"""

import json
import sys
import pytest
from unittest.mock import patch, Mock, MagicMock

# Mock the problematic imports before importing the module
sys.modules['hindsight.core.llm.llm'] = MagicMock()
sys.modules['hindsight.core.llm.code_analysis'] = MagicMock()
sys.modules['hindsight.core.llm'] = MagicMock()

# Now import the module - need to import directly to avoid circular import
import importlib.util
import os

# Get the path to prompt_builder.py
prompt_builder_path = os.path.join(
    os.path.dirname(__file__),
    '..', '..', '..', 'core', 'prompts', 'prompt_builder.py'
)
prompt_builder_path = os.path.abspath(prompt_builder_path)

# Load the module directly
spec = importlib.util.spec_from_file_location("prompt_builder", prompt_builder_path)
prompt_builder_module = importlib.util.module_from_spec(spec)

# Mock dependencies before executing the module
with patch.dict('sys.modules', {
    'hindsight.core.llm.llm': MagicMock(),
    'hindsight.core.llm': MagicMock(),
}):
    try:
        spec.loader.exec_module(prompt_builder_module)
    except ImportError:
        # If still failing, create minimal mocks
        pass

# Try to import normally with mocks in place
try:
    from hindsight.core.prompts.prompt_builder import PromptBuilder, _read_package_file
except ImportError:
    # Fallback: use the directly loaded module
    PromptBuilder = prompt_builder_module.PromptBuilder
    _read_package_file = prompt_builder_module._read_package_file


class TestReadPackageFile:
    """Tests for _read_package_file function."""

    def test_read_existing_package_file(self):
        """Test reading an existing package file."""
        # systemPrompt.md should exist in the package
        content = _read_package_file("systemPrompt.md")
        assert content is not None
        assert isinstance(content, str)
        assert len(content) > 0

    def test_read_nonexistent_package_file(self):
        """Test reading a non-existent package file returns None."""
        content = _read_package_file("nonexistent_file.md")
        assert content is None

    def test_read_output_schema_file(self):
        """Test reading the output schema JSON file."""
        content = _read_package_file("outputSchema.json")
        assert content is not None
        # Verify it's valid JSON
        data = json.loads(content)
        assert isinstance(data, list)


class TestLoadFileSummary:
    """Tests for _load_file_summary method."""

    def test_load_file_summary_returns_none(self):
        """Test that _load_file_summary returns None (functionality removed)."""
        result = PromptBuilder._load_file_summary()
        assert result is None


class TestLoadDirectorySummary:
    """Tests for _load_directory_summary method."""

    def test_load_directory_summary_returns_none(self):
        """Test that _load_directory_summary returns None (functionality removed)."""
        result = PromptBuilder._load_directory_summary("/some/path/file.py")
        assert result is None


class TestLoadProjectSummary:
    """Tests for _load_project_summary method."""

    def test_load_project_summary_returns_none(self):
        """Test that _load_project_summary returns None (functionality removed)."""
        result = PromptBuilder._load_project_summary()
        assert result is None


class TestExtractFilePathFromJson:
    """Tests for _extract_file_path_from_json method."""

    def test_extract_file_path_direct_key(self):
        """Test extracting file path from direct 'file' key."""
        json_content = json.dumps({"file": "src/main.c", "function": "main"})
        result = PromptBuilder._extract_file_path_from_json(json_content)
        assert result == "src/main.c"

    def test_extract_file_path_from_filepath_key(self):
        """Test extracting file path from 'filePath' key."""
        json_content = json.dumps({"filePath": "src/utils.py", "function": "helper"})
        result = PromptBuilder._extract_file_path_from_json(json_content)
        assert result == "src/utils.py"

    def test_extract_file_path_from_context(self):
        """Test extracting file path from nested 'context' object."""
        json_content = json.dumps({
            "function": "process",
            "context": {"file": "src/processor.c", "start": 10, "end": 50}
        })
        result = PromptBuilder._extract_file_path_from_json(json_content)
        assert result == "src/processor.c"

    def test_extract_file_path_from_function_context(self):
        """Test extracting file path from 'function_context' object."""
        json_content = json.dumps({
            "function": "validate",
            "function_context": {"file": "src/validator.py", "start": 1, "end": 20}
        })
        result = PromptBuilder._extract_file_path_from_json(json_content)
        assert result == "src/validator.py"

    def test_extract_file_path_from_file_context(self):
        """Test extracting file path from 'fileContext' object."""
        json_content = json.dumps({
            "function": "render",
            "fileContext": {"file": "src/renderer.js", "start": 5, "end": 100}
        })
        result = PromptBuilder._extract_file_path_from_json(json_content)
        assert result == "src/renderer.js"

    def test_extract_file_path_invalid_json(self):
        """Test extracting file path from invalid JSON returns None."""
        result = PromptBuilder._extract_file_path_from_json("not valid json")
        assert result is None

    def test_extract_file_path_empty_json(self):
        """Test extracting file path from empty JSON object returns None."""
        json_content = json.dumps({})
        result = PromptBuilder._extract_file_path_from_json(json_content)
        assert result is None

    def test_extract_file_path_null_value(self):
        """Test extracting file path when value is null returns None."""
        json_content = json.dumps({"file": None})
        result = PromptBuilder._extract_file_path_from_json(json_content)
        assert result is None


class TestExtractInvokingFilePaths:
    """Tests for _extract_invoking_file_paths method."""

    def test_extract_invoking_file_paths_with_function_context(self):
        """Test extracting file paths from function_context."""
        json_content = json.dumps({
            "function": "main",
            "function_context": {"file": "src/main.c"},
            "functions_invoked": ["helper", "process"]
        })
        result = PromptBuilder._extract_invoking_file_paths(json_content)
        assert "src/main.c" in result

    def test_extract_invoking_file_paths_nested(self):
        """Test extracting file paths from nested structures."""
        json_content = json.dumps({
            "function": "main",
            "nested": {
                "function_context": {"file": "src/nested.c"}
            }
        })
        result = PromptBuilder._extract_invoking_file_paths(json_content)
        assert "src/nested.c" in result

    def test_extract_invoking_file_paths_invalid_json(self):
        """Test extracting file paths from invalid JSON returns empty list."""
        result = PromptBuilder._extract_invoking_file_paths("not valid json")
        assert result == []

    def test_extract_invoking_file_paths_empty_json(self):
        """Test extracting file paths from empty JSON returns empty list."""
        json_content = json.dumps({})
        result = PromptBuilder._extract_invoking_file_paths(json_content)
        assert result == []


class TestBuildSummaryContext:
    """Tests for _build_summary_context method."""

    def test_build_summary_context_empty(self):
        """Test building summary context with no inputs returns empty string."""
        result = PromptBuilder._build_summary_context()
        assert result == ""

    def test_build_summary_context_with_file_summary(self):
        """Test building summary context with file summary."""
        result = PromptBuilder._build_summary_context(file_summary="This is a file summary")
        assert "## CONTEXTUAL SUMMARIES" in result
        assert "### File Summary" in result
        assert "This is a file summary" in result

    def test_build_summary_context_with_dir_summary(self):
        """Test building summary context with directory summary."""
        result = PromptBuilder._build_summary_context(dir_summary="This is a directory summary")
        assert "## CONTEXTUAL SUMMARIES" in result
        assert "### Primary Directory Summary" in result
        assert "This is a directory summary" in result

    def test_build_summary_context_with_invoking_dir_summaries(self):
        """Test building summary context with invoking directory summaries."""
        invoking_summaries = {
            "utils": "Utility functions",
            "helpers": "Helper functions"
        }
        result = PromptBuilder._build_summary_context(invoking_dir_summaries=invoking_summaries)
        assert "## CONTEXTUAL SUMMARIES" in result
        assert "### Related Directory Summaries" in result
        assert "#### Directory: utils" in result
        assert "Utility functions" in result
        assert "#### Directory: helpers" in result
        assert "Helper functions" in result

    def test_build_summary_context_all_inputs(self):
        """Test building summary context with all inputs."""
        result = PromptBuilder._build_summary_context(
            file_summary="File summary",
            dir_summary="Dir summary",
            invoking_dir_summaries={"other": "Other summary"}
        )
        assert "### File Summary" in result
        assert "### Primary Directory Summary" in result
        assert "### Related Directory Summaries" in result
        assert "Use this contextual information" in result


class TestDetermineAnalysisType:
    """Tests for determine_analysis_type method."""

    def test_determine_analysis_type_entire_file(self):
        """Test determining analysis type for entire file."""
        json_content = json.dumps({
            "fileContext": {"file": "src/main.c", "content": "..."}
        })
        result = PromptBuilder.determine_analysis_type(json_content)
        assert result == "entire_file"

    def test_determine_analysis_type_specific_function(self):
        """Test determining analysis type for specific function."""
        json_content = json.dumps({
            "function": "main",
            "file": "src/main.c"
        })
        result = PromptBuilder.determine_analysis_type(json_content)
        assert result == "specific_function"

    def test_determine_analysis_type_with_both(self):
        """Test determining analysis type when both fileContext and function present."""
        json_content = json.dumps({
            "fileContext": {"file": "src/main.c"},
            "function": "main"
        })
        result = PromptBuilder.determine_analysis_type(json_content)
        assert result == "specific_function"

    def test_determine_analysis_type_invalid_json(self):
        """Test determining analysis type for invalid JSON defaults to specific_function."""
        result = PromptBuilder.determine_analysis_type("not valid json")
        assert result == "specific_function"


class TestNormalizeFunctionName:
    """Tests for _normalize_function_name method."""

    def test_normalize_function_name_with_parentheses(self):
        """Test normalizing function name with parentheses."""
        result = PromptBuilder._normalize_function_name("myFunction()")
        assert result == "myFunction"

    def test_normalize_function_name_with_parameters(self):
        """Test normalizing function name with parameters."""
        result = PromptBuilder._normalize_function_name("myFunction(int x, int y)")
        assert result == "myFunction"

    def test_normalize_function_name_class_method(self):
        """Test normalizing class method name."""
        result = PromptBuilder._normalize_function_name("CLGnssProvider::stopLocation()")
        assert result == "CLGnssProvider::stopLocation"

    def test_normalize_function_name_simple(self):
        """Test normalizing simple function name without parentheses."""
        result = PromptBuilder._normalize_function_name("simpleFunction")
        assert result == "simpleFunction"

    def test_normalize_function_name_with_whitespace(self):
        """Test normalizing function name with whitespace."""
        result = PromptBuilder._normalize_function_name("  myFunction()  ")
        assert result == "myFunction"

    def test_normalize_function_name_empty(self):
        """Test normalizing empty function name."""
        result = PromptBuilder._normalize_function_name("")
        assert result == ""

    def test_normalize_function_name_none(self):
        """Test normalizing None function name."""
        result = PromptBuilder._normalize_function_name(None)
        assert result is None


class TestNormalizeDataTypeName:
    """Tests for _normalize_data_type_name method."""

    def test_normalize_data_type_name_with_template(self):
        """Test normalizing data type name with template parameters."""
        result = PromptBuilder._normalize_data_type_name("vector<int>")
        assert result == "vector"

    def test_normalize_data_type_name_nested_template(self):
        """Test normalizing data type name with nested template parameters."""
        result = PromptBuilder._normalize_data_type_name("map<string, vector<int>>")
        assert result == "map"

    def test_normalize_data_type_name_simple(self):
        """Test normalizing simple data type name."""
        result = PromptBuilder._normalize_data_type_name("MyClass")
        assert result == "MyClass"

    def test_normalize_data_type_name_with_whitespace(self):
        """Test normalizing data type name with whitespace."""
        result = PromptBuilder._normalize_data_type_name("  MyClass  ")
        assert result == "MyClass"

    def test_normalize_data_type_name_empty(self):
        """Test normalizing empty data type name."""
        result = PromptBuilder._normalize_data_type_name("")
        assert result == ""

    def test_normalize_data_type_name_none(self):
        """Test normalizing None data type name."""
        result = PromptBuilder._normalize_data_type_name(None)
        assert result is None


class TestFindFunctionContextInLookup:
    """Tests for _find_function_context_in_lookup method."""

    def test_find_function_context_exact_match(self):
        """Test finding function context with exact match."""
        lookup = {
            "myFunction": {"file": "src/main.c", "start": 10, "end": 20}
        }
        result = PromptBuilder._find_function_context_in_lookup("myFunction", lookup)
        assert result == {"file": "src/main.c", "start": 10, "end": 20}

    def test_find_function_context_normalized_match(self):
        """Test finding function context with normalized match."""
        lookup = {
            "myFunction": {"file": "src/main.c", "start": 10, "end": 20}
        }
        result = PromptBuilder._find_function_context_in_lookup("myFunction()", lookup)
        assert result == {"file": "src/main.c", "start": 10, "end": 20}

    def test_find_function_context_partial_match(self):
        """Test finding function context with partial match."""
        lookup = {
            "MyClass::myFunction": {"file": "src/main.cpp", "start": 10, "end": 20}
        }
        result = PromptBuilder._find_function_context_in_lookup("myFunction", lookup)
        assert result == {"file": "src/main.cpp", "start": 10, "end": 20}

    def test_find_function_context_not_found(self):
        """Test finding function context when not found."""
        lookup = {
            "otherFunction": {"file": "src/main.c", "start": 10, "end": 20}
        }
        result = PromptBuilder._find_function_context_in_lookup("myFunction", lookup)
        assert result is None

    def test_find_function_context_empty_lookup(self):
        """Test finding function context with empty lookup."""
        result = PromptBuilder._find_function_context_in_lookup("myFunction", {})
        assert result is None

    def test_find_function_context_none_inputs(self):
        """Test finding function context with None inputs."""
        result = PromptBuilder._find_function_context_in_lookup(None, None)
        assert result is None


class TestFindDataTypeContextInLookup:
    """Tests for _find_data_type_context_in_lookup method."""

    def test_find_data_type_context_exact_match(self):
        """Test finding data type context with exact match."""
        lookup = {
            "MyStruct": {"file": "src/types.h", "start": 5, "end": 15}
        }
        result = PromptBuilder._find_data_type_context_in_lookup("MyStruct", lookup)
        assert result == {"file": "src/types.h", "start": 5, "end": 15}

    def test_find_data_type_context_normalized_match(self):
        """Test finding data type context with normalized match."""
        lookup = {
            "vector": {"file": "src/types.h", "start": 5, "end": 15}
        }
        result = PromptBuilder._find_data_type_context_in_lookup("vector<int>", lookup)
        assert result == {"file": "src/types.h", "start": 5, "end": 15}

    def test_find_data_type_context_partial_match(self):
        """Test finding data type context with partial match."""
        lookup = {
            "MyNamespace::MyStruct": {"file": "src/types.h", "start": 5, "end": 15}
        }
        result = PromptBuilder._find_data_type_context_in_lookup("MyStruct", lookup)
        assert result == {"file": "src/types.h", "start": 5, "end": 15}

    def test_find_data_type_context_not_found(self):
        """Test finding data type context when not found."""
        lookup = {
            "OtherStruct": {"file": "src/types.h", "start": 5, "end": 15}
        }
        result = PromptBuilder._find_data_type_context_in_lookup("MyStruct", lookup)
        assert result is None

    def test_find_data_type_context_empty_lookup(self):
        """Test finding data type context with empty lookup."""
        result = PromptBuilder._find_data_type_context_in_lookup("MyStruct", {})
        assert result is None

    def test_find_data_type_context_none_inputs(self):
        """Test finding data type context with None inputs."""
        result = PromptBuilder._find_data_type_context_in_lookup(None, None)
        assert result is None


class TestBuildSystemPrompt:
    """Tests for build_system_prompt method."""

    def test_build_system_prompt_basic(self):
        """Test building basic system prompt."""
        config = {
            "project_name": "TestProject",
            "description": "A test project"
        }
        result = PromptBuilder.build_system_prompt(config)
        assert "## Project Context" in result
        assert "TestProject" in result
        assert "A test project" in result

    def test_build_system_prompt_with_user_prompts(self):
        """Test building system prompt with user-provided prompts."""
        config = {
            "project_name": "TestProject",
            "description": "A test project"
        }
        user_prompts = ["Focus on security issues", "Check for memory leaks"]
        result = PromptBuilder.build_system_prompt(config, user_prompts)
        assert "## Additional User Instructions" in result
        assert "Focus on security issues" in result
        assert "Check for memory leaks" in result

    def test_build_system_prompt_empty_user_prompts(self):
        """Test building system prompt with empty user prompts."""
        config = {
            "project_name": "TestProject",
            "description": "A test project"
        }
        user_prompts = ["", "  "]
        result = PromptBuilder.build_system_prompt(config, user_prompts)
        assert "## Additional User Instructions" not in result


class TestBuildOutputRequirements:
    """Tests for build_output_requirements method."""

    def test_build_output_requirements(self):
        """Test building output requirements."""
        result = PromptBuilder.build_output_requirements()
        assert isinstance(result, str)
        assert len(result) > 0


class TestBuildJsonOutputGuidance:
    """Tests for build_json_output_guidance method."""

    def test_build_json_output_guidance(self):
        """Test building JSON output guidance."""
        result = PromptBuilder.build_json_output_guidance()
        assert isinstance(result, str)
        assert len(result) > 0


class TestConvertJsonToCommentFormat:
    """Tests for _convert_json_to_comment_format method."""

    def test_convert_json_to_comment_format_basic(self):
        """Test converting basic JSON to comment format."""
        json_content = json.dumps({
            "function": "main",
            "file": "src/main.c",
            "code": "int main() { return 0; }"
        })
        result = PromptBuilder._convert_json_to_comment_format(json_content)
        assert "// Function - main" in result
        assert "// file : src/main.c" in result

    def test_convert_json_to_comment_format_with_context(self):
        """Test converting JSON with context to comment format."""
        json_content = json.dumps({
            "function": "process",
            "context": {
                "file": "src/processor.c",
                "start": 10,
                "end": 50
            },
            "code": "void process() { /* code */ }"
        })
        result = PromptBuilder._convert_json_to_comment_format(json_content)
        assert "// Function - process" in result
        assert "// file : src/processor.c" in result

    def test_convert_json_to_comment_format_with_functions_invoked(self):
        """Test converting JSON with functions_invoked to comment format."""
        json_content = json.dumps({
            "function": "main",
            "file": "src/main.c",
            "code": "int main() { helper(); return 0; }",
            "functions_invoked": ["helper"]
        })
        result = PromptBuilder._convert_json_to_comment_format(json_content)
        assert "// == Additional context when analyzing above function" in result
        assert "main() invokes the following function(s)" in result

    def test_convert_json_to_comment_format_with_invoked_by(self):
        """Test converting JSON with invoked_by to comment format."""
        json_content = json.dumps({
            "function": "helper",
            "file": "src/helper.c",
            "code": "void helper() { }",
            "invoked_by": ["main"]
        })
        result = PromptBuilder._convert_json_to_comment_format(json_content)
        assert "// == Caller context when analyzing above function" in result
        assert "helper() is called by the following function(s)" in result

    def test_convert_json_to_comment_format_with_data_types(self):
        """Test converting JSON with data_types_used to comment format."""
        json_content = json.dumps({
            "function": "process",
            "file": "src/processor.c",
            "code": "void process(MyStruct* s) { }",
            "data_types_used": ["MyStruct"]
        })
        result = PromptBuilder._convert_json_to_comment_format(json_content)
        assert "// == Data types used by above function" in result

    def test_convert_json_to_comment_format_with_constants(self):
        """Test converting JSON with constants_used to comment format."""
        json_content = json.dumps({
            "function": "process",
            "file": "src/processor.c",
            "code": "void process() { int x = MAX_SIZE; }",
            "constants_used": {"MAX_SIZE": 100}
        })
        result = PromptBuilder._convert_json_to_comment_format(json_content)
        assert "// === Constants used by function process()" in result
        assert "MAX_SIZE = 100" in result

    def test_convert_json_to_comment_format_invalid_json(self):
        """Test converting invalid JSON returns original content."""
        invalid_json = "not valid json"
        result = PromptBuilder._convert_json_to_comment_format(invalid_json)
        assert result == invalid_json

    def test_convert_json_to_comment_format_large_function(self):
        """Test converting JSON with large function (>300 lines)."""
        # Create a function with more than 300 lines
        large_code = "\n".join([f"line {i}" for i in range(350)])
        json_content = json.dumps({
            "function": "largeFunction",
            "file": "src/large.c",
            "code": large_code,
            "startLine": 1,
            "endLine": 350
        })
        result = PromptBuilder._convert_json_to_comment_format(json_content)
        assert "// LARGE FUNCTION - Use tools to read the actual code" in result


class TestApplyCodeContextPruning:
    """Tests for _apply_code_context_pruning method."""

    def test_apply_code_context_pruning_json_with_code(self):
        """Test applying code context pruning to JSON with code field."""
        json_content = json.dumps({
            "code": "// This is a comment\nint main() { return 0; }"
        })
        result = PromptBuilder._apply_code_context_pruning(json_content)
        # The result should be valid JSON
        data = json.loads(result)
        assert "code" in data

    def test_apply_code_context_pruning_plain_code(self):
        """Test applying code context pruning to plain code."""
        code = "// Comment\n#include <stdio.h>\nint main() { return 0; }"
        result = PromptBuilder._apply_code_context_pruning(code)
        assert isinstance(result, str)

    def test_apply_code_context_pruning_non_code(self):
        """Test applying code context pruning to non-code text."""
        text = "This is just plain text without any code indicators"
        result = PromptBuilder._apply_code_context_pruning(text)
        assert result == text


class TestBuildUserPrompt:
    """Tests for build_user_prompt method."""

    @patch('hindsight.core.prompts.prompt_builder._read_package_file')
    def test_build_user_prompt_specific_function(self, mock_read):
        """Test building user prompt for specific function analysis."""
        mock_read.return_value = "Analyze the following code:\n{json_content}"
        
        json_content = json.dumps({
            "function": "main",
            "file": "src/main.c",
            "code": "int main() { return 0; }"
        })
        
        result = PromptBuilder.build_user_prompt(json_content, analysis_type="specific_function")
        assert isinstance(result, str)
        assert len(result) > 0

    @patch('hindsight.core.prompts.prompt_builder._read_package_file')
    def test_build_user_prompt_entire_file(self, mock_read):
        """Test building user prompt for entire file analysis."""
        mock_read.return_value = "Analyze the entire file:\n{json_content}"
        
        json_content = json.dumps({
            "fileContext": {"file": "src/main.c", "content": "int main() { return 0; }"}
        })
        
        result = PromptBuilder.build_user_prompt(json_content, analysis_type="entire_file")
        assert isinstance(result, str)
        assert len(result) > 0


class TestBuildCompletePrompt:
    """Tests for build_complete_prompt method."""

    def test_build_complete_prompt(self):
        """Test building complete prompt returns tuple of system and user prompts."""
        config = {
            "project_name": "TestProject",
            "description": "A test project"
        }
        json_content = json.dumps({
            "function": "main",
            "file": "src/main.c",
            "code": "int main() { return 0; }"
        })
        
        system_prompt, user_prompt = PromptBuilder.build_complete_prompt(
            json_content,
            analysis_type="specific_function",
            config=config
        )
        
        assert isinstance(system_prompt, str)
        assert isinstance(user_prompt, str)
        assert "TestProject" in system_prompt
        assert len(user_prompt) > 0

    def test_build_complete_prompt_with_user_prompts(self):
        """Test building complete prompt with user-provided prompts."""
        config = {
            "project_name": "TestProject",
            "description": "A test project"
        }
        json_content = json.dumps({
            "function": "main",
            "file": "src/main.c",
            "code": "int main() { return 0; }"
        })
        user_prompts = ["Focus on security"]
        
        system_prompt, user_prompt = PromptBuilder.build_complete_prompt(
            json_content,
            analysis_type="specific_function",
            config=config,
            user_provided_prompts=user_prompts
        )
        
        assert "Focus on security" in system_prompt


class TestLookupFunctionBody:
    """Tests for _lookup_function_body method."""

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_function_body_provider_not_available(self, mock_provider_class):
        """Test lookup when FileContentProvider is not available."""
        mock_provider_class.get.side_effect = RuntimeError("Not initialized")
        result = PromptBuilder._lookup_function_body("myFunction", None)
        assert result is None

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_function_body_no_data(self, mock_provider_class):
        """Test lookup when no merged functions data is provided and file doesn't exist."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        
        with patch('hindsight.core.prompts.prompt_builder.get_output_directory_provider') as mock_output:
            mock_output.return_value.get_repo_artifacts_dir.return_value = "/fake/path"
            with patch('os.path.exists', return_value=False):
                result = PromptBuilder._lookup_function_body("myFunction", None)
                assert result is None

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_function_body_with_data(self, mock_provider_class):
        """Test lookup with provided merged functions data."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        mock_provider.guess_path.return_value = "/resolved/path/main.c"
        mock_provider.read_text.return_value = "line1\nline2\nline3\nline4\nline5"
        
        merged_data = {
            "myFunction": {
                "code": [{"file_name": "main.c", "start": 2, "end": 4}]
            }
        }
        
        result = PromptBuilder._lookup_function_body("myFunction", merged_data)
        assert result is not None
        assert result['file'] == "main.c"
        assert result['start_line'] == 2
        assert result['end_line'] == 4
        assert "line2" in result['code']

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_function_body_function_not_found(self, mock_provider_class):
        """Test lookup when function is not in the data."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        
        merged_data = {
            "otherFunction": {
                "code": [{"file_name": "main.c", "start": 2, "end": 4}]
            }
        }
        
        result = PromptBuilder._lookup_function_body("myFunction", merged_data)
        assert result is None

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_function_body_file_not_resolved(self, mock_provider_class):
        """Test lookup when file path cannot be resolved."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        mock_provider.guess_path.return_value = None
        mock_provider.resolve_file_path.return_value = None
        
        merged_data = {
            "myFunction": {
                "code": [{"file_name": "main.c", "start": 2, "end": 4}]
            }
        }
        
        result = PromptBuilder._lookup_function_body("myFunction", merged_data)
        assert result is None


class TestResolveFilePathWithProvider:
    """Tests for _resolve_file_path_with_provider method."""

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_resolve_file_path_provider_not_available(self, mock_provider_class):
        """Test resolution when FileContentProvider is not available."""
        mock_provider_class.get.side_effect = RuntimeError("Not initialized")
        result = PromptBuilder._resolve_file_path_with_provider("myFunction")
        assert result == 'Unknown'

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_resolve_file_path_with_class_method(self, mock_provider_class):
        """Test resolution for class method (e.g., MyClass::myMethod)."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        mock_provider.resolve_file_path.return_value = "/path/to/MyClass.cpp"
        
        result = PromptBuilder._resolve_file_path_with_provider("MyClass::myMethod")
        assert result == "/path/to/MyClass.cpp"

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_resolve_file_path_with_function_name(self, mock_provider_class):
        """Test resolution using function name directly."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        mock_provider.resolve_file_path.side_effect = [None, "/path/to/utils.c"]
        
        result = PromptBuilder._resolve_file_path_with_provider("myFunction")
        assert result == "/path/to/utils.c"

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_resolve_file_path_swift_fallback(self, mock_provider_class):
        """Test resolution with Swift filename fallback."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        # First two calls return None, third call (Swift filename) returns path
        mock_provider.resolve_file_path.side_effect = [None, None, "/path/to/MyClass.swift"]
        
        result = PromptBuilder._resolve_file_path_with_provider("MyClass::myMethod")
        assert result == "/path/to/MyClass.swift"

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_resolve_file_path_not_found(self, mock_provider_class):
        """Test resolution when file cannot be found."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        mock_provider.resolve_file_path.return_value = None
        
        result = PromptBuilder._resolve_file_path_with_provider("unknownFunction")
        assert result == 'Unknown'

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_resolve_file_path_with_dot_notation(self, mock_provider_class):
        """Test resolution for dot notation (e.g., SRSensor.isEnabled)."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        mock_provider.resolve_file_path.return_value = "/path/to/SRSensor.swift"
        
        result = PromptBuilder._resolve_file_path_with_provider("SRSensor.isEnabled")
        assert result == "/path/to/SRSensor.swift"


class TestLookupDataTypeBody:
    """Tests for _lookup_data_type_body method."""

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_data_type_body_provider_not_available(self, mock_provider_class):
        """Test lookup when FileContentProvider is not available."""
        mock_provider_class.get.side_effect = RuntimeError("Not initialized")
        result = PromptBuilder._lookup_data_type_body("MyStruct", None)
        assert result is None

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_data_type_body_no_data(self, mock_provider_class):
        """Test lookup when no merged data types data is provided and file doesn't exist."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        
        with patch('hindsight.core.prompts.prompt_builder.get_output_directory_provider') as mock_output:
            mock_output.return_value.get_repo_artifacts_dir.return_value = "/fake/path"
            with patch('os.path.exists', return_value=False):
                result = PromptBuilder._lookup_data_type_body("MyStruct", None)
                assert result is None

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_data_type_body_with_data(self, mock_provider_class):
        """Test lookup with provided merged data types data."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        mock_provider.guess_path.return_value = "/resolved/path/types.h"
        mock_provider.read_text.return_value = "line1\nstruct MyStruct {\nint x;\n};\nline5"
        
        merged_data = {
            "data_type_to_location_and_checksum": {
                "MyStruct": {
                    "code": [{"file_name": "types.h", "start": 2, "end": 4}]
                }
            }
        }
        
        result = PromptBuilder._lookup_data_type_body("MyStruct", merged_data)
        assert result is not None
        assert result['file'] == "types.h"
        assert result['start_line'] == 2
        assert result['end_line'] == 4
        assert "struct MyStruct" in result['code']

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_data_type_body_not_found(self, mock_provider_class):
        """Test lookup when data type is not in the data."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        
        merged_data = {
            "data_type_to_location_and_checksum": {
                "OtherStruct": {
                    "code": [{"file_name": "types.h", "start": 2, "end": 4}]
                }
            }
        }
        
        result = PromptBuilder._lookup_data_type_body("MyStruct", merged_data)
        assert result is None

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_data_type_body_file_not_resolved(self, mock_provider_class):
        """Test lookup when file path cannot be resolved."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        mock_provider.guess_path.return_value = None
        mock_provider.resolve_file_path.return_value = None
        
        merged_data = {
            "data_type_to_location_and_checksum": {
                "MyStruct": {
                    "code": [{"file_name": "types.h", "start": 2, "end": 4}]
                }
            }
        }
        
        result = PromptBuilder._lookup_data_type_body("MyStruct", merged_data)
        assert result is None

    @patch('hindsight.core.prompts.prompt_builder.FileContentProvider')
    def test_lookup_data_type_body_unexpected_format(self, mock_provider_class):
        """Test lookup with unexpected data format."""
        mock_provider = MagicMock()
        mock_provider_class.get.return_value = mock_provider
        
        # Data without the expected wrapper
        merged_data = {
            "MyStruct": {
                "code": [{"file_name": "types.h", "start": 2, "end": 4}]
            }
        }
        
        result = PromptBuilder._lookup_data_type_body("MyStruct", merged_data)
        assert result is None

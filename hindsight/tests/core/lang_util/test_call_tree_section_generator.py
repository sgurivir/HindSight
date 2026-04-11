"""
Unit tests for CallTreeSectionGenerator.

Tests the call tree section generation functionality for LLM prompts.
"""

import pytest
from unittest.mock import Mock, patch

from hindsight.core.lang_util.call_tree_section_generator import (
    CallTreeSectionGenerator,
    generate_call_tree_section_for_function
)


class TestCallTreeSectionGenerator:
    """Tests for CallTreeSectionGenerator class."""
    
    @pytest.fixture
    def sample_call_graph_data(self):
        """Create sample call graph data for testing."""
        return [
            {
                "file": "src/main.c",
                "functions": [
                    {
                        "function": "main",
                        "context": {
                            "file": "src/main.c",
                            "start": 10,
                            "end": 50
                        },
                        "functions_invoked": [
                            {
                                "function": "process_data",
                                "context": {
                                    "file": "src/processor.c",
                                    "start": 100,
                                    "end": 200
                                },
                                "functions_invoked": [
                                    {
                                        "function": "validate_input",
                                        "context": {
                                            "file": "src/validator.c",
                                            "start": 20,
                                            "end": 40
                                        },
                                        "functions_invoked": []
                                    },
                                    {
                                        "function": "transform_data",
                                        "context": {
                                            "file": "src/transformer.c",
                                            "start": 50,
                                            "end": 100
                                        },
                                        "functions_invoked": []
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "file": "src/processor.c",
                "functions": [
                    {
                        "function": "process_data",
                        "context": {
                            "file": "src/processor.c",
                            "start": 100,
                            "end": 200
                        },
                        "functions_invoked": [
                            {
                                "function": "validate_input",
                                "context": {
                                    "file": "src/validator.c",
                                    "start": 20,
                                    "end": 40
                                },
                                "functions_invoked": []
                            },
                            {
                                "function": "transform_data",
                                "context": {
                                    "file": "src/transformer.c",
                                    "start": 50,
                                    "end": 100
                                },
                                "functions_invoked": []
                            }
                        ]
                    }
                ]
            },
            {
                "file": "src/validator.c",
                "functions": [
                    {
                        "function": "validate_input",
                        "context": {
                            "file": "src/validator.c",
                            "start": 20,
                            "end": 40
                        },
                        "functions_invoked": []
                    }
                ]
            },
            {
                "file": "src/transformer.c",
                "functions": [
                    {
                        "function": "transform_data",
                        "context": {
                            "file": "src/transformer.c",
                            "start": 50,
                            "end": 100
                        },
                        "functions_invoked": []
                    }
                ]
            }
        ]
    
    def test_init(self, sample_call_graph_data):
        """Test initialization of CallTreeSectionGenerator."""
        generator = CallTreeSectionGenerator(
            call_graph_data=sample_call_graph_data,
            max_ancestor_depth=1,
            max_descendant_depth=3,
            max_children_per_node=5
        )
        
        assert generator.max_ancestor_depth == 1
        assert generator.max_descendant_depth == 3
        assert generator.max_children_per_node == 5
        assert generator.graph is not None
        assert generator.implementations is not None
        assert generator._normalized_name_map is not None
    
    def test_generate_section_for_existing_function(self, sample_call_graph_data):
        """Test generating section for an existing function."""
        generator = CallTreeSectionGenerator(sample_call_graph_data)
        
        section = generator.generate_section("process_data")
        
        assert "target_function" in section
        assert section["target_function"]["name"] == "process_data"
        assert "ancestors" in section
        assert "descendants" in section
    
    def test_generate_section_for_nonexistent_function(self, sample_call_graph_data):
        """Test generating section for a non-existent function."""
        generator = CallTreeSectionGenerator(sample_call_graph_data)
        
        section = generator.generate_section("nonexistent_function")
        
        assert section["target_function"]["name"] == "nonexistent_function"
        assert section["ancestors"] == []
        assert section["descendants"] == {}
    
    def test_generate_section_for_leaf_function(self, sample_call_graph_data):
        """Test generating section for a leaf function (no callees)."""
        generator = CallTreeSectionGenerator(sample_call_graph_data)
        
        section = generator.generate_section("validate_input")
        
        assert section["target_function"]["name"] == "validate_input"
        # Leaf function should have no children in descendants
        descendants = section["descendants"]
        assert descendants.get("children", []) == []
    
    def test_generate_section_for_root_function(self, sample_call_graph_data):
        """Test generating section for a root function (no callers)."""
        generator = CallTreeSectionGenerator(sample_call_graph_data)
        
        section = generator.generate_section("main")
        
        assert section["target_function"]["name"] == "main"
        # Root function should have no ancestors
        assert section["ancestors"] == []
    
    def test_format_as_text(self, sample_call_graph_data):
        """Test formatting section as text."""
        generator = CallTreeSectionGenerator(sample_call_graph_data)
        
        section = generator.generate_section("process_data")
        text = generator.format_as_text(section)
        
        assert "// === CALL TREE CONTEXT ===" in text
        assert "process_data" in text
        assert "CALLERS" in text or "CALLEES" in text
    
    def test_format_as_text_includes_file_paths(self, sample_call_graph_data):
        """Test that formatted text includes file paths."""
        generator = CallTreeSectionGenerator(sample_call_graph_data)
        
        section = generator.generate_section("process_data")
        text = generator.format_as_text(section)
        
        # Should include file path references
        assert "src/" in text or ".c" in text
    
    def test_estimate_token_count(self, sample_call_graph_data):
        """Test token count estimation."""
        generator = CallTreeSectionGenerator(sample_call_graph_data)
        
        section = generator.generate_section("process_data")
        token_count = generator.estimate_token_count(section)
        
        assert token_count > 0
        assert isinstance(token_count, int)
    
    def test_can_reduce_depth(self, sample_call_graph_data):
        """Test depth reduction capability check."""
        generator = CallTreeSectionGenerator(
            sample_call_graph_data,
            max_ancestor_depth=2,
            max_descendant_depth=3
        )
        
        assert generator.can_reduce_depth() is True
        
        # Reduce to minimum
        generator._current_ancestor_depth = 0
        generator._current_descendant_depth = 1
        
        assert generator.can_reduce_depth() is False
    
    def test_reduce_depth(self, sample_call_graph_data):
        """Test depth reduction."""
        generator = CallTreeSectionGenerator(
            sample_call_graph_data,
            max_ancestor_depth=2,
            max_descendant_depth=3
        )
        
        initial_descendant_depth = generator._current_descendant_depth
        generator.reduce_depth()
        
        # Should reduce descendant depth first
        assert generator._current_descendant_depth == initial_descendant_depth - 1
    
    def test_reset_depth(self, sample_call_graph_data):
        """Test depth reset."""
        generator = CallTreeSectionGenerator(
            sample_call_graph_data,
            max_ancestor_depth=2,
            max_descendant_depth=3
        )
        
        generator.reduce_depth()
        generator.reduce_depth()
        generator.reset_depth()
        
        assert generator._current_ancestor_depth == 2
        assert generator._current_descendant_depth == 3
    
    def test_max_children_per_node_limit(self, sample_call_graph_data):
        """Test that max_children_per_node limits children."""
        generator = CallTreeSectionGenerator(
            sample_call_graph_data,
            max_children_per_node=1
        )
        
        section = generator.generate_section("process_data")
        descendants = section["descendants"]
        
        # Should have at most 1 child
        children = descendants.get("children", [])
        assert len(children) <= 1


class TestGenerateCallTreeSectionForFunction:
    """Tests for the convenience function."""
    
    @pytest.fixture
    def sample_call_graph_data(self):
        """Create sample call graph data for testing."""
        return [
            {
                "file": "src/main.c",
                "functions": [
                    {
                        "function": "main",
                        "context": {
                            "file": "src/main.c",
                            "start": 10,
                            "end": 50
                        },
                        "functions_invoked": [
                            {
                                "function": "helper",
                                "context": {
                                    "file": "src/helper.c",
                                    "start": 5,
                                    "end": 20
                                },
                                "functions_invoked": []
                            }
                        ]
                    }
                ]
            },
            {
                "file": "src/helper.c",
                "functions": [
                    {
                        "function": "helper",
                        "context": {
                            "file": "src/helper.c",
                            "start": 5,
                            "end": 20
                        },
                        "functions_invoked": []
                    }
                ]
            }
        ]
    
    def test_returns_text_for_valid_function(self, sample_call_graph_data):
        """Test that function returns text for valid input."""
        result = generate_call_tree_section_for_function(
            call_graph_data=sample_call_graph_data,
            function_name="main"
        )
        
        assert result is not None
        assert isinstance(result, str)
        assert "CALL TREE CONTEXT" in result
    
    def test_returns_none_for_empty_data(self):
        """Test that function returns None for empty data."""
        result = generate_call_tree_section_for_function(
            call_graph_data=None,
            function_name="main"
        )
        
        assert result is None
    
    def test_returns_none_for_empty_function_name(self, sample_call_graph_data):
        """Test that function returns None for empty function name."""
        result = generate_call_tree_section_for_function(
            call_graph_data=sample_call_graph_data,
            function_name=""
        )
        
        assert result is None
    
    def test_respects_max_tokens(self, sample_call_graph_data):
        """Test that function respects max_tokens limit."""
        # With very low max_tokens, should return None
        result = generate_call_tree_section_for_function(
            call_graph_data=sample_call_graph_data,
            function_name="main",
            max_tokens=10  # Very low limit
        )
        
        # Should either return None or a very short result
        if result is not None:
            # If it returns something, it should be within reasonable bounds
            assert len(result) < 1000
    
    def test_custom_depth_settings(self, sample_call_graph_data):
        """Test with custom depth settings."""
        result = generate_call_tree_section_for_function(
            call_graph_data=sample_call_graph_data,
            function_name="main",
            max_ancestor_depth=1,
            max_descendant_depth=1,
            max_children_per_node=2
        )
        
        assert result is not None
        assert isinstance(result, str)


class TestCallTreeSectionGeneratorEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_empty_call_graph(self):
        """Test with empty call graph data."""
        generator = CallTreeSectionGenerator(call_graph_data=[])
        
        section = generator.generate_section("any_function")
        
        assert section["target_function"]["name"] == "any_function"
        assert section["ancestors"] == []
        assert section["descendants"] == {}
    
    def test_cyclic_call_graph(self):
        """Test with cyclic call graph (A calls B, B calls A)."""
        cyclic_data = [
            {
                "file": "src/a.c",
                "functions": [
                    {
                        "function": "func_a",
                        "context": {"file": "src/a.c", "start": 1, "end": 10},
                        "functions_invoked": [
                            {
                                "function": "func_b",
                                "context": {"file": "src/b.c", "start": 1, "end": 10},
                                "functions_invoked": [
                                    {
                                        "function": "func_a",
                                        "context": {"file": "src/a.c", "start": 1, "end": 10},
                                        "functions_invoked": []
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "file": "src/b.c",
                "functions": [
                    {
                        "function": "func_b",
                        "context": {"file": "src/b.c", "start": 1, "end": 10},
                        "functions_invoked": [
                            {
                                "function": "func_a",
                                "context": {"file": "src/a.c", "start": 1, "end": 10},
                                "functions_invoked": []
                            }
                        ]
                    }
                ]
            }
        ]
        
        generator = CallTreeSectionGenerator(cyclic_data)
        
        # Should not hang or crash
        section = generator.generate_section("func_a")
        text = generator.format_as_text(section)
        
        assert section is not None
        assert text is not None
    
    def test_self_recursive_function(self):
        """Test with self-recursive function."""
        recursive_data = [
            {
                "file": "src/recursive.c",
                "functions": [
                    {
                        "function": "factorial",
                        "context": {"file": "src/recursive.c", "start": 1, "end": 10},
                        "functions_invoked": [
                            {
                                "function": "factorial",
                                "context": {"file": "src/recursive.c", "start": 1, "end": 10},
                                "functions_invoked": []
                            }
                        ]
                    }
                ]
            }
        ]
        
        generator = CallTreeSectionGenerator(recursive_data)
        
        # Should not hang or crash
        section = generator.generate_section("factorial")
        
        assert section is not None
        assert section["target_function"]["name"] == "factorial"
    
    def test_function_with_no_location(self):
        """Test function with missing location information."""
        data_no_location = [
            {
                "file": "src/main.c",
                "functions": [
                    {
                        "function": "no_location_func",
                        "functions_invoked": []
                    }
                ]
            }
        ]
        
        generator = CallTreeSectionGenerator(data_no_location)
        section = generator.generate_section("no_location_func")
        text = generator.format_as_text(section)
        
        assert section is not None
        assert text is not None
    
    def test_dict_format_call_graph(self):
        """Test with dict-wrapped call graph format."""
        dict_format_data = {
            "call_graph": [
                {
                    "file": "src/main.c",
                    "functions": [
                        {
                            "function": "main",
                            "context": {"file": "src/main.c", "start": 1, "end": 10},
                            "functions_invoked": []
                        }
                    ]
                }
            ]
        }
        
        generator = CallTreeSectionGenerator(dict_format_data)
        section = generator.generate_section("main")
        
        assert section["target_function"]["name"] == "main"


class TestFunctionNameNormalization:
    """Tests for function name normalization and fuzzy matching."""
    
    def test_normalize_function_name_removes_parentheses(self):
        """Test that normalization removes parentheses and parameters."""
        result = CallTreeSectionGenerator._normalize_function_name("MyClass::method()")
        assert result == "MyClass::method"
        
        result = CallTreeSectionGenerator._normalize_function_name("func(int x, int y)")
        assert result == "func"
    
    def test_normalize_function_name_handles_swift_selectors(self):
        """Test that normalization handles Swift selector syntax."""
        result = CallTreeSectionGenerator._normalize_function_name("AppDelegate::application(_:didFinishLaunchingWithOptions:)")
        # The normalized form should have the selector extracted and normalized
        assert result == "AppDelegate::application::didFinishLaunchingWithOptions"
    
    def test_normalize_function_name_removes_trailing_colons(self):
        """Test that normalization removes trailing colons."""
        result = CallTreeSectionGenerator._normalize_function_name("method:")
        assert result == "method"
        
        result = CallTreeSectionGenerator._normalize_function_name("method::")
        assert result == "method"
    
    def test_normalize_function_name_handles_empty_string(self):
        """Test that normalization handles empty strings."""
        result = CallTreeSectionGenerator._normalize_function_name("")
        assert result == ""
        
        result = CallTreeSectionGenerator._normalize_function_name(None)
        assert result is None
    
    def test_find_function_exact_match(self):
        """Test that exact match is preferred."""
        data = [
            {
                "file": "src/main.c",
                "functions": [
                    {
                        "function": "process_data",
                        "context": {"file": "src/main.c", "start": 1, "end": 10},
                        "functions_invoked": []
                    }
                ]
            }
        ]
        
        generator = CallTreeSectionGenerator(data)
        result = generator._find_function_in_graph("process_data")
        
        assert result == "process_data"
    
    def test_find_function_normalized_match(self):
        """Test that normalized matching works for function name variations."""
        data = [
            {
                "file": "src/main.cpp",
                "functions": [
                    {
                        "function": "MyClass::processData()",
                        "context": {"file": "src/main.cpp", "start": 1, "end": 10},
                        "functions_invoked": []
                    }
                ]
            }
        ]
        
        generator = CallTreeSectionGenerator(data)
        
        # Should find the function using normalized matching (without parentheses)
        result = generator._find_function_in_graph("MyClass::processData")
        assert result == "MyClass::processData()"
    
    def test_find_function_with_parentheses_variation(self):
        """Test matching functions with/without parentheses."""
        data = [
            {
                "file": "src/main.cpp",
                "functions": [
                    {
                        "function": "MyClass::method",
                        "context": {"file": "src/main.cpp", "start": 1, "end": 10},
                        "functions_invoked": []
                    }
                ]
            }
        ]
        
        generator = CallTreeSectionGenerator(data)
        
        # Should find the function even when searching with parentheses
        result = generator._find_function_in_graph("MyClass::method()")
        assert result == "MyClass::method"
    
    def test_find_function_returns_none_for_nonexistent(self):
        """Test that None is returned for non-existent functions."""
        data = [
            {
                "file": "src/main.c",
                "functions": [
                    {
                        "function": "existing_func",
                        "context": {"file": "src/main.c", "start": 1, "end": 10},
                        "functions_invoked": []
                    }
                ]
            }
        ]
        
        generator = CallTreeSectionGenerator(data)
        result = generator._find_function_in_graph("nonexistent_func")
        
        assert result is None
    
    def test_generate_section_uses_fuzzy_matching(self):
        """Test that generate_section uses fuzzy matching to find functions."""
        data = [
            {
                "file": "src/main.cpp",
                "functions": [
                    {
                        "function": "MyClass::processData()",
                        "context": {"file": "src/main.cpp", "start": 1, "end": 50},
                        "functions_invoked": []
                    }
                ]
            }
        ]
        
        generator = CallTreeSectionGenerator(data)
        
        # Should find the function using normalized matching (without parentheses)
        section = generator.generate_section("MyClass::processData")
        
        # The target function name should be the actual name from the graph
        assert section["target_function"]["name"] == "MyClass::processData()"


class TestConstrainedDepthSettings:
    """Tests for the constrained depth settings (1 ancestor, 3 descendants)."""
    
    @pytest.fixture
    def deep_call_graph_data(self):
        """Create a call graph with multiple levels for depth testing."""
        return [
            {
                "file": "src/level0.c",
                "functions": [
                    {
                        "function": "level0_func",
                        "context": {"file": "src/level0.c", "start": 1, "end": 10},
                        "functions_invoked": [
                            {
                                "function": "level1_func",
                                "context": {"file": "src/level1.c", "start": 1, "end": 10},
                                "functions_invoked": [
                                    {
                                        "function": "level2_func",
                                        "context": {"file": "src/level2.c", "start": 1, "end": 10},
                                        "functions_invoked": [
                                            {
                                                "function": "level3_func",
                                                "context": {"file": "src/level3.c", "start": 1, "end": 10},
                                                "functions_invoked": [
                                                    {
                                                        "function": "level4_func",
                                                        "context": {"file": "src/level4.c", "start": 1, "end": 10},
                                                        "functions_invoked": []
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                "file": "src/level1.c",
                "functions": [
                    {
                        "function": "level1_func",
                        "context": {"file": "src/level1.c", "start": 1, "end": 10},
                        "functions_invoked": [
                            {
                                "function": "level2_func",
                                "context": {"file": "src/level2.c", "start": 1, "end": 10},
                                "functions_invoked": []
                            }
                        ]
                    }
                ]
            },
            {
                "file": "src/level2.c",
                "functions": [
                    {
                        "function": "level2_func",
                        "context": {"file": "src/level2.c", "start": 1, "end": 10},
                        "functions_invoked": [
                            {
                                "function": "level3_func",
                                "context": {"file": "src/level3.c", "start": 1, "end": 10},
                                "functions_invoked": []
                            }
                        ]
                    }
                ]
            },
            {
                "file": "src/level3.c",
                "functions": [
                    {
                        "function": "level3_func",
                        "context": {"file": "src/level3.c", "start": 1, "end": 10},
                        "functions_invoked": [
                            {
                                "function": "level4_func",
                                "context": {"file": "src/level4.c", "start": 1, "end": 10},
                                "functions_invoked": []
                            }
                        ]
                    }
                ]
            },
            {
                "file": "src/level4.c",
                "functions": [
                    {
                        "function": "level4_func",
                        "context": {"file": "src/level4.c", "start": 1, "end": 10},
                        "functions_invoked": []
                    }
                ]
            }
        ]
    
    def test_one_ancestor_depth(self, deep_call_graph_data):
        """Test that only 1 level of ancestors (direct callers) is shown."""
        generator = CallTreeSectionGenerator(
            deep_call_graph_data,
            max_ancestor_depth=1,  # Only direct callers
            max_descendant_depth=3
        )
        
        # level2_func is called by level1_func
        section = generator.generate_section("level2_func")
        ancestors = section["ancestors"]
        
        # Should have at most 1 level of ancestors
        for path in ancestors:
            assert len(path) <= 1, "Should only show 1 level of ancestors (direct callers)"
    
    def test_three_descendant_depth(self, deep_call_graph_data):
        """Test that 3 levels of descendants (callees) are shown."""
        generator = CallTreeSectionGenerator(
            deep_call_graph_data,
            max_ancestor_depth=1,
            max_descendant_depth=3  # 3 levels of callees
        )
        
        # level1_func calls level2_func -> level3_func -> level4_func
        section = generator.generate_section("level1_func")
        descendants = section["descendants"]
        
        # Count the depth of the descendant tree
        def get_max_depth(node, current_depth=0):
            if not node or not node.get("children"):
                return current_depth
            max_child_depth = current_depth
            for child in node.get("children", []):
                child_depth = get_max_depth(child, current_depth + 1)
                max_child_depth = max(max_child_depth, child_depth)
            return max_child_depth
        
        max_depth = get_max_depth(descendants)
        assert max_depth <= 3, f"Should show at most 3 levels of descendants, got {max_depth}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

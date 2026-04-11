#!/usr/bin/env python3
"""
Test script for CommitExtendedContextProvider
Tests the additional context generation functionality.
"""

import os
import sys
import tempfile
from pathlib import Path

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.diff_analyzers.commit_additional_context_provider import CommitExtendedContextProvider


def test_context_provider():
    """Test the CommitExtendedContextProvider with a simple example."""
    print("Testing CommitExtendedContextProvider...")
    
    # Create a temporary directory structure for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create a simple test file
        test_file = temp_path / "test.c"
        test_file.write_text("""
#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

int main() {
    int result = add(5, 3);
    printf("Result: %d\\n", result);
    return 0;
}
""")
        
        # Initialize the context provider
        context_provider = CommitExtendedContextProvider(
            repo_path=str(temp_path),
            exclude_directories=['.git', 'build']
        )
        
        # Test context generation
        changed_files = ["test.c"]
        context = context_provider.get_context_for_files(changed_files)
        
        print("Generated context:")
        print("=" * 50)
        print(context)
        print("=" * 50)
        
        # Verify context contains expected information
        if "Additional Context from Code Analysis" in context:
            print("✓ Context header found")
        else:
            print("✗ Context header missing")
            
        if "test.c" in context:
            print("✓ File name found in context")
        else:
            print("✗ File name missing from context")
            
        print("\nTest completed!")


def test_empty_files():
    """Test with empty file list."""
    print("\nTesting with empty file list...")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        context_provider = CommitExtendedContextProvider(
            repo_path=temp_dir,
            exclude_directories=[]
        )
        
        context = context_provider.get_context_for_files([])
        print(f"Empty files context: {context}")
        
        if "No files were changed" in context:
            print("✓ Empty files handled correctly")
        else:
            print("✗ Empty files not handled correctly")


def test_nonexistent_files():
    """Test with non-existent files."""
    print("\nTesting with non-existent files...")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        context_provider = CommitExtendedContextProvider(
            repo_path=temp_dir,
            exclude_directories=[]
        )
        
        context = context_provider.get_context_for_files(["nonexistent.c"])
        print(f"Non-existent files context: {context}")
        
        if "No valid files found" in context:
            print("✓ Non-existent files handled correctly")
        else:
            print("✗ Non-existent files not handled correctly")


if __name__ == "__main__":
    print("CommitExtendedContextProvider Test Suite")
    print("=" * 50)
    
    try:
        test_context_provider()
        test_empty_files()
        test_nonexistent_files()
        
        print("\n" + "=" * 50)
        print("All tests completed!")
        
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
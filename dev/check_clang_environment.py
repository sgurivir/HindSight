#!/usr/bin/env python3
"""
Script to check clang/LLVM environment configuration.
This helps debug template argument kind errors that vary by platform.
Also shows exactly which clang/libclang version Hindsight is using.

Usage:
    python3 -m dev.check_clang_environment
"""

import os
import sys
import platform
import subprocess
import logging
from pathlib import Path

# Add project root to Python path to import Hindsight modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def print_section(title):
    """Print a formatted section header."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def run_command(cmd):
    """Run a shell command and return output."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception as e:
        return f"Error: {e}"


def check_system_clang():
    """Check system clang version."""
    print_section("1. System Clang Version")
    
    version = run_command("clang --version")
    if version:
        print(version)
    else:
        print("clang command not found in PATH")


def check_llvm_config():
    """Check LLVM configuration."""
    print_section("2. LLVM Configuration")
    
    version = run_command("llvm-config --version")
    if version:
        print(f"LLVM version: {version}")
        
        prefix = run_command("llvm-config --prefix")
        if prefix:
            print(f"LLVM prefix: {prefix}")
        
        libdir = run_command("llvm-config --libdir")
        if libdir:
            print(f"LLVM libdir: {libdir}")
    else:
        print("llvm-config command not found in PATH")


def check_environment_variable():
    """Check LIBCLANG_PATH environment variable."""
    print_section("3. LIBCLANG_PATH Environment Variable")
    
    libclang_path = os.environ.get('LIBCLANG_PATH')
    
    if not libclang_path:
        print("LIBCLANG_PATH is not set")
    else:
        print(f"LIBCLANG_PATH={libclang_path}")
        
        # Check if file exists
        if os.path.isfile(libclang_path):
            print("✓ File exists")
            
            # Get file size
            size = os.path.getsize(libclang_path)
            print(f"  Size: {size:,} bytes ({size / (1024*1024):.1f} MB)")
            
            # Check if readable
            if os.access(libclang_path, os.R_OK):
                print("✓ File is readable")
            else:
                print("✗ File is not readable")
        else:
            print("✗ File does not exist at this path")


def check_python_libclang():
    """Check Python libclang configuration."""
    print_section("4. Python libclang Configuration")
    
    try:
        import clang.cindex
        
        # Get the library path being used
        lib = clang.cindex.conf.lib
        lib_path = getattr(lib, '_name', 'Unknown')
        print(f"Python libclang library: {lib_path}")
        
        # Check if it's a real file
        if lib_path != 'Unknown' and os.path.isfile(lib_path):
            print(f"✓ Library file exists")
            size = os.path.getsize(lib_path)
            print(f"  Size: {size:,} bytes ({size / (1024*1024):.1f} MB)")
        
        # Get clang version from the library
        try:
            version_str = lib.clang_getClangVersion()
            # Convert CXString to Python string
            if hasattr(version_str, 'spelling'):
                version = version_str.spelling
            else:
                version = str(version_str)
            print(f"Clang version from Python: {version}")
        except Exception as e:
            print(f"Could not get version: {e}")
        
        # Check if library is loaded
        print(f"Library loaded: {lib is not None}")
        
        # Try to get some basic info
        try:
            # Create a simple index to verify it works
            index = clang.cindex.Index.create()
            print("✓ Can create clang Index (library is functional)")
        except Exception as e:
            print(f"✗ Cannot create Index: {e}")
            
    except ImportError:
        print("✗ Python clang module not installed")
        print("  Install with: pip install libclang")
    except Exception as e:
        print(f"✗ Error checking Python libclang: {e}")


def check_repoiq_libclang():
    """Check the exact libclang configuration that Hindsight uses."""
    print_section("5. Hindsight-Specific LibClang Configuration")
    
    try:
        # Import Hindsight's Environment class
        from hindsight.core.lang_util.Environment import Environment
        from clang import cindex
        
        # 1. Check what Environment detects (without initializing)
        print("Path Detection:")
        try:
            detected_path = Environment.get_libclang_path_from_platform()
            print(f"  Environment.get_libclang_path_from_platform(): {detected_path}")
            if detected_path and os.path.exists(detected_path):
                print(f"  ✓ Path exists")
                stat_info = os.stat(detected_path)
                print(f"  File size: {stat_info.st_size:,} bytes ({stat_info.st_size / (1024*1024):.1f} MB)")
            else:
                print(f"  ✗ Path does not exist")
        except Exception as e:
            print(f"  Error: {e}")
        
        print()
        
        # 2. Check if libclang is already initialized (from section 4)
        print("Initialization Status:")
        already_initialized = False
        try:
            # Try to create an index - if this works, libclang is already initialized
            test_index = cindex.Index.create()
            print("  ✓ LibClang already initialized (from previous checks)")
            already_initialized = True
        except Exception:
            print("  LibClang not yet initialized")
        
        # 3. If not initialized, try Hindsight's initialization method
        if not already_initialized:
            print()
            print("Attempting Hindsight Initialization:")
            try:
                Environment.set_clang_path_from_brew_or_pip()
                print("  ✓ Successfully initialized using Environment.set_clang_path_from_brew_or_pip()")
                already_initialized = True
            except Exception as e:
                # Suppress the verbose error message, just show it failed
                print(f"  ✗ Initialization failed")
                try:
                    Environment.initialize_libclang()
                    print("  ✓ Fallback: Successfully initialized using Environment.initialize_libclang()")
                    already_initialized = True
                except Exception as e2:
                    print(f"  ✗ Fallback also failed")
        
        print()
        
        # 4. Get version and test functionality
        print("Functionality Test:")
        if not already_initialized:
            print("  ⚠️  Skipping - libclang not properly initialized")
            print("  Note: This is expected if libclang was already checked in section 4")
            return
        
        try:
            # Get library path
            from clang.cindex import conf
            lib_path = conf.lib._name if hasattr(conf.lib, '_name') else 'Unknown'
            print(f"  Using libclang from: {lib_path}")
            
            # Check if pip or system
            if 'site-packages/clang/native' in lib_path:
                print("  ✓ Using pip-installed libclang")
                try:
                    import pkg_resources
                    clang_version = pkg_resources.get_distribution('libclang').version
                    print(f"  Pip package version: {clang_version}")
                except:
                    pass
            else:
                print("  ✓ Using system/homebrew libclang")
            
            # Test parsing
            index = cindex.Index.create()
            temp_code = "int main() { return 0; }"
            tu = index.parse('temp.c', unsaved_files=[('temp.c', temp_code)])
            if tu:
                print("  ✓ Successfully parsed test code - libclang is functional")
                print("  ✓ Hindsight will use this libclang configuration")
            else:
                print("  ✗ Failed to parse test code")
                
        except Exception as e:
            print(f"  ✗ Error testing functionality: {e}")
            
    except ImportError as e:
        print(f"✗ Cannot import Hindsight modules: {e}")
        print("  Make sure you're running from the project root")
    except Exception as e:
        print(f"✗ Error checking Hindsight configuration: {e}")


def find_llvm_installations():
    """Find available LLVM installations."""
    print_section("6. Available LLVM Installations")
    
    system = platform.system()
    found_any = False
    
    if system == "Darwin":  # macOS
        print("Searching macOS locations...")
        
        # Homebrew locations
        homebrew_paths = [
            "/opt/homebrew/opt",
            "/opt/homebrew/Cellar",
            "/usr/local/opt",
            "/usr/local/Cellar"
        ]
        
        for base_path in homebrew_paths:
            if os.path.isdir(base_path):
                llvm_dirs = list(Path(base_path).glob("llvm*"))
                if llvm_dirs:
                    print(f"\nFound in {base_path}:")
                    for d in sorted(llvm_dirs):
                        print(f"  {d}")
                        # Look for libclang
                        libclang = list(d.glob("**/libclang.dylib"))
                        if libclang:
                            print(f"    → libclang: {libclang[0]}")
                    found_any = True
    
    elif system == "Linux":
        print("Searching Linux locations...")
        
        # Common Linux paths
        linux_paths = [
            "/usr/lib",
            "/usr/lib/x86_64-linux-gnu",
            "/usr/lib/aarch64-linux-gnu",
            "/usr/local/lib"
        ]
        
        for base_path in linux_paths:
            if os.path.isdir(base_path):
                # Look for LLVM directories
                llvm_dirs = list(Path(base_path).glob("llvm*"))
                if llvm_dirs:
                    print(f"\nFound in {base_path}:")
                    for d in sorted(llvm_dirs):
                        print(f"  {d}")
                        # Look for libclang
                        libclang = list(d.glob("**/libclang.so*"))
                        if libclang:
                            for lib in libclang[:3]:  # Show first 3
                                print(f"    → {lib}")
                    found_any = True
                
                # Also look for direct libclang files
                libclang_files = list(Path(base_path).glob("libclang.so*"))
                if libclang_files:
                    print(f"\nDirect libclang files in {base_path}:")
                    for lib in sorted(libclang_files)[:5]:  # Show first 5
                        print(f"  {lib}")
                    found_any = True
    
    if not found_any:
        print("No LLVM installations found in common locations")


def check_platform_info():
    """Display platform information."""
    print_section("7. Platform Information")
    
    print(f"OS: {platform.system()}")
    print(f"OS Version: {platform.version()}")
    print(f"Architecture: {platform.machine()}")
    print(f"Python Version: {sys.version}")
    print(f"Python Executable: {sys.executable}")


def print_recommendations():
    """Print recommendations for fixing issues."""
    print_section("Recommendations")
    
    print("""
If you see 'Unknown template argument kind 604' errors:

1. Check version consistency:
   - System clang version should match Python libclang
   - Set LIBCLANG_PATH to use specific version

2. To set LIBCLANG_PATH:
   
   Linux:
   export LIBCLANG_PATH=/usr/lib/llvm-18/lib/libclang.so
   
   macOS (Homebrew):
   export LIBCLANG_PATH=/opt/homebrew/opt/llvm/lib/libclang.dylib
   
   Or add to your shell profile (~/.bashrc, ~/.zshrc):
   echo 'export LIBCLANG_PATH=/path/to/libclang' >> ~/.bashrc

3. To upgrade Python libclang:
   pip install --upgrade libclang

4. Run the debug script to analyze template errors:
   python3 -m dev.debug_template_args --repo /path/to/repo

5. Check if errors affect your analysis:
   - Run your normal analysis workflow
   - If results are complete and accurate, errors are benign
   - The code already handles these errors gracefully
""")


def main():
    """Main function."""
    print("=" * 60)
    print("Clang/LLVM Environment Check")
    print("=" * 60)
    
    check_system_clang()
    check_llvm_config()
    check_environment_variable()
    check_python_libclang()
    check_repoiq_libclang()
    find_llvm_installations()
    check_platform_info()
    print_recommendations()
    
    print("\n" + "=" * 60)
    print("Check complete!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
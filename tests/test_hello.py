import subprocess
import os
import sys


def test_hello_file_exists():
    """Test that hello.py file exists"""
    assert os.path.exists("hello.py"), "hello.py file should exist"


def test_hello_file_content():
    """Test that hello.py contains the correct content"""
    with open("hello.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Check that file contains exactly the expected line
    expected_content = 'print("Hello, World!")\n'
    assert content == expected_content, f"File content should be exactly '{expected_content}'"


def test_hello_file_encoding():
    """Test that hello.py uses UTF-8 encoding"""
    with open("hello.py", "r", encoding="utf-8") as f:
        # If we can read it with UTF-8, it's encoded correctly
        content = f.read()
        assert len(content) > 0, "File should not be empty"


def test_hello_file_syntax():
    """Test that hello.py has valid Python syntax"""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", "hello.py"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"Syntax check failed: {result.stderr}"


def test_hello_execution():
    """Test that hello.py executes correctly"""
    result = subprocess.run(
        [sys.executable, "hello.py"],
        capture_output=True,
        text=True
    )
    
    # Check exit code
    assert result.returncode == 0, f"Program should exit with code 0, got {result.returncode}"
    
    # Check stdout
    assert result.stdout == "Hello, World!\n", f"Expected 'Hello, World!\\n', got '{result.stdout}'"
    
    # Check stderr (should be empty)
    assert result.stderr == "", f"Expected no stderr output, got '{result.stderr}'"


def test_hello_file_ending():
    """Test that hello.py ends with a newline character"""
    with open("hello.py", "rb") as f:
        content = f.read()
    
    # Check that file ends with newline
    assert content.endswith(b"\n"), "File should end with a newline character"


def test_hello_no_trailing_whitespace():
    """Test that hello.py has no trailing whitespace"""
    with open("hello.py", "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines):
        # Check for trailing whitespace (except the newline at end of file)
        if i < len(lines) - 1:
            assert not line.rstrip("\n").rstrip(), f"Line {i+1} has trailing whitespace"


def test_hello_line_length():
    """Test that hello.py follows PEP 8 line length guidelines"""
    with open("hello.py", "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines):
        # Remove newline for length check
        line_length = len(line.rstrip("\n"))
        assert line_length <= 79, f"Line {i+1} exceeds 79 characters (length: {line_length})"


def test_hello_indentation():
    """Test that hello.py uses 4-space indentation (no tabs)"""
    with open("hello.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Check for tabs
    assert "\t" not in content, "File should not contain tabs"
    
    # Check for 4-space indentation (if there are indented lines)
    lines = content.split("\n")
    for line in lines:
        if line.startswith("    "):
            # Should start with 4 spaces
            pass
        elif line.startswith("\t"):
            # Should not start with tabs
            assert False, "File should not use tabs for indentation"


def test_hello_python_version_compatibility():
    """Test that hello.py is compatible with Python 3.6+"""
    # Check that the code uses only basic Python syntax
    with open("hello.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # The code should be simple enough to work with Python 3.6+
    # This is a basic check - the actual execution test covers this
    assert "print(" in content, "File should contain print statement"
    assert '"Hello, World!"' in content, "File should contain the exact string"


def test_hello_no_external_dependencies():
    """Test that hello.py has no external dependencies"""
    with open("hello.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Check for import statements
    lines = content.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert False, f"File should not contain import statements, found: {stripped}"


def test_hello_file_permissions():
    """Test that hello.py is readable"""
    assert os.access("hello.py", os.R_OK), "hello.py should be readable"
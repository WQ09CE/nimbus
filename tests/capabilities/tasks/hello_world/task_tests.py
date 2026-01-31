"""
Test: hello_world
Difficulty: easy
Category: file_operations

Verifies that the agent can create a file with specific content.
"""

from pathlib import Path


def test_hello_file_exists(workspace: Path):
    """Test that hello.txt file exists."""
    hello_path = workspace / "hello.txt"
    assert hello_path.exists(), f"File {hello_path} does not exist"


def test_hello_file_content(workspace: Path):
    """Test that hello.txt contains exactly 'Hello, world!'"""
    hello_path = workspace / "hello.txt"
    content = hello_path.read_text().strip()
    assert content == "Hello, world!", (
        f"Expected 'Hello, world!' but got '{content}'"
    )

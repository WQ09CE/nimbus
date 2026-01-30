"""Test cases for utility functions."""
import pytest
from utils import slugify


class TestSlugify:
    """Test cases for the slugify function."""

    def test_slugify_basic_text(self):
        """Test slugify with basic text."""
        assert slugify("Hello World") == "hello-world"

    def test_slugify_with_special_characters(self):
        """Test slugify with special characters and multiple spaces."""
        assert slugify("Hello!!! World @#$") == "hello-world"

    def test_slugify_with_leading_trailing_spaces(self):
        """Test slugify with leading and trailing spaces."""
        assert slugify("  Hello World  ") == "hello-world"

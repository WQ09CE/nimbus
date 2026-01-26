"""Tests for nimbus.tools.websearch module."""

import pytest
from unittest.mock import patch, MagicMock

from nimbus.tools.websearch import (
    web_search,
    WebSearchError,
    _format_results,
    _validate_time_range,
    _perform_search,
)


class TestValidateTimeRange:
    """Tests for time range validation."""

    def test_validate_none(self):
        """Test that None is passed through."""
        assert _validate_time_range(None) is None

    def test_validate_valid_ranges(self):
        """Test that valid ranges are accepted."""
        assert _validate_time_range("d") == "d"
        assert _validate_time_range("w") == "w"
        assert _validate_time_range("m") == "m"
        assert _validate_time_range("y") == "y"

    def test_validate_case_insensitive(self):
        """Test that ranges are case-insensitive."""
        assert _validate_time_range("D") == "d"
        assert _validate_time_range("W") == "w"
        assert _validate_time_range("M") == "m"
        assert _validate_time_range("Y") == "y"

    def test_validate_with_whitespace(self):
        """Test that whitespace is trimmed."""
        assert _validate_time_range("  d  ") == "d"
        assert _validate_time_range(" w ") == "w"

    def test_validate_invalid_range(self):
        """Test that invalid ranges raise ValueError."""
        with pytest.raises(ValueError, match="Invalid time_range"):
            _validate_time_range("x")

        with pytest.raises(ValueError, match="Invalid time_range"):
            _validate_time_range("day")

        with pytest.raises(ValueError, match="Invalid time_range"):
            _validate_time_range("week")


class TestFormatResults:
    """Tests for result formatting."""

    def test_format_empty_results(self):
        """Test formatting of empty results."""
        result = _format_results("test query", [])
        assert 'No results found for "test query"' in result

    def test_format_single_result(self):
        """Test formatting of a single result."""
        results = [
            {
                "title": "Test Title",
                "href": "https://example.com",
                "body": "This is a test snippet.",
            }
        ]
        result = _format_results("test", results)

        assert 'Found 1 results for "test"' in result
        assert "**[Test Title](https://example.com)**" in result
        assert "This is a test snippet." in result
        assert "Source: DuckDuckGo" in result

    def test_format_multiple_results(self):
        """Test formatting of multiple results."""
        results = [
            {
                "title": "First Result",
                "href": "https://first.com",
                "body": "First snippet.",
            },
            {
                "title": "Second Result",
                "href": "https://second.com",
                "body": "Second snippet.",
            },
        ]
        result = _format_results("query", results)

        assert 'Found 2 results for "query"' in result
        assert "1. **[First Result](https://first.com)**" in result
        assert "2. **[Second Result](https://second.com)**" in result
        assert "First snippet." in result
        assert "Second snippet." in result

    def test_format_result_without_url(self):
        """Test formatting result without URL."""
        results = [
            {
                "title": "No URL Title",
                "body": "Some content.",
            }
        ]
        result = _format_results("test", results)

        assert "**No URL Title**" in result
        assert "Some content." in result

    def test_format_result_with_link_key(self):
        """Test formatting result using 'link' key instead of 'href'."""
        results = [
            {
                "title": "Alt Link",
                "link": "https://alt.example.com",
                "snippet": "Alt snippet.",
            }
        ]
        result = _format_results("test", results)

        assert "https://alt.example.com" in result

    def test_format_cleans_whitespace(self):
        """Test that extra whitespace in snippets is cleaned."""
        results = [
            {
                "title": "Test",
                "href": "https://example.com",
                "body": "This   has   extra    whitespace.",
            }
        ]
        result = _format_results("test", results)

        assert "This has extra whitespace." in result


class TestPerformSearch:
    """Tests for the sync search function."""

    def test_search_import_error(self):
        """Test handling of missing duckduckgo-search library."""
        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            # Force reimport to trigger ImportError
            with patch("builtins.__import__", side_effect=ImportError("No module")):
                with pytest.raises(WebSearchError, match="not installed"):
                    _perform_search("test", 10, "wt-wt", None)

    def test_search_rate_limit_error(self):
        """Test handling of rate limit errors."""
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.side_effect = Exception("Ratelimit reached")

        with patch("duckduckgo_search.DDGS", return_value=mock_ddgs):
            with pytest.raises(WebSearchError, match="rate limit"):
                _perform_search("test", 10, "wt-wt", None)

    def test_search_timeout_error(self):
        """Test handling of timeout errors."""
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.side_effect = Exception("Connection timeout")

        with patch("duckduckgo_search.DDGS", return_value=mock_ddgs):
            with pytest.raises(WebSearchError, match="timed out"):
                _perform_search("test", 10, "wt-wt", None)


class TestWebSearch:
    """Tests for the main web_search async function."""

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        """Test that empty query raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await web_search("")

        with pytest.raises(ValueError, match="cannot be empty"):
            await web_search("   ")

    @pytest.mark.asyncio
    async def test_search_basic(self):
        """Test basic search functionality."""
        mock_results = [
            {
                "title": "Python Tutorial",
                "href": "https://python.org/tutorial",
                "body": "Learn Python programming.",
            },
            {
                "title": "Python Documentation",
                "href": "https://docs.python.org",
                "body": "Official Python docs.",
            },
        ]

        with patch("nimbus.tools.websearch._perform_search", return_value=mock_results):
            result = await web_search("python tutorial")

        assert 'Found 2 results for "python tutorial"' in result
        assert "Python Tutorial" in result
        assert "Python Documentation" in result
        assert "https://python.org/tutorial" in result

    @pytest.mark.asyncio
    async def test_search_max_results_clamping(self):
        """Test that max_results is clamped to valid range."""
        mock_results = []

        with patch("nimbus.tools.websearch._perform_search", return_value=mock_results) as mock_search:
            # Test lower bound
            await web_search("test", max_results=0)
            _, kwargs = mock_search.call_args
            # max_results should be clamped to 1
            assert mock_search.call_args[0][1] == 1

            # Test upper bound
            await web_search("test", max_results=100)
            assert mock_search.call_args[0][1] == 20

    @pytest.mark.asyncio
    async def test_search_with_time_range(self):
        """Test search with time range filter."""
        mock_results = []

        with patch("nimbus.tools.websearch._perform_search", return_value=mock_results) as mock_search:
            await web_search("test", time_range="w")

            # Check that time_range was passed
            assert mock_search.call_args[0][3] == "w"

    @pytest.mark.asyncio
    async def test_search_with_region(self):
        """Test search with specific region."""
        mock_results = []

        with patch("nimbus.tools.websearch._perform_search", return_value=mock_results) as mock_search:
            await web_search("test", region="us-en")

            # Check that region was passed
            assert mock_search.call_args[0][2] == "us-en"

    @pytest.mark.asyncio
    async def test_search_invalid_time_range(self):
        """Test that invalid time range raises ValueError."""
        with pytest.raises(ValueError, match="Invalid time_range"):
            await web_search("test", time_range="invalid")

    @pytest.mark.asyncio
    async def test_search_error_handling(self):
        """Test that WebSearchError is propagated."""
        with patch(
            "nimbus.tools.websearch._perform_search",
            side_effect=WebSearchError("Test error"),
        ):
            with pytest.raises(WebSearchError, match="Test error"):
                await web_search("test")

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        """Test handling of empty results."""
        with patch("nimbus.tools.websearch._perform_search", return_value=[]):
            result = await web_search("very obscure query xyz123")

        assert 'No results found for "very obscure query xyz123"' in result

    @pytest.mark.asyncio
    async def test_search_query_trimmed(self):
        """Test that query whitespace is trimmed."""
        mock_results = []

        with patch("nimbus.tools.websearch._perform_search", return_value=mock_results) as mock_search:
            await web_search("  test query  ")

            # Check that query was trimmed
            assert mock_search.call_args[0][0] == "test query"


class TestWebSearchToolDefinition:
    """Tests for tool definition and metadata."""

    def test_tool_definition_attached(self):
        """Test that tool definition is attached to function."""
        assert hasattr(web_search, "_tool_definition")
        defn = web_search._tool_definition

        assert defn.name == "WebSearch"
        assert "DuckDuckGo" in defn.description
        assert "search" in defn.description.lower()

    def test_tool_parameters(self):
        """Test tool parameter definitions."""
        defn = web_search._tool_definition
        param_names = [p.name for p in defn.parameters]

        assert "query" in param_names
        assert "max_results" in param_names
        assert "region" in param_names
        assert "time_range" in param_names

        # Query should be required
        query_param = next(p for p in defn.parameters if p.name == "query")
        assert query_param.required is True

        # Other params should be optional
        max_results_param = next(p for p in defn.parameters if p.name == "max_results")
        assert max_results_param.required is False

        region_param = next(p for p in defn.parameters if p.name == "region")
        assert region_param.required is False

        time_range_param = next(p for p in defn.parameters if p.name == "time_range")
        assert time_range_param.required is False

    def test_time_range_enum(self):
        """Test that time_range has proper enum values."""
        defn = web_search._tool_definition
        time_range_param = next(p for p in defn.parameters if p.name == "time_range")

        assert time_range_param.enum is not None
        assert set(time_range_param.enum) == {"d", "w", "m", "y"}

    def test_tool_not_marked_dangerous(self):
        """Test that WebSearch is not marked as dangerous."""
        defn = web_search._tool_definition
        assert defn.dangerous is False


class TestWebSearchError:
    """Tests for WebSearchError exception."""

    def test_error_message(self):
        """Test error message is set correctly."""
        error = WebSearchError("Test error")
        assert str(error) == "Test error"
        assert error.message == "Test error"
        assert error.original_error is None

    def test_error_with_original(self):
        """Test error with original exception."""
        original = ValueError("Original error")
        error = WebSearchError("Wrapped error", original_error=original)

        assert error.message == "Wrapped error"
        assert error.original_error is original

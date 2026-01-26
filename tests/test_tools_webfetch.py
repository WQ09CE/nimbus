"""Tests for nimbus.tools.webfetch module."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from nimbus.tools.webfetch import (
    web_fetch,
    clear_cache,
    _validate_url,
    _convert_html_to_markdown,
    _extract_content_type,
    _format_response,
    WebFetchCache,
)


class TestValidateUrl:
    """Tests for URL validation and normalization."""

    def test_validate_https_url(self):
        """Test that HTTPS URLs pass through unchanged."""
        url = "https://example.com/path"
        assert _validate_url(url) == url

    def test_validate_http_upgrade_to_https(self):
        """Test that HTTP URLs are upgraded to HTTPS."""
        assert _validate_url("http://example.com") == "https://example.com"
        assert _validate_url("http://example.com/path") == "https://example.com/path"

    def test_validate_no_scheme_assumes_https(self):
        """Test that URLs without scheme get HTTPS added."""
        assert _validate_url("example.com") == "https://example.com"
        assert _validate_url("example.com/path") == "https://example.com/path"

    def test_validate_empty_url_raises(self):
        """Test that empty URL raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_url("")

    def test_validate_unsupported_scheme_raises(self):
        """Test that unsupported schemes raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _validate_url("ftp://example.com")

        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            _validate_url("file:///etc/passwd")

    def test_validate_missing_hostname_raises(self):
        """Test that URLs without hostname raise ValueError."""
        with pytest.raises(ValueError, match="missing hostname"):
            _validate_url("https://")


class TestConvertHtmlToMarkdown:
    """Tests for HTML to Markdown conversion."""

    def test_convert_simple_html(self):
        """Test conversion of simple HTML."""
        html = "<h1>Hello</h1><p>World</p>"
        markdown = _convert_html_to_markdown(html)

        assert "Hello" in markdown
        assert "World" in markdown

    def test_convert_html_with_links(self):
        """Test conversion preserves links."""
        html = '<a href="https://example.com">Click here</a>'
        markdown = _convert_html_to_markdown(html)

        assert "Click here" in markdown
        assert "example.com" in markdown

    def test_convert_html_with_emphasis(self):
        """Test conversion preserves emphasis."""
        html = "<p><strong>Bold</strong> and <em>italic</em></p>"
        markdown = _convert_html_to_markdown(html)

        assert "Bold" in markdown
        assert "italic" in markdown

    def test_convert_removes_excessive_whitespace(self):
        """Test that excessive whitespace is cleaned up."""
        html = "<p>Line 1</p>\n\n\n\n\n<p>Line 2</p>"
        markdown = _convert_html_to_markdown(html)

        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in markdown


class TestExtractContentType:
    """Tests for content type extraction."""

    def test_extract_simple_content_type(self):
        """Test extraction of simple content type."""
        assert _extract_content_type("text/html") == "text/html"
        assert _extract_content_type("application/json") == "application/json"

    def test_extract_content_type_with_charset(self):
        """Test extraction with charset parameter."""
        assert _extract_content_type("text/html; charset=utf-8") == "text/html"
        assert _extract_content_type("application/json; charset=UTF-8") == "application/json"

    def test_extract_content_type_none(self):
        """Test handling of None content type."""
        assert _extract_content_type(None) == "unknown"

    def test_extract_content_type_empty(self):
        """Test handling of empty content type."""
        assert _extract_content_type("") == "unknown"


class TestFormatResponse:
    """Tests for response formatting."""

    def test_format_basic_response(self):
        """Test basic response formatting."""
        result = _format_response(
            url="https://example.com",
            status_code=200,
            content_type="text/html",
            content="Hello World",
            max_length=50000,
        )

        assert "URL: https://example.com" in result
        assert "Status: 200" in result
        assert "Content-Type: text/html" in result
        assert "Hello World" in result

    def test_format_response_with_redirect(self):
        """Test response formatting with redirect info."""
        result = _format_response(
            url="https://new.example.com",
            status_code=200,
            content_type="text/html",
            content="Content",
            max_length=50000,
            redirect_info="Redirected from old.example.com to new.example.com",
        )

        assert "Redirect:" in result
        assert "old.example.com" in result
        assert "new.example.com" in result

    def test_format_response_truncation(self):
        """Test content truncation for long content."""
        long_content = "x" * 1000
        result = _format_response(
            url="https://example.com",
            status_code=200,
            content_type="text/html",
            content=long_content,
            max_length=100,
        )

        assert "[Content truncated at 100 characters]" in result
        # Content portion should be truncated
        assert len(result) < len(long_content) + 500


class TestWebFetchCache:
    """Tests for WebFetch cache functionality."""

    def test_cache_set_and_get(self):
        """Test basic cache set and get."""
        cache = WebFetchCache(ttl=60)
        cache.set("key1", "value1")

        assert cache.get("key1") == "value1"

    def test_cache_miss(self):
        """Test cache miss returns None."""
        cache = WebFetchCache()

        assert cache.get("nonexistent") is None

    def test_cache_expiration(self):
        """Test cache entry expiration."""
        cache = WebFetchCache(ttl=0)  # Immediate expiration
        cache.set("key1", "value1")

        # Should return None because TTL is 0
        import time
        time.sleep(0.01)
        assert cache.get("key1") is None

    def test_cache_clear(self):
        """Test cache clearing."""
        cache = WebFetchCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()

        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_cache_max_size_eviction(self):
        """Test cache evicts entries when max size exceeded."""
        cache = WebFetchCache(ttl=3600, max_size=2)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        # One entry should have been evicted
        assert len([k for k in ["key1", "key2", "key3"] if cache.get(k)]) <= 2


class TestWebFetch:
    """Tests for the main web_fetch function."""

    @pytest.mark.asyncio
    async def test_fetch_html_page(self):
        """Test fetching an HTML page."""
        clear_cache()

        html_content = "<html><body><h1>Test</h1><p>Hello</p></body></html>"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.text = html_content
        mock_response.url = httpx.URL("https://example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await web_fetch("https://example.com")

        assert "URL: https://example.com" in result
        assert "Status: 200" in result
        assert "text/html" in result
        assert "Test" in result
        assert "Hello" in result

    @pytest.mark.asyncio
    async def test_fetch_json_content(self):
        """Test fetching JSON content (no conversion)."""
        clear_cache()

        json_content = '{"key": "value"}'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = json_content
        mock_response.url = httpx.URL("https://api.example.com/data")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await web_fetch("https://api.example.com/data")

        assert "application/json" in result
        assert '{"key": "value"}' in result

    @pytest.mark.asyncio
    async def test_fetch_http_upgrade_to_https(self):
        """Test that HTTP URLs are upgraded to HTTPS."""
        clear_cache()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "content"
        mock_response.url = httpx.URL("https://example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            # Pass HTTP URL
            result = await web_fetch("http://example.com")

            # Verify HTTPS was used in the request
            call_args = mock_client.get.call_args
            assert "https://example.com" in str(call_args)

    @pytest.mark.asyncio
    async def test_fetch_cross_domain_redirect(self):
        """Test detection of cross-domain redirect."""
        clear_cache()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><body>Redirected</body></html>"
        # Different domain in final URL
        mock_response.url = httpx.URL("https://newdomain.com/page")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await web_fetch("https://olddomain.com")

        assert "Redirect:" in result
        assert "olddomain.com" in result
        assert "newdomain.com" in result

    @pytest.mark.asyncio
    async def test_fetch_content_truncation(self):
        """Test content truncation when max_content_length exceeded."""
        clear_cache()

        long_content = "x" * 1000

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = long_content
        mock_response.url = httpx.URL("https://example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            result = await web_fetch("https://example.com", max_content_length=100)

        assert "[Content truncated at 100 characters]" in result

    @pytest.mark.asyncio
    async def test_fetch_uses_cache(self):
        """Test that repeated requests use cache."""
        clear_cache()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "cached content"
        mock_response.url = httpx.URL("https://example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            # First request
            result1 = await web_fetch("https://example.com")
            # Second request (should use cache)
            result2 = await web_fetch("https://example.com")

        # Both results should be the same
        assert result1 == result2
        # HTTP client should only be called once
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_fetch_invalid_url(self):
        """Test that invalid URL raises ValueError."""
        with pytest.raises(ValueError):
            await web_fetch("")

    @pytest.mark.asyncio
    async def test_fetch_timeout(self):
        """Test timeout handling."""
        clear_cache()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(httpx.TimeoutException):
                await web_fetch("https://example.com", timeout=5)

    @pytest.mark.asyncio
    async def test_fetch_network_error(self):
        """Test network error handling."""
        clear_cache()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=httpx.RequestError("Connection failed")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            with pytest.raises(httpx.RequestError):
                await web_fetch("https://example.com")

    @pytest.mark.asyncio
    async def test_clear_cache_function(self):
        """Test the clear_cache utility function."""
        clear_cache()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = "content"
        mock_response.url = httpx.URL("https://example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            # First request
            await web_fetch("https://example.com")
            # Clear cache
            clear_cache()
            # Second request (should NOT use cache)
            await web_fetch("https://example.com")

        # HTTP client should be called twice (cache was cleared)
        assert mock_client.get.call_count == 2


class TestWebFetchToolDefinition:
    """Tests for tool definition and metadata."""

    def test_tool_definition_attached(self):
        """Test that tool definition is attached to function."""
        assert hasattr(web_fetch, "_tool_definition")
        defn = web_fetch._tool_definition

        assert defn.name == "WebFetch"
        assert "URL" in defn.description
        assert "markdown" in defn.description.lower() or "readable" in defn.description.lower()

    def test_tool_parameters(self):
        """Test tool parameter definitions."""
        defn = web_fetch._tool_definition
        param_names = [p.name for p in defn.parameters]

        assert "url" in param_names
        assert "timeout" in param_names
        assert "max_content_length" in param_names

        # URL should be required
        url_param = next(p for p in defn.parameters if p.name == "url")
        assert url_param.required is True

        # timeout and max_content_length should be optional
        timeout_param = next(p for p in defn.parameters if p.name == "timeout")
        assert timeout_param.required is False

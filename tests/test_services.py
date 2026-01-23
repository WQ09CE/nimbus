"""Tests for services module."""

import unittest
from nimbus.services.ingestion import IngestionService
from nimbus.services.retrieval import RetrievalService
from nimbus.domain.models import Source, SourceType


class TestIngestionService(unittest.TestCase):
    """Test cases for IngestionService."""

    def setUp(self):
        """Set up test fixtures."""
        self.ingestion = IngestionService(chunk_size=100, overlap=10)

    def test_process_text_basic(self):
        """Test basic text processing."""
        text = "This is a simple test document."
        source = self.ingestion.process_text(text, "test.txt")

        self.assertEqual(source.title, "test.txt")
        self.assertEqual(source.source_type, SourceType.TEXT)
        self.assertEqual(source.content, text)
        self.assertGreater(len(source.chunks), 0)

    def test_process_text_chunking(self):
        """Test that long text gets chunked."""
        # Create text longer than chunk_size
        text = "This is paragraph one. " * 10 + "\n\n" + "This is paragraph two. " * 10
        source = self.ingestion.process_text(text, "long.txt")

        # Should have multiple chunks
        self.assertGreater(len(source.chunks), 1)

        # Each chunk should have valid fields
        for chunk in source.chunks:
            self.assertTrue(chunk.id.startswith(source.id))
            self.assertEqual(chunk.source_id, source.id)
            self.assertGreater(len(chunk.content), 0)

    def test_process_text_empty(self):
        """Test processing empty text."""
        source = self.ingestion.process_text("", "empty.txt")
        self.assertEqual(len(source.chunks), 0)

    def test_chunk_ids_are_unique(self):
        """Test that chunk IDs are unique."""
        text = "Para 1.\n\nPara 2.\n\nPara 3.\n\nPara 4."
        source = self.ingestion.process_text(text, "test.txt")

        chunk_ids = [c.id for c in source.chunks]
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)))


class TestRetrievalService(unittest.TestCase):
    """Test cases for RetrievalService."""

    def setUp(self):
        """Set up test fixtures."""
        self.retrieval = RetrievalService()
        self.ingestion = IngestionService(chunk_size=100)

        # Create test sources
        self.source1 = self.ingestion.process_text(
            "Python is a programming language. It is easy to learn.",
            "python.txt"
        )
        self.source2 = self.ingestion.process_text(
            "JavaScript runs in browsers. It is used for web development.",
            "javascript.txt"
        )

    def test_add_and_get_source(self):
        """Test adding and retrieving sources."""
        self.retrieval.add_source(self.source1)

        retrieved = self.retrieval.get_source(self.source1.id)
        self.assertEqual(retrieved.title, "python.txt")

    def test_remove_source(self):
        """Test removing sources."""
        self.retrieval.add_source(self.source1)
        self.retrieval.add_source(self.source2)

        removed = self.retrieval.remove_source(self.source1.id)
        self.assertEqual(removed.title, "python.txt")
        self.assertEqual(self.retrieval.get_source_count(), 1)

    def test_search_basic(self):
        """Test basic search functionality."""
        self.retrieval.add_source(self.source1)
        self.retrieval.add_source(self.source2)

        # Search for Python
        results = self.retrieval.search("Python programming", top_k=2)
        self.assertGreater(len(results), 0)

        # First result should be from python.txt
        chunk, score = results[0]
        self.assertEqual(chunk.source_id, self.source1.id)

    def test_search_with_source_filter(self):
        """Test search with source ID filter."""
        self.retrieval.add_source(self.source1)
        self.retrieval.add_source(self.source2)

        # Search only in source2
        results = self.retrieval.search(
            "programming language",
            top_k=2,
            source_ids=[self.source2.id]
        )

        # All results should be from source2
        for chunk, score in results:
            self.assertEqual(chunk.source_id, self.source2.id)

    def test_search_no_results(self):
        """Test search with no matching content."""
        self.retrieval.add_source(self.source1)

        results = self.retrieval.search("quantum mechanics", top_k=2)
        self.assertEqual(len(results), 0)

    def test_get_citations(self):
        """Test citation generation."""
        self.retrieval.add_source(self.source1)

        results = self.retrieval.search("Python", top_k=1)
        chunks = [chunk for chunk, score in results]

        citations = self.retrieval.get_citations(chunks)
        self.assertEqual(len(citations), len(chunks))

        for citation in citations:
            self.assertEqual(citation.source_title, "python.txt")
            self.assertGreater(len(citation.text), 0)

    def test_clear(self):
        """Test clearing all sources."""
        self.retrieval.add_source(self.source1)
        self.retrieval.add_source(self.source2)

        self.retrieval.clear()
        self.assertEqual(self.retrieval.get_source_count(), 0)

    def test_get_total_chunks(self):
        """Test total chunk count."""
        self.retrieval.add_source(self.source1)
        self.retrieval.add_source(self.source2)

        total = self.retrieval.get_total_chunks()
        expected = len(self.source1.chunks) + len(self.source2.chunks)
        self.assertEqual(total, expected)


if __name__ == "__main__":
    unittest.main()

"""Tests for vector store module."""

import pytest
import uuid
from nimbus.core.vector_store import (
    Document,
    SearchResult,
    VectorStore,
    ChromaVectorStore,
    EmbeddingClient,
    OllamaEmbeddingClient,
    MockEmbeddingClient,
)


class TestDocument:
    """Test cases for Document dataclass."""

    def test_document_creation_minimal(self):
        """Test creating document with minimal fields."""
        doc = Document(id="doc1", content="Hello world")
        assert doc.id == "doc1"
        assert doc.content == "Hello world"
        assert doc.embedding is None
        assert doc.metadata == {}

    def test_document_creation_full(self):
        """Test creating document with all fields."""
        embedding = [0.1, 0.2, 0.3]
        metadata = {"source": "test", "page": 1}
        doc = Document(
            id="doc1",
            content="Hello world",
            embedding=embedding,
            metadata=metadata
        )
        assert doc.id == "doc1"
        assert doc.content == "Hello world"
        assert doc.embedding == embedding
        assert doc.metadata == metadata


class TestSearchResult:
    """Test cases for SearchResult dataclass."""

    def test_search_result_creation(self):
        """Test creating search result."""
        doc = Document(id="doc1", content="Test content")
        result = SearchResult(document=doc, score=0.95)
        assert result.document.id == "doc1"
        assert result.score == 0.95


class TestMockEmbeddingClient:
    """Test cases for MockEmbeddingClient."""

    def test_dimension(self):
        """Test dimension property."""
        client = MockEmbeddingClient(dimension=512)
        assert client.dimension == 512

    @pytest.mark.asyncio
    async def test_embed_single(self):
        """Test embedding single text."""
        client = MockEmbeddingClient(dimension=768)
        embeddings = await client.embed(["Hello world"])

        assert len(embeddings) == 1
        assert len(embeddings[0]) == 768

    @pytest.mark.asyncio
    async def test_embed_multiple(self):
        """Test embedding multiple texts."""
        client = MockEmbeddingClient(dimension=768)
        texts = ["First text", "Second text", "Third text"]
        embeddings = await client.embed(texts)

        assert len(embeddings) == 3
        for emb in embeddings:
            assert len(emb) == 768

    @pytest.mark.asyncio
    async def test_embed_deterministic(self):
        """Test that embeddings are deterministic."""
        client = MockEmbeddingClient(dimension=768)
        text = "Test text for determinism"

        emb1 = await client.embed([text])
        emb2 = await client.embed([text])

        assert emb1[0] == emb2[0]

    @pytest.mark.asyncio
    async def test_embed_empty_list(self):
        """Test embedding empty list."""
        client = MockEmbeddingClient(dimension=768)
        embeddings = await client.embed([])
        assert embeddings == []

    @pytest.mark.asyncio
    async def test_embed_different_texts_different_embeddings(self):
        """Test that different texts produce different embeddings."""
        client = MockEmbeddingClient(dimension=768)

        emb1 = await client.embed(["Hello"])
        emb2 = await client.embed(["World"])

        assert emb1[0] != emb2[0]


class TestChromaVectorStore:
    """Test cases for ChromaVectorStore."""

    @pytest.fixture
    def store(self):
        """Create in-memory ChromaDB store for testing with unique collection name."""
        try:
            # Use unique collection name to avoid conflicts between tests
            collection_name = f"test_collection_{uuid.uuid4().hex[:8]}"
            return ChromaVectorStore(
                collection_name=collection_name,
                persist_directory=None
            )
        except ImportError:
            pytest.skip("chromadb not installed")

    @pytest.fixture
    def mock_embedding_client(self):
        """Create mock embedding client."""
        return MockEmbeddingClient(dimension=384)

    @pytest.mark.asyncio
    async def test_add_and_get_document(self, store):
        """Test adding and retrieving a document."""
        doc = Document(
            id="doc1",
            content="Python is a programming language",
            embedding=[0.1] * 384,
            metadata={"source": "test"}
        )

        ids = await store.add_documents([doc])
        assert ids == ["doc1"]

        retrieved = await store.get("doc1")
        assert retrieved is not None
        assert retrieved.id == "doc1"
        assert retrieved.content == "Python is a programming language"
        assert retrieved.metadata["source"] == "test"

    @pytest.mark.asyncio
    async def test_add_multiple_documents(self, store):
        """Test adding multiple documents."""
        docs = [
            Document(id="1", content="First doc", embedding=[0.1] * 384),
            Document(id="2", content="Second doc", embedding=[0.2] * 384),
            Document(id="3", content="Third doc", embedding=[0.3] * 384),
        ]

        ids = await store.add_documents(docs)
        assert len(ids) == 3

        count = await store.count()
        assert count == 3

    @pytest.mark.asyncio
    async def test_search(self, store, mock_embedding_client):
        """Test vector similarity search."""
        # Add documents with embeddings
        texts = [
            "Python is a programming language",
            "JavaScript is used for web development",
            "Machine learning uses algorithms",
        ]

        embeddings = await mock_embedding_client.embed(texts)

        docs = [
            Document(id=f"doc{i}", content=text, embedding=emb)
            for i, (text, emb) in enumerate(zip(texts, embeddings))
        ]

        await store.add_documents(docs)

        # Search with query similar to first document
        query_emb = await mock_embedding_client.embed(["Python programming"])
        results = await store.search(query_emb[0], top_k=2)

        assert len(results) <= 2
        assert all(isinstance(r, SearchResult) for r in results)
        assert all(0 <= r.score <= 1 for r in results)

    @pytest.mark.asyncio
    async def test_search_with_filter(self, store, mock_embedding_client):
        """Test search with metadata filter."""
        # Add documents with different categories
        texts = ["Doc about Python", "Doc about Java", "Another Python doc"]
        embeddings = await mock_embedding_client.embed(texts)

        docs = [
            Document(
                id="1",
                content=texts[0],
                embedding=embeddings[0],
                metadata={"category": "python"}
            ),
            Document(
                id="2",
                content=texts[1],
                embedding=embeddings[1],
                metadata={"category": "java"}
            ),
            Document(
                id="3",
                content=texts[2],
                embedding=embeddings[2],
                metadata={"category": "python"}
            ),
        ]

        await store.add_documents(docs)

        # Search with filter
        query_emb = await mock_embedding_client.embed(["programming"])
        results = await store.search(
            query_emb[0],
            top_k=3,
            filter={"category": "python"}
        )

        # All results should be Python category
        for r in results:
            assert r.document.metadata.get("category") == "python"

    @pytest.mark.asyncio
    async def test_delete_documents(self, store):
        """Test deleting documents."""
        docs = [
            Document(id="1", content="First", embedding=[0.1] * 384),
            Document(id="2", content="Second", embedding=[0.2] * 384),
            Document(id="3", content="Third", embedding=[0.3] * 384),
        ]

        await store.add_documents(docs)
        assert await store.count() == 3

        deleted = await store.delete(["1", "2"])
        assert deleted == 2

        assert await store.count() == 1

        # Verify deleted docs are gone
        assert await store.get("1") is None
        assert await store.get("2") is None
        assert await store.get("3") is not None

    @pytest.mark.asyncio
    async def test_clear(self, store):
        """Test clearing all documents."""
        docs = [
            Document(id="1", content="First", embedding=[0.1] * 384),
            Document(id="2", content="Second", embedding=[0.2] * 384),
        ]

        await store.add_documents(docs)
        assert await store.count() == 2

        deleted = await store.clear()
        assert deleted == 2
        assert await store.count() == 0

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store):
        """Test getting non-existent document."""
        result = await store.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_add_empty_list(self, store):
        """Test adding empty document list."""
        ids = await store.add_documents([])
        assert ids == []

    @pytest.mark.asyncio
    async def test_delete_empty_list(self, store):
        """Test deleting empty list."""
        deleted = await store.delete([])
        assert deleted == 0


class TestVectorStoreIntegration:
    """Integration tests for VectorStore with services."""

    @pytest.fixture
    def store(self):
        """Create in-memory store with unique collection name."""
        try:
            collection_name = f"integration_test_{uuid.uuid4().hex[:8]}"
            return ChromaVectorStore(
                collection_name=collection_name,
                persist_directory=None
            )
        except ImportError:
            pytest.skip("chromadb not installed")

    @pytest.fixture
    def embedding_client(self):
        """Create mock embedding client."""
        return MockEmbeddingClient(dimension=384)

    @pytest.mark.asyncio
    async def test_ingestion_and_retrieval_flow(self, store, embedding_client):
        """Test complete ingestion and retrieval flow."""
        from nimbus.services.ingestion import IngestionService
        from nimbus.services.retrieval import RetrievalService

        # Create services with vector store
        ingestion = IngestionService(
            chunk_size=100,
            vector_store=store,
            embedding_client=embedding_client
        )

        retrieval = RetrievalService(
            vector_store=store,
            embedding_client=embedding_client
        )

        # Ingest a document
        content = """Python is a high-level programming language.

        It is known for its simplicity and readability.

        Python is widely used in data science and machine learning."""

        chunk_count = await ingestion.ingest_document(content, "source_1")
        assert chunk_count > 0

        # Search for related content
        results = await retrieval.search_async("programming language", top_k=2)
        assert len(results) > 0

        # Results should be (Chunk, score) tuples
        for chunk, score in results:
            assert hasattr(chunk, "content")
            assert 0 <= score <= 1


class TestOllamaEmbeddingClient:
    """Test cases for OllamaEmbeddingClient (basic tests without server)."""

    def test_initialization(self):
        """Test client initialization."""
        client = OllamaEmbeddingClient(
            model="nomic-embed-text",
            base_url="http://localhost:11434"
        )
        assert client.model == "nomic-embed-text"
        assert client.base_url == "http://localhost:11434"

    def test_dimension_known_model(self):
        """Test dimension for known model."""
        client = OllamaEmbeddingClient(model="nomic-embed-text")
        assert client.dimension == 768

    def test_dimension_unknown_model(self):
        """Test dimension defaults to 768 for unknown model."""
        client = OllamaEmbeddingClient(model="unknown-model")
        assert client.dimension == 768


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

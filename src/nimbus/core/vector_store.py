"""Vector store abstraction for Nimbus.

Provides an abstract interface for vector storage and similarity search,
with a ChromaDB implementation for MVP.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import uuid


@dataclass
class Document:
    """Document data structure for vector storage.

    Attributes:
        id: Unique document identifier.
        content: Text content of the document.
        embedding: Optional vector embedding (list of floats).
        metadata: Optional metadata dictionary.
    """
    id: str
    content: str
    embedding: Optional[List[float]] = None
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Search result with document and similarity score.

    Attributes:
        document: The matched document.
        score: Similarity score (0-1, higher is more similar).
    """
    document: Document
    score: float


class VectorStore(ABC):
    """Abstract interface for vector storage backends.

    This interface supports CRUD operations and similarity search.
    Implementations can use different backends (ChromaDB, SurrealDB, etc.).
    """

    @abstractmethod
    async def add_documents(self, documents: List[Document]) -> List[str]:
        """Add documents to the vector store.

        Args:
            documents: List of Document objects to add.
                       Each document should have content and optionally embedding.

        Returns:
            List of document IDs that were added.
        """
        pass

    @abstractmethod
    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        filter: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """Search for similar documents by vector similarity.

        Args:
            query_embedding: Query vector for similarity search.
            top_k: Maximum number of results to return.
            filter: Optional metadata filter (implementation-specific).

        Returns:
            List of SearchResult objects sorted by similarity (descending).
        """
        pass

    @abstractmethod
    async def delete(self, document_ids: List[str]) -> int:
        """Delete documents by their IDs.

        Args:
            document_ids: List of document IDs to delete.

        Returns:
            Number of documents actually deleted.
        """
        pass

    @abstractmethod
    async def get(self, document_id: str) -> Optional[Document]:
        """Get a single document by ID.

        Args:
            document_id: The document ID to retrieve.

        Returns:
            Document if found, None otherwise.
        """
        pass

    @abstractmethod
    async def clear(self) -> int:
        """Clear all documents from the store.

        Returns:
            Number of documents deleted.
        """
        pass

    @abstractmethod
    async def count(self) -> int:
        """Get the total number of documents.

        Returns:
            Document count.
        """
        pass


class ChromaVectorStore(VectorStore):
    """ChromaDB implementation of VectorStore.

    Uses ChromaDB for vector storage and similarity search.
    Supports both in-memory mode (for testing) and persistent mode.

    Example:
        # In-memory mode (for testing)
        store = ChromaVectorStore(collection_name="test")

        # Persistent mode
        store = ChromaVectorStore(
            collection_name="my_docs",
            persist_directory="/path/to/data"
        )
    """

    def __init__(
        self,
        collection_name: str = "nimbus_default",
        persist_directory: Optional[str] = None
    ):
        """Initialize ChromaDB vector store.

        Args:
            collection_name: Name of the ChromaDB collection.
            persist_directory: Directory for persistence. None = in-memory mode.
        """
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise ImportError(
                "chromadb is required for ChromaVectorStore. "
                "Install it with: pip install chromadb>=0.4.0"
            )

        self.collection_name = collection_name
        self.persist_directory = persist_directory

        # Initialize ChromaDB client
        if persist_directory:
            self._client = chromadb.PersistentClient(
                path=persist_directory,
                settings=Settings(anonymized_telemetry=False)
            )
        else:
            self._client = chromadb.Client(
                Settings(anonymized_telemetry=False)
            )

        # Get or create collection
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}  # Use cosine similarity
        )

    async def add_documents(self, documents: List[Document]) -> List[str]:
        """Add documents to ChromaDB.

        Args:
            documents: List of Document objects.

        Returns:
            List of document IDs added.
        """
        if not documents:
            return []

        ids = []
        embeddings = []
        contents = []
        metadatas = []

        for doc in documents:
            doc_id = doc.id or str(uuid.uuid4())
            ids.append(doc_id)
            contents.append(doc.content)
            # ChromaDB requires non-empty metadata, so add a placeholder if empty
            metadata = doc.metadata or {}
            if not metadata:
                metadata = {"_placeholder": "true"}
            metadatas.append(metadata)

            if doc.embedding is not None:
                embeddings.append(doc.embedding)

        # Add to collection
        if embeddings and len(embeddings) == len(documents):
            self._collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=contents,
                metadatas=metadatas
            )
        else:
            # Add without embeddings (ChromaDB can generate them with default model)
            self._collection.add(
                ids=ids,
                documents=contents,
                metadatas=metadatas
            )

        return ids

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        filter: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """Search for similar documents.

        Args:
            query_embedding: Query vector.
            top_k: Number of results to return.
            filter: Optional metadata filter (ChromaDB where clause).

        Returns:
            List of SearchResult objects.
        """
        # Build query kwargs
        query_kwargs = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances", "embeddings"]
        }

        if filter:
            query_kwargs["where"] = filter

        results = self._collection.query(**query_kwargs)

        search_results = []

        if results and results["ids"] and results["ids"][0]:
            ids = results["ids"][0]
            documents = results["documents"][0] if results["documents"] else [None] * len(ids)
            metadatas = results["metadatas"][0] if results["metadatas"] else [{}] * len(ids)
            distances = results["distances"][0] if results["distances"] else [0.0] * len(ids)
            result_embeddings = results.get("embeddings")
            if result_embeddings is not None and len(result_embeddings) > 0:
                embeddings_list = result_embeddings[0]
            else:
                embeddings_list = [None] * len(ids)

            for i, doc_id in enumerate(ids):
                # Convert distance to similarity score (cosine distance to similarity)
                # ChromaDB returns L2 distance for cosine space, which is sqrt(2 - 2*cos_sim)
                # So cos_sim = 1 - (distance^2 / 2)
                # But for simplicity, we just normalize to [0, 1] range
                distance = distances[i] if distances[i] is not None else 0.0
                # Cosine distance in ChromaDB is 1 - cosine_similarity
                # So similarity = 1 - distance, but clamp to [0, 1]
                score = max(0.0, min(1.0, 1.0 - distance))

                # Get embedding for this document, convert numpy array to list if needed
                emb = embeddings_list[i] if embeddings_list[i] is not None else None
                if emb is not None and hasattr(emb, 'tolist'):
                    emb = emb.tolist()

                # Clean up metadata - remove placeholder
                meta = metadatas[i] if metadatas[i] else {}
                if meta.get("_placeholder") == "true":
                    meta = {k: v for k, v in meta.items() if k != "_placeholder"}

                doc = Document(
                    id=doc_id,
                    content=documents[i] if documents[i] else "",
                    embedding=emb,
                    metadata=meta
                )
                search_results.append(SearchResult(document=doc, score=score))

        return search_results

    async def delete(self, document_ids: List[str]) -> int:
        """Delete documents by ID.

        Args:
            document_ids: List of document IDs to delete.

        Returns:
            Number of documents deleted.
        """
        if not document_ids:
            return 0

        # Get count before deletion
        try:
            existing = self._collection.get(ids=document_ids)
            count_before = len(existing["ids"]) if existing and existing["ids"] else 0
        except Exception:
            count_before = len(document_ids)

        # Delete documents
        self._collection.delete(ids=document_ids)

        return count_before

    async def get(self, document_id: str) -> Optional[Document]:
        """Get a document by ID.

        Args:
            document_id: The document ID.

        Returns:
            Document if found, None otherwise.
        """
        try:
            results = self._collection.get(
                ids=[document_id],
                include=["documents", "metadatas", "embeddings"]
            )

            if results and results["ids"] and len(results["ids"]) > 0:
                # Get embedding and convert numpy array to list if needed
                emb = None
                result_embeddings = results.get("embeddings")
                if result_embeddings is not None:
                    # ChromaDB returns embeddings as numpy array, check shape
                    try:
                        if hasattr(result_embeddings, 'shape'):
                            # It's a numpy array
                            if result_embeddings.shape[0] > 0:
                                emb = result_embeddings[0]
                                if hasattr(emb, 'tolist'):
                                    emb = emb.tolist()
                        elif isinstance(result_embeddings, list) and len(result_embeddings) > 0:
                            emb = result_embeddings[0]
                            if hasattr(emb, 'tolist'):
                                emb = emb.tolist()
                    except (IndexError, TypeError):
                        emb = None

                # Clean up metadata - remove placeholder
                meta = results["metadatas"][0] if results["metadatas"] else {}
                if meta.get("_placeholder") == "true":
                    meta = {k: v for k, v in meta.items() if k != "_placeholder"}

                return Document(
                    id=results["ids"][0],
                    content=results["documents"][0] if results["documents"] else "",
                    embedding=emb,
                    metadata=meta
                )
        except Exception:
            pass

        return None

    async def clear(self) -> int:
        """Clear all documents from the collection.

        Returns:
            Number of documents deleted.
        """
        count = await self.count()

        # Delete and recreate collection
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        return count

    async def count(self) -> int:
        """Get the document count.

        Returns:
            Number of documents in the collection.
        """
        return self._collection.count()


class EmbeddingClient(ABC):
    """Abstract interface for embedding model clients.

    Implementations can use different embedding providers
    (Ollama, OpenAI, HuggingFace, etc.).
    """

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (one per input text).
        """
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the embedding vector dimension.

        Returns:
            Dimension of the embedding vectors.
        """
        pass


class OllamaEmbeddingClient(EmbeddingClient):
    """Ollama embedding client implementation.

    Uses Ollama's local embedding models for generating vectors.
    Default model is nomic-embed-text (768 dimensions).

    Example:
        client = OllamaEmbeddingClient(model="nomic-embed-text")
        embeddings = await client.embed(["Hello world", "How are you?"])
    """

    # Known embedding dimensions for common models
    _MODEL_DIMENSIONS = {
        "nomic-embed-text": 768,
        "mxbai-embed-large": 1024,
        "all-minilm": 384,
        "snowflake-arctic-embed": 1024,
    }

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434"
    ):
        """Initialize Ollama embedding client.

        Args:
            model: Ollama embedding model name.
            base_url: Ollama API base URL.
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._dimension = self._MODEL_DIMENSIONS.get(model, 768)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using Ollama.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        if not texts:
            return []

        try:
            import httpx
        except ImportError:
            raise ImportError(
                "httpx is required for OllamaEmbeddingClient. "
                "Install it with: pip install httpx"
            )

        embeddings = []

        async with httpx.AsyncClient(timeout=60.0) as client:
            for text in texts:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text}
                )
                response.raise_for_status()
                data = response.json()
                embeddings.append(data["embedding"])

                # Update dimension from actual response
                if embeddings and not self._dimension:
                    self._dimension = len(embeddings[0])

        return embeddings

    @property
    def dimension(self) -> int:
        """Return the embedding dimension.

        Returns:
            Embedding vector dimension.
        """
        return self._dimension


class MockEmbeddingClient(EmbeddingClient):
    """Mock embedding client for testing.

    Generates deterministic embeddings based on text hash.
    Useful for unit tests without requiring actual embedding models.
    """

    def __init__(self, dimension: int = 768):
        """Initialize mock client.

        Args:
            dimension: Dimension of generated embeddings.
        """
        self._dimension = dimension

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate mock embeddings.

        Args:
            texts: List of texts.

        Returns:
            List of deterministic embedding vectors.
        """
        embeddings = []
        for text in texts:
            # Generate deterministic embedding from text hash
            import hashlib
            hash_bytes = hashlib.sha256(text.encode()).digest()

            # Generate embedding values from hash bytes
            embedding = []
            for i in range(self._dimension):
                # Use hash bytes cyclically
                byte_val = hash_bytes[i % len(hash_bytes)]
                # Normalize to [-1, 1]
                embedding.append((byte_val / 127.5) - 1.0)

            # Normalize to unit vector
            magnitude = sum(x**2 for x in embedding) ** 0.5
            if magnitude > 0:
                embedding = [x / magnitude for x in embedding]

            embeddings.append(embedding)

        return embeddings

    @property
    def dimension(self) -> int:
        """Return the mock dimension."""
        return self._dimension

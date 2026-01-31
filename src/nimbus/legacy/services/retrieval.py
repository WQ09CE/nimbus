"""RAG retrieval service for Nimbus.

Supports both keyword-based search (MVP) and vector similarity search.
"""

from typing import List, Tuple, Optional, TYPE_CHECKING

from ..domain.models import Source, Chunk, Citation

if TYPE_CHECKING:
    from ..core.vector_store import VectorStore, EmbeddingClient


class RetrievalService:
    """RAG retrieval service with keyword-based and vector search.

    In keyword mode (default), uses simple BM25-like keyword matching.
    In vector mode, uses embedding similarity search via VectorStore.

    Example:
        # Keyword mode (MVP)
        service = RetrievalService()

        # Vector mode
        from nimbus.core.vector_store import ChromaVectorStore, OllamaEmbeddingClient
        vector_store = ChromaVectorStore()
        embedding_client = OllamaEmbeddingClient()
        service = RetrievalService(
            vector_store=vector_store,
            embedding_client=embedding_client
        )
    """

    def __init__(
        self,
        vector_store: Optional["VectorStore"] = None,
        embedding_client: Optional["EmbeddingClient"] = None
    ):
        """Initialize retrieval service.

        Args:
            vector_store: Optional VectorStore for vector search mode.
            embedding_client: Optional EmbeddingClient for generating query embeddings.
        """
        self.sources: dict[str, Source] = {}
        self.vector_store = vector_store
        self.embedding_client = embedding_client
        self._use_vector_search = vector_store is not None and embedding_client is not None

    def add_source(self, source: Source) -> None:
        """Add a source to the index.

        Args:
            source: Source object to index.
        """
        self.sources[source.id] = source

    def remove_source(self, source_id: str) -> Optional[Source]:
        """Remove a source from the index.

        Args:
            source_id: ID of source to remove.

        Returns:
            Removed Source, or None if not found.
        """
        return self.sources.pop(source_id, None)

    def get_source(self, source_id: str) -> Optional[Source]:
        """Get a source by ID.

        Args:
            source_id: ID of source to retrieve.

        Returns:
            Source object, or None if not found.
        """
        return self.sources.get(source_id)

    def search(
        self,
        query: str,
        top_k: int = 3,
        source_ids: Optional[List[str]] = None
    ) -> List[Tuple[Chunk, float]]:
        """Search for relevant chunks (synchronous interface).

        Uses keyword matching for backward compatibility.
        For vector search, use search_async().

        Args:
            query: Search query.
            top_k: Maximum number of results to return.
            source_ids: Optional list of source IDs to search within.

        Returns:
            List of (Chunk, score) tuples, sorted by relevance.
        """
        return self._keyword_search(query, top_k, source_ids)

    async def search_async(
        self,
        query: str,
        top_k: int = 5,
        source_ids: Optional[List[str]] = None
    ) -> List[Tuple[Chunk, float]]:
        """Search for relevant chunks using vector similarity.

        Falls back to keyword search if vector store is not configured.

        Args:
            query: Search query string.
            top_k: Maximum number of results to return.
            source_ids: Optional list of source IDs to filter by.

        Returns:
            List of (Chunk, score) tuples, sorted by relevance (descending).
        """
        if not self._use_vector_search:
            return self._keyword_search(query, top_k, source_ids)

        # Vector search mode
        assert self.embedding_client is not None
        assert self.vector_store is not None

        # Generate query embedding
        query_embeddings = await self.embedding_client.embed([query])
        query_embedding = query_embeddings[0]

        # Build metadata filter
        filter_dict = None
        if source_ids:
            filter_dict = {"source_id": {"$in": source_ids}}

        # Search vector store
        results = await self.vector_store.search(
            query_embedding=query_embedding,
            top_k=top_k,
            filter=filter_dict
        )

        # Convert to (Chunk, score) format for compatibility
        chunk_results = []
        for result in results:
            doc = result.document
            metadata = doc.metadata or {}

            chunk = Chunk(
                id=doc.id,
                source_id=metadata.get("source_id", ""),
                content=doc.content,
                index=metadata.get("chunk_index", 0),
                metadata=metadata
            )
            chunk_results.append((chunk, result.score))

        return chunk_results

    def _keyword_search(
        self,
        query: str,
        top_k: int = 3,
        source_ids: Optional[List[str]] = None
    ) -> List[Tuple[Chunk, float]]:
        """Keyword-based search (BM25-like).

        MVP implementation uses simple keyword matching.

        Args:
            query: Search query.
            top_k: Maximum number of results to return.
            source_ids: Optional list of source IDs to search within.

        Returns:
            List of (Chunk, score) tuples, sorted by relevance.
        """
        results = []
        query_terms = set(query.lower().split())

        # Determine which sources to search
        if source_ids:
            search_sources = [
                self.sources[sid] for sid in source_ids
                if sid in self.sources
            ]
        else:
            search_sources = list(self.sources.values())

        for source in search_sources:
            for chunk in source.chunks:
                # Simple keyword matching score
                chunk_terms = set(chunk.content.lower().split())
                overlap = len(query_terms & chunk_terms)
                if overlap > 0:
                    # Normalize by query length
                    score = overlap / len(query_terms)
                    results.append((chunk, score))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_citations(self, chunks: List[Chunk]) -> List[Citation]:
        """Generate citations from chunks.

        Args:
            chunks: List of chunks to cite.

        Returns:
            List of Citation objects.
        """
        citations = []
        for chunk in chunks:
            source = self.sources.get(chunk.source_id)
            if source:
                # Truncate long text for citation
                text = chunk.content
                if len(text) > 200:
                    text = text[:200] + "..."

                citations.append(Citation(
                    chunk_id=chunk.id,
                    source_id=source.id,
                    source_title=source.title,
                    text=text
                ))
        return citations

    def clear(self) -> None:
        """Clear all indexed sources."""
        self.sources.clear()

    def get_source_count(self) -> int:
        """Get the number of indexed sources."""
        return len(self.sources)

    def get_total_chunks(self) -> int:
        """Get the total number of chunks across all sources."""
        return sum(len(s.chunks) for s in self.sources.values())

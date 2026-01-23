"""Document ingestion service for Nimbus.

Supports document processing, chunking, and optional vectorization.
"""

import uuid
import re
from typing import List, Optional, TYPE_CHECKING

from ..domain.models import Source, Chunk, SourceType

if TYPE_CHECKING:
    from ..core.vector_store import VectorStore, EmbeddingClient, Document


class IngestionService:
    """Document ingestion and chunking service.

    Supports two modes:
    1. Basic mode: Process documents into chunks (no vector embeddings)
    2. Vector mode: Process documents and store embeddings in VectorStore

    Example:
        # Basic mode
        service = IngestionService()
        source = service.process_text("Hello world", "test.txt")

        # Vector mode
        from nimbus.core.vector_store import ChromaVectorStore, OllamaEmbeddingClient
        vector_store = ChromaVectorStore()
        embedding_client = OllamaEmbeddingClient()
        service = IngestionService(
            vector_store=vector_store,
            embedding_client=embedding_client
        )
        chunk_count = await service.ingest_document("Hello world", "source_1")
    """

    def __init__(
        self,
        chunk_size: int = 500,
        overlap: int = 50,
        vector_store: Optional["VectorStore"] = None,
        embedding_client: Optional["EmbeddingClient"] = None
    ):
        """Initialize ingestion service.

        Args:
            chunk_size: Target size for each chunk in characters.
            overlap: Number of characters to overlap between chunks.
            vector_store: Optional VectorStore for vector mode.
            embedding_client: Optional EmbeddingClient for generating embeddings.
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.vector_store = vector_store
        self.embedding_client = embedding_client
        self._use_vectorization = vector_store is not None and embedding_client is not None

    def process_text(self, text: str, title: str = "Untitled") -> Source:
        """Process plain text into a Source with chunks.

        Args:
            text: Plain text content.
            title: Title for the source.

        Returns:
            Source object with chunks.
        """
        source_id = str(uuid.uuid4())[:8]
        chunks = self._split_text(text, source_id)
        return Source(
            id=source_id,
            title=title,
            source_type=SourceType.TEXT,
            content=text,
            chunks=chunks
        )

    def process_file(self, file_path: str) -> Source:
        """Process a file into a Source with chunks.

        MVP: Supports .txt and .pdf files.

        Args:
            file_path: Path to the file.

        Returns:
            Source object with chunks.
        """
        # Detect file type
        if file_path.endswith('.pdf'):
            text = self._extract_pdf(file_path)
            source_type = SourceType.PDF
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            source_type = SourceType.TEXT

        title = file_path.split('/')[-1]
        source_id = str(uuid.uuid4())[:8]
        chunks = self._split_text(text, source_id)

        return Source(
            id=source_id,
            title=title,
            source_type=source_type,
            content=text,
            chunks=chunks,
            metadata={"file_path": file_path}
        )

    async def ingest_document(
        self,
        content: str,
        source_id: str,
        metadata: Optional[dict] = None
    ) -> int:
        """Ingest a document with vectorization.

        Splits content into chunks, generates embeddings, and stores
        in the vector store. Requires vector_store and embedding_client.

        Args:
            content: Document text content.
            source_id: Unique identifier for the source document.
            metadata: Optional additional metadata for all chunks.

        Returns:
            Number of chunks created and stored.

        Raises:
            RuntimeError: If vector_store or embedding_client not configured.
        """
        if not self._use_vectorization:
            raise RuntimeError(
                "Vector ingestion requires vector_store and embedding_client. "
                "Initialize IngestionService with these parameters for vector mode."
            )

        from ..core.vector_store import Document

        # Split text into chunks
        text_chunks = self._split_text_simple(content)

        if not text_chunks:
            return 0

        # Generate embeddings for all chunks
        assert self.embedding_client is not None
        embeddings = await self.embedding_client.embed(text_chunks)

        # Create Document objects
        documents = []
        for i, (chunk_text, embedding) in enumerate(zip(text_chunks, embeddings)):
            chunk_metadata = {
                "source_id": source_id,
                "chunk_index": i,
                **(metadata or {})
            }
            doc = Document(
                id=f"{source_id}_chunk_{i}",
                content=chunk_text,
                embedding=embedding,
                metadata=chunk_metadata
            )
            documents.append(doc)

        # Store in vector store
        assert self.vector_store is not None
        await self.vector_store.add_documents(documents)

        return len(documents)

    async def ingest_source(self, source: Source) -> int:
        """Ingest a Source object with vectorization.

        Convenience method that takes a Source and ingests its content.

        Args:
            source: Source object to ingest.

        Returns:
            Number of chunks created and stored.
        """
        return await self.ingest_document(
            content=source.content,
            source_id=source.id,
            metadata={
                "title": source.title,
                "source_type": source.source_type.value
            }
        )

    def _extract_pdf(self, file_path: str) -> str:
        """Extract text from PDF file.

        Args:
            file_path: Path to PDF file.

        Returns:
            Extracted text content.
        """
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text
        except ImportError:
            # Fallback if pypdf is not installed
            return f"[PDF content from {file_path} - install pypdf for extraction]"

    def _split_text(self, text: str, source_id: str) -> List[Chunk]:
        """Split text into chunks.

        Uses paragraph-based splitting, then combines paragraphs to reach
        target chunk size.

        Args:
            text: Text to split.
            source_id: ID of the source document.

        Returns:
            List of Chunk objects.
        """
        # Split by paragraph
        paragraphs = re.split(r'\n\s*\n', text)
        chunks = []
        current = ""
        chunk_idx = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) < self.chunk_size:
                current += "\n\n" + para if current else para
            else:
                if current:
                    chunks.append(Chunk(
                        id=f"{source_id}_c{chunk_idx}",
                        source_id=source_id,
                        content=current.strip(),
                        index=chunk_idx
                    ))
                    chunk_idx += 1
                current = para

        # Don't forget the last chunk
        if current:
            chunks.append(Chunk(
                id=f"{source_id}_c{chunk_idx}",
                source_id=source_id,
                content=current.strip(),
                index=chunk_idx
            ))

        return chunks

    def _split_text_simple(self, text: str) -> List[str]:
        """Split text into chunk strings (without Chunk objects).

        Used for vectorization where we need simple strings.

        Args:
            text: Text to split.

        Returns:
            List of chunk strings.
        """
        # Split by paragraph
        paragraphs = re.split(r'\n\s*\n', text)
        chunks = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) < self.chunk_size:
                current += "\n\n" + para if current else para
            else:
                if current:
                    chunks.append(current.strip())
                current = para

        # Don't forget the last chunk
        if current:
            chunks.append(current.strip())

        return chunks

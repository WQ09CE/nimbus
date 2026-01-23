"""RAG skill for OpenNotebook."""

from typing import Any, Dict, List, Optional

from ..domain.models import Citation
from ..services.retrieval import RetrievalService


class RAGResult:
    """Result from RAG query."""

    def __init__(self, answer: str, citations: List[Citation]):
        """Initialize RAG result.

        Args:
            answer: Generated answer text.
            citations: List of citations supporting the answer.
        """
        self.answer = answer
        self.citations = citations

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "answer": self.answer,
            "citations": [c.model_dump() for c in self.citations]
        }

    def format_with_refs(self) -> str:
        """Format answer with reference citations.

        Returns:
            Formatted string with answer and references.
        """
        if not self.citations:
            return self.answer

        refs = "\n\n---\nReferences:\n"
        for i, c in enumerate(self.citations, 1):
            # Truncate citation text
            text = c.text[:100] + "..." if len(c.text) > 100 else c.text
            refs += f"[{i}] {c.source_title}: \"{text}\"\n"
        return self.answer + refs


def create_rag_skill(retrieval: RetrievalService, llm_client):
    """Create a RAG skill function.

    Args:
        retrieval: RetrievalService instance for searching.
        llm_client: LLM client with async complete() method.

    Returns:
        Async function for document-based Q&A.
    """

    async def rag_search(
        query: str,
        source_ids: Optional[List[str]] = None
    ) -> str:
        """Answer questions based on indexed documents.

        Args:
            query: User's question.
            source_ids: Optional list of source IDs to search within.

        Returns:
            Answer with citations formatted as string.
        """
        # 1. Retrieve relevant chunks
        results = retrieval.search(query, top_k=3, source_ids=source_ids)

        if not results:
            return "No relevant content found in the documents."

        # 2. Build context from chunks
        context_parts = []
        chunks = []
        for chunk, score in results:
            context_parts.append(f"[Source: {chunk.source_id}]\n{chunk.content}")
            chunks.append(chunk)

        context = "\n\n---\n\n".join(context_parts)

        # 3. Generate answer using LLM
        prompt = f"""Based on the following document content, answer the question.
Please cite the sources in your answer using [Source: source_id] format.

Document content:
{context}

Question: {query}

Please answer in Chinese and mark references with [Source: source_id] for relevant information."""

        answer = await llm_client.complete(prompt)

        # 4. Generate citations
        citations = retrieval.get_citations(chunks)

        result = RAGResult(answer, citations)
        return result.format_with_refs()

    return rag_search

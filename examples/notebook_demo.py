"""Complete Notebook demo with RAG and artifact generation."""

import asyncio
import aiohttp


class OllamaClient:
    """Ollama LLM client for local inference."""

    def __init__(self, model: str = "gemma3n", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    async def complete(self, prompt: str) -> str:
        """Call Ollama API for completion."""
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 1024,
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Ollama error: {resp.status} - {text}")
                data = await resp.json()
                return data.get("response", "")


# Sample document content for demo
SAMPLE_DOCUMENT = """
# Python Programming Guide

## Chapter 1: Introduction to Python

Python is a high-level, interpreted programming language known for its simplicity
and readability. Created by Guido van Rossum and first released in 1991, Python
has become one of the most popular programming languages in the world.

Key features of Python include:
- Easy to learn and read syntax
- Dynamic typing and automatic memory management
- Extensive standard library
- Support for multiple programming paradigms (procedural, OOP, functional)
- Large and active community

## Chapter 2: Data Types and Variables

Python supports several built-in data types:

### Numeric Types
- int: Integer numbers (e.g., 42, -17)
- float: Floating-point numbers (e.g., 3.14, -0.001)
- complex: Complex numbers (e.g., 3+4j)

### Sequence Types
- str: String (text) data
- list: Ordered, mutable collections
- tuple: Ordered, immutable collections

### Mapping Type
- dict: Key-value pair collections

### Set Types
- set: Unordered collections of unique elements
- frozenset: Immutable version of set

## Chapter 3: Control Flow

Python provides several control flow statements:

### Conditional Statements
```python
if condition:
    # do something
elif other_condition:
    # do something else
else:
    # default action
```

### Loops
```python
# For loop
for item in iterable:
    process(item)

# While loop
while condition:
    do_something()
```

## Chapter 4: Functions

Functions are defined using the `def` keyword:

```python
def greet(name, greeting="Hello"):
    return f"{greeting}, {name}!"
```

Key concepts:
- Parameters and arguments
- Default values
- Return statements
- Lambda expressions
- Decorators

## Chapter 5: Object-Oriented Programming

Python supports OOP with classes and inheritance:

```python
class Animal:
    def __init__(self, name):
        self.name = name

    def speak(self):
        raise NotImplementedError

class Dog(Animal):
    def speak(self):
        return f"{self.name} says Woof!"
```
"""

SAMPLE_DOCUMENT_2 = """
# Machine Learning Fundamentals

## Overview

Machine Learning (ML) is a subset of artificial intelligence that enables systems
to learn and improve from experience without being explicitly programmed.

## Types of Machine Learning

### Supervised Learning
Learning from labeled data. Examples include:
- Classification: Spam detection, image recognition
- Regression: Price prediction, weather forecasting

### Unsupervised Learning
Finding patterns in unlabeled data. Examples include:
- Clustering: Customer segmentation
- Dimensionality reduction: PCA, t-SNE

### Reinforcement Learning
Learning through trial and error with rewards/penalties.
Used in robotics, game playing, and autonomous systems.

## Common Algorithms

1. Linear Regression
2. Decision Trees
3. Random Forest
4. Support Vector Machines
5. Neural Networks
6. K-Means Clustering
7. Principal Component Analysis

## Best Practices

- Start with simple models
- Use cross-validation
- Handle missing data appropriately
- Feature engineering is crucial
- Monitor for overfitting
"""


async def demo_ingestion():
    """Demo: Document ingestion and chunking."""
    from nimbus.services import IngestionService

    print("\n" + "=" * 60)
    print("Part 1: Document Ingestion")
    print("=" * 60)

    ingestion = IngestionService(chunk_size=500, overlap=50)

    # Process sample documents
    source1 = ingestion.process_text(SAMPLE_DOCUMENT, "Python Guide")
    source2 = ingestion.process_text(SAMPLE_DOCUMENT_2, "ML Fundamentals")

    print(f"\n[Ingestion] Processed '{source1.title}':")
    print(f"  - Source ID: {source1.id}")
    print(f"  - Content length: {len(source1.content)} chars")
    print(f"  - Chunks created: {len(source1.chunks)}")

    print(f"\n[Ingestion] Processed '{source2.title}':")
    print(f"  - Source ID: {source2.id}")
    print(f"  - Content length: {len(source2.content)} chars")
    print(f"  - Chunks created: {len(source2.chunks)}")

    # Show chunk preview
    print("\n[Preview] First chunk of Python Guide:")
    print(f"  {source1.chunks[0].content[:200]}...")

    return source1, source2


async def demo_retrieval(source1, source2):
    """Demo: RAG retrieval."""
    from nimbus.services import RetrievalService

    print("\n" + "=" * 60)
    print("Part 2: Document Retrieval")
    print("=" * 60)

    retrieval = RetrievalService()
    retrieval.add_source(source1)
    retrieval.add_source(source2)

    print(f"\n[Retrieval] Indexed {retrieval.get_source_count()} sources")
    print(f"[Retrieval] Total chunks: {retrieval.get_total_chunks()}")

    # Search examples
    queries = [
        "What are Python data types?",
        "machine learning algorithms",
        "object oriented programming",
    ]

    for query in queries:
        print(f"\n[Search] Query: '{query}'")
        results = retrieval.search(query, top_k=2)
        for chunk, score in results:
            print(f"  - Score: {score:.2f} | Chunk: {chunk.content[:80]}...")

    return retrieval


async def demo_notebook_agent(source1, source2, retrieval):
    """Demo: Full notebook agent with RAG."""
    from nimbus.core import NotebookAgent
    from nimbus.domain import NotebookContext

    print("\n" + "=" * 60)
    print("Part 3: Notebook Agent with RAG")
    print("=" * 60)

    # Initialize agent
    llm = OllamaClient(model="gemma3n")
    agent = NotebookAgent(llm_client=llm)

    # Set up notebook context
    context = NotebookContext(
        notebook_id="demo-notebook",
        title="Programming Study Notes"
    )
    agent.set_notebook_context(context)

    # Set up retrieval and register skills
    agent.setup_retrieval(retrieval)
    agent.register_notebook_skills()

    # Add sources
    agent.add_source(source1)
    agent.add_source(source2)

    print(f"\n[Agent] Notebook: {agent.notebook_context.title}")
    print(f"[Agent] Sources: {len(agent.notebook_context.sources)}")
    print(f"[Agent] Active sources: {agent.notebook_context.active_source_ids}")
    print(f"[Agent] Available skills: {agent.executor.get_skill_names()}")

    # RAG Q&A
    print("\n--- RAG Q&A ---")

    questions = [
        "What are the key features of Python?",
        "Explain the types of machine learning.",
    ]

    for question in questions:
        print(f"\n[User] {question}")
        response = await agent.run(question)
        # Truncate long responses
        text = response.text
        if len(text) > 500:
            text = text[:500] + "...\n(truncated)"
        print(f"[Agent] {text}")

    return agent


async def demo_draft_skills(agent):
    """Demo: Artifact generation skills."""
    print("\n" + "=" * 60)
    print("Part 4: Draft/Artifact Generation")
    print("=" * 60)

    # Generate outline
    print("\n--- Generate Outline ---")
    print("[User] Generate an outline for learning Python")
    response = await agent.run("Generate an outline for learning Python")
    text = response.text
    if len(text) > 600:
        text = text[:600] + "...\n(truncated)"
    print(f"[Agent] {text}")

    # Generate summary
    print("\n--- Generate Summary ---")
    print("[User] Summarize the machine learning document")
    response = await agent.run("Summarize the machine learning fundamentals document")
    text = response.text
    if len(text) > 500:
        text = text[:500] + "...\n(truncated)"
    print(f"[Agent] {text}")


async def demo_source_management():
    """Demo: Source management (add/remove/activate)."""
    from nimbus.core import NotebookAgent
    from nimbus.services import IngestionService, RetrievalService

    print("\n" + "=" * 60)
    print("Part 5: Source Management")
    print("=" * 60)

    llm = OllamaClient(model="gemma3n")
    agent = NotebookAgent(llm_client=llm)
    ingestion = IngestionService()
    retrieval = RetrievalService()

    agent.setup_retrieval(retrieval)
    agent.register_notebook_skills()

    # Add multiple sources
    source1 = ingestion.process_text("Content about Python basics.", "python.txt")
    source2 = ingestion.process_text("Content about JavaScript.", "javascript.txt")
    source3 = ingestion.process_text("Content about Rust language.", "rust.txt")

    agent.add_source(source1)
    agent.add_source(source2)
    agent.add_source(source3)

    print(f"\n[Management] Added {len(agent.notebook_context.sources)} sources")
    for src in agent.notebook_context.sources:
        print(f"  - {src.title} (ID: {src.id})")

    # Deactivate some sources
    print(f"\n[Management] Activating only Python and Rust sources...")
    agent.set_active_sources([source1.id, source3.id])
    print(f"  Active: {agent.notebook_context.active_source_ids}")

    # Remove a source
    print(f"\n[Management] Removing JavaScript source...")
    removed = agent.remove_source(source2.id)
    print(f"  Removed: {removed}")
    print(f"  Remaining sources: {[s.title for s in agent.notebook_context.sources]}")


async def main():
    """Run all demos."""
    print("=" * 60)
    print("OpenNotebook - AI Notebook Demo")
    print("=" * 60)
    print("\nThis demo showcases the 5-layer architecture:")
    print("  1. Domain: Source, Note, NotebookContext")
    print("  2. Services: Ingestion, Retrieval")
    print("  3. Skills: RAG, Draft")
    print("  4. Core: NotebookAgent")
    print("  5. Examples: This demo")

    try:
        # Part 1 & 2: Ingestion and Retrieval
        source1, source2 = await demo_ingestion()
        retrieval = await demo_retrieval(source1, source2)

        # Part 3 & 4: Notebook Agent and Skills
        agent = await demo_notebook_agent(source1, source2, retrieval)
        await demo_draft_skills(agent)

        # Part 5: Source Management
        await demo_source_management()

        print("\n" + "=" * 60)
        print("Demo completed successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\n[Error] {e}")
        print("\nNote: This demo requires Ollama running with gemma3n model.")
        print("Start Ollama with: ollama run gemma3n")
        raise


if __name__ == "__main__":
    asyncio.run(main())

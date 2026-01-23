"""Integration tests for NotebookAgent end-to-end flows."""

import pytest
import asyncio
from typing import Any, Dict

from nimbus.core.agent import NotebookAgent
from nimbus.core.types import (
    TaskDAG,
    TaskStatus,
    RuntimeConfig,
    Artifact,
    ArtifactType,
    NotebookResponse,
)
from nimbus.core.planner import LLMClient


class MockLLMClient(LLMClient):
    """Mock LLM client for testing."""

    def __init__(self, responses: Dict[str, str] = None):
        """Initialize with predefined responses.

        Args:
            responses: Dict mapping prompt substrings to responses.
        """
        self.responses = responses or {}
        self.default_response = '{"mode": "direct", "response": "Hello!"}'
        self.call_count = 0
        self.last_prompt = None

    async def complete(self, prompt: str) -> str:
        """Return predefined response based on prompt content."""
        self.call_count += 1
        self.last_prompt = prompt

        for key, response in self.responses.items():
            if key in prompt:
                return response

        return self.default_response


class TestAgentIntegration:
    """Agent end-to-end integration tests."""

    @pytest.fixture
    def mock_llm(self):
        """Create a basic mock LLM."""
        return MockLLMClient()

    @pytest.fixture
    def dag_llm(self):
        """Create mock LLM that returns DAG plans."""
        responses = {
            "search": '''{
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "search", "params": {"query": "test"}, "depends_on": []}
                ]
            }''',
        }
        return MockLLMClient(responses)

    @pytest.fixture
    def multi_task_dag_llm(self):
        """Create mock LLM for multi-task DAG tests."""
        responses = {
            "multi": '''{
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "search", "params": {"query": "A"}, "depends_on": []},
                    {"id": "t2", "skill": "search", "params": {"query": "B"}, "depends_on": []},
                    {"id": "t3", "skill": "summarize", "params": {"text": ""}, "depends_on": ["t1", "t2"]}
                ]
            }''',
        }
        return MockLLMClient(responses)

    @pytest.mark.asyncio
    async def test_simple_chat_flow(self, mock_llm):
        """Test simple conversation flow end-to-end."""
        agent = NotebookAgent(
            llm_client=mock_llm,
            memory_type="simple",
            planner_type="simple",
            enable_logging=False,
        )

        response = await agent.run("Hello!")

        assert isinstance(response, NotebookResponse)
        assert response.text is not None
        assert not response.is_error()
        assert response.memory_stats is not None

    @pytest.mark.asyncio
    async def test_dag_mode_single_task(self, dag_llm):
        """Test DAG mode with single task."""
        # Create mock search skill
        async def mock_search(query: str) -> str:
            return f"Search results for: {query}"

        agent = NotebookAgent(
            llm_client=dag_llm,
            memory_type="simple",
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("search", mock_search)

        response = await agent.run("search for test")

        assert isinstance(response, NotebookResponse)
        assert not response.is_error()
        assert "test" in response.text.lower() or "search" in response.text.lower()

    @pytest.mark.asyncio
    async def test_multi_task_dag_flow(self, multi_task_dag_llm):
        """Test multi-task DAG execution with dependencies."""
        execution_order = []

        async def mock_search(query: str) -> str:
            execution_order.append(f"search:{query}")
            await asyncio.sleep(0.01)
            return f"Results for {query}"

        async def mock_summarize(text: str = "") -> str:
            execution_order.append("summarize")
            await asyncio.sleep(0.01)
            return "Summary of results"

        agent = NotebookAgent(
            llm_client=multi_task_dag_llm,
            memory_type="simple",
            planner_type="dag",
            runtime_config=RuntimeConfig(max_concurrent=10),
            enable_logging=False,
        )
        agent.register_skill("search", mock_search)
        agent.register_skill("summarize", mock_summarize)

        response = await agent.run("multi task test")

        assert isinstance(response, NotebookResponse)
        assert not response.is_error()

        # Verify summarize ran after searches
        assert "summarize" in execution_order
        summarize_idx = execution_order.index("summarize")
        assert summarize_idx == len(execution_order) - 1  # summarize should be last

    @pytest.mark.asyncio
    async def test_artifact_generation(self, mock_llm):
        """Test artifact generation from task results."""
        # Create skill that returns artifact data
        async def artifact_skill(**kwargs) -> Dict[str, Any]:
            return {
                "artifact_type": "chart",
                "id": "chart_001",
                "title": "Test Chart",
                "data": {"type": "bar", "values": [1, 2, 3]},
                "metadata": {"source": "test"},
            }

        # Create DAG LLM that routes to artifact skill
        dag_llm = MockLLMClient({
            "chart": '''{
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "artifact_skill", "params": {}, "depends_on": []}
                ]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            memory_type="simple",
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("artifact_skill", artifact_skill)

        response = await agent.run("generate chart")

        assert isinstance(response, NotebookResponse)
        assert response.has_artifacts()
        assert len(response.artifacts) == 1

        artifact = response.artifacts[0]
        assert artifact.type == ArtifactType.CHART
        assert artifact.title == "Test Chart"
        assert artifact.data == {"type": "bar", "values": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_error_recovery(self, mock_llm):
        """Test graceful error handling and recovery."""
        call_count = [0]

        async def failing_skill(**kwargs) -> str:
            call_count[0] += 1
            if call_count[0] < 3:  # Fail first 2 times
                raise ValueError("Temporary failure")
            return "Success after retry"

        dag_llm = MockLLMClient({
            "retry": '''{
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "failing_skill", "params": {}, "depends_on": []}
                ]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            memory_type="simple",
            planner_type="dag",
            runtime_config=RuntimeConfig(max_retries=3, retry_delay=0.01),
            enable_logging=False,
        )
        agent.register_skill("failing_skill", failing_skill)

        response = await agent.run("retry test")

        assert isinstance(response, NotebookResponse)
        # Should succeed after retries
        assert not response.is_error()
        assert "Success" in response.text

    @pytest.mark.asyncio
    async def test_partial_failure_graceful_degradation(self, mock_llm):
        """Test graceful degradation when some tasks fail."""
        async def success_skill(query: str) -> str:
            return f"Success: {query}"

        async def fail_skill(**kwargs) -> str:
            raise ValueError("Always fails")

        dag_llm = MockLLMClient({
            "partial": '''{
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "success_skill", "params": {"query": "A"}, "depends_on": []},
                    {"id": "t2", "skill": "fail_skill", "params": {}, "depends_on": []}
                ]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            memory_type="simple",
            planner_type="dag",
            runtime_config=RuntimeConfig(max_retries=0),
            enable_logging=False,
        )
        agent.register_skill("success_skill", success_skill)
        agent.register_skill("fail_skill", fail_skill)

        response = await agent.run("partial failure test")

        assert isinstance(response, NotebookResponse)
        # Should have partial results
        assert "Success" in response.text or "could not be completed" in response.text

    @pytest.mark.asyncio
    async def test_empty_dag_handling(self, mock_llm):
        """Test handling of empty DAG."""
        # Create LLM that returns empty tasks
        empty_dag_llm = MockLLMClient({
            "empty": '{"mode": "dag", "tasks": []}'
        })

        agent = NotebookAgent(
            llm_client=empty_dag_llm,
            memory_type="simple",
            planner_type="dag",
            enable_logging=False,
        )

        response = await agent.run("empty dag test")

        assert isinstance(response, NotebookResponse)
        # Should handle gracefully
        assert response.text is not None

    @pytest.mark.asyncio
    async def test_suggestions_generation(self, dag_llm):
        """Test follow-up suggestions are generated."""
        async def mock_search(query: str) -> str:
            return f"Results for {query}"

        agent = NotebookAgent(
            llm_client=dag_llm,
            memory_type="simple",
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("search", mock_search)

        response = await agent.run("search for information")

        assert isinstance(response, NotebookResponse)
        assert isinstance(response.suggestions, list)
        # Should have suggestions after search
        if len(response.suggestions) > 0:
            assert any("summarize" in s.lower() or "search" in s.lower()
                      for s in response.suggestions)

    @pytest.mark.asyncio
    async def test_response_truncation(self, mock_llm):
        """Test long response truncation."""
        async def verbose_skill(**kwargs) -> str:
            # Return very long response
            return "A" * 100000

        dag_llm = MockLLMClient({
            "verbose": '''{
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "verbose_skill", "params": {}, "depends_on": []}
                ]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            memory_type="simple",
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("verbose_skill", verbose_skill)

        response = await agent.run("verbose test")

        assert isinstance(response, NotebookResponse)
        # Should be truncated
        assert len(response.text) <= 51000  # 50000 + truncation message
        assert "truncated" in response.text.lower()

    @pytest.mark.asyncio
    async def test_streaming_execution(self, dag_llm):
        """Test streaming execution with status updates."""
        async def mock_search(query: str) -> str:
            await asyncio.sleep(0.01)
            return f"Results for {query}"

        agent = NotebookAgent(
            llm_client=dag_llm,
            memory_type="simple",
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("search", mock_search)

        events = []
        async for event in agent.run_stream("search test"):
            events.append(event)

        event_types = [e["type"] for e in events]

        # Should have proper event sequence
        assert "status" in event_types or "planning" in event_types
        assert "complete" in event_types

    @pytest.mark.asyncio
    async def test_memory_stats_included(self, mock_llm):
        """Test memory stats are included in response."""
        agent = NotebookAgent(
            llm_client=mock_llm,
            memory_type="simple",
            planner_type="simple",
            enable_logging=False,
        )

        response = await agent.run("Test message")

        assert response.memory_stats is not None
        assert "type" in response.memory_stats
        assert response.memory_stats["type"] == "simple"

    @pytest.mark.asyncio
    async def test_tiered_memory_integration(self, mock_llm):
        """Test integration with tiered memory."""
        agent = NotebookAgent(
            llm_client=mock_llm,
            memory_type="tiered",
            planner_type="simple",
            enable_logging=False,
        )

        response = await agent.run("Test with tiered memory")

        assert response.memory_stats is not None
        assert response.memory_stats["type"] == "tiered"
        assert "pinned_tokens" in response.memory_stats


class TestArtifactTypes:
    """Tests for different artifact types."""

    @pytest.mark.asyncio
    async def test_file_artifact(self):
        """Test file-type artifact creation."""
        async def file_skill(**kwargs) -> Dict[str, Any]:
            return {
                "artifact_type": "file",
                "id": "doc_001",
                "title": "Report.pdf",
                "data": b"PDF content",
                "mime_type": "application/pdf",
                "url": "/downloads/report.pdf",
            }

        dag_llm = MockLLMClient({
            "file": '''{
                "mode": "dag",
                "tasks": [{"id": "t1", "skill": "file_skill", "params": {}, "depends_on": []}]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("file_skill", file_skill)

        response = await agent.run("generate file")

        assert response.has_artifacts()
        artifact = response.artifacts[0]
        assert artifact.type == ArtifactType.FILE
        assert artifact.mime_type == "application/pdf"
        assert artifact.url == "/downloads/report.pdf"

    @pytest.mark.asyncio
    async def test_code_artifact(self):
        """Test code-type artifact creation."""
        async def code_skill(**kwargs) -> Dict[str, Any]:
            return {
                "artifact_type": "code",
                "id": "code_001",
                "title": "Example Code",
                "data": "def hello():\n    print('Hello')",
                "metadata": {"language": "python"},
            }

        dag_llm = MockLLMClient({
            "code": '''{
                "mode": "dag",
                "tasks": [{"id": "t1", "skill": "code_skill", "params": {}, "depends_on": []}]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("code_skill", code_skill)

        response = await agent.run("generate code")

        assert response.has_artifacts()
        artifact = response.artifacts[0]
        assert artifact.type == ArtifactType.CODE
        assert "def hello" in artifact.data
        assert artifact.metadata.get("language") == "python"

    @pytest.mark.asyncio
    async def test_table_artifact(self):
        """Test table-type artifact creation."""
        async def table_skill(**kwargs) -> Dict[str, Any]:
            return {
                "artifact_type": "table",
                "id": "table_001",
                "title": "Data Table",
                "data": {
                    "headers": ["Name", "Value"],
                    "rows": [["A", 1], ["B", 2]],
                },
            }

        dag_llm = MockLLMClient({
            "table": '''{
                "mode": "dag",
                "tasks": [{"id": "t1", "skill": "table_skill", "params": {}, "depends_on": []}]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("table_skill", table_skill)

        response = await agent.run("generate table")

        assert response.has_artifacts()
        artifact = response.artifacts[0]
        assert artifact.type == ArtifactType.TABLE
        assert "headers" in artifact.data
        assert "rows" in artifact.data

    @pytest.mark.asyncio
    async def test_multiple_artifacts_from_single_task(self):
        """Test task returning multiple artifacts."""
        async def multi_artifact_skill(**kwargs) -> Dict[str, Any]:
            return {
                "text": "Generated multiple outputs",
                "artifacts": [
                    {
                        "artifact_type": "chart",
                        "id": "chart_1",
                        "title": "Chart 1",
                        "data": {"type": "pie"},
                    },
                    {
                        "artifact_type": "table",
                        "id": "table_1",
                        "title": "Table 1",
                        "data": {"rows": []},
                    },
                ],
            }

        dag_llm = MockLLMClient({
            "multi": '''{
                "mode": "dag",
                "tasks": [{"id": "t1", "skill": "multi_artifact_skill", "params": {}, "depends_on": []}]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("multi_artifact_skill", multi_artifact_skill)

        response = await agent.run("generate multi artifacts")

        assert response.has_artifacts()
        assert len(response.artifacts) == 2

        types = {a.type for a in response.artifacts}
        assert ArtifactType.CHART in types
        assert ArtifactType.TABLE in types


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_all_tasks_fail(self):
        """Test handling when all tasks fail."""
        async def always_fail(**kwargs) -> str:
            raise ValueError("Always fails")

        dag_llm = MockLLMClient({
            "fail": '''{
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "always_fail", "params": {}, "depends_on": []},
                    {"id": "t2", "skill": "always_fail", "params": {}, "depends_on": []}
                ]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            planner_type="dag",
            runtime_config=RuntimeConfig(max_retries=0),
            enable_logging=False,
        )
        agent.register_skill("always_fail", always_fail)

        response = await agent.run("all fail test")

        assert isinstance(response, NotebookResponse)
        # Should contain error information
        assert "error" in response.text.lower() or response.is_error()

    @pytest.mark.asyncio
    async def test_unknown_skill_in_dag(self):
        """Test handling of unknown skill in DAG."""
        dag_llm = MockLLMClient({
            "unknown": '''{
                "mode": "dag",
                "tasks": [
                    {"id": "t1", "skill": "nonexistent_skill", "params": {}, "depends_on": []}
                ]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            planner_type="dag",
            runtime_config=RuntimeConfig(max_retries=0),
            enable_logging=False,
        )

        response = await agent.run("unknown skill test")

        # Should handle gracefully
        assert isinstance(response, NotebookResponse)

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Test task timeout handling."""
        async def slow_skill(**kwargs) -> str:
            await asyncio.sleep(10)  # Very slow
            return "Done"

        dag_llm = MockLLMClient({
            "slow": '''{
                "mode": "dag",
                "tasks": [{"id": "t1", "skill": "slow_skill", "params": {}, "depends_on": []}]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            planner_type="dag",
            runtime_config=RuntimeConfig(default_timeout=0.1, max_retries=0),
            enable_logging=False,
        )
        agent.register_skill("slow_skill", slow_skill)

        response = await agent.run("timeout test")

        # Should handle timeout gracefully
        assert isinstance(response, NotebookResponse)
        assert "timeout" in response.text.lower() or "error" in response.text.lower()

    @pytest.mark.asyncio
    async def test_response_serialization(self):
        """Test NotebookResponse serialization."""
        async def artifact_skill(**kwargs) -> Dict[str, Any]:
            return {
                "artifact_type": "markdown",
                "id": "md_001",
                "title": "Notes",
                "data": "# Hello\nWorld",
            }

        dag_llm = MockLLMClient({
            "serialize": '''{
                "mode": "dag",
                "tasks": [{"id": "t1", "skill": "artifact_skill", "params": {}, "depends_on": []}]
            }'''
        })

        agent = NotebookAgent(
            llm_client=dag_llm,
            planner_type="dag",
            enable_logging=False,
        )
        agent.register_skill("artifact_skill", artifact_skill)

        response = await agent.run("serialize test")

        # Test to_dict serialization
        response_dict = response.to_dict()

        assert "text" in response_dict
        assert "artifacts" in response_dict
        assert "suggestions" in response_dict
        assert "memory_stats" in response_dict

        # Verify artifacts are properly serialized
        if response_dict["artifacts"]:
            artifact_dict = response_dict["artifacts"][0]
            assert "id" in artifact_dict
            assert "type" in artifact_dict
            assert artifact_dict["type"] == "markdown"

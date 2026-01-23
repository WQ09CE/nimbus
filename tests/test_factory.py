"""Tests for AgentFactory, AgentConfig, and Re-planning."""

import pytest
import tempfile
from pathlib import Path

from nimbus.core.config import (
    AgentConfig,
    LLMConfig,
    MemoryConfigSpec,
    RuntimeConfigSpec,
    SkillConfig,
    SkillType,
)
from nimbus.core.factory import AgentFactory, MockLLMClient, create_agent
from nimbus.core.planner import (
    AdaptivePlanner,
    ReplanRequest,
    ReplanningStrategy,
    DAGPlanner,
)
from nimbus.core.types import TaskDAG, TaskNode, TaskStatus


class TestLLMConfig:
    """Tests for LLMConfig dataclass."""

    def test_default_values(self):
        """Test default LLMConfig values."""
        config = LLMConfig()

        assert config.model == "claude-3-5-sonnet"
        assert config.temperature == 0.7
        assert config.max_tokens == 4096
        assert config.api_key_env == "ANTHROPIC_API_KEY"
        assert config.base_url is None

    def test_from_dict(self):
        """Test creating LLMConfig from dictionary."""
        data = {
            "model": "gpt-4",
            "temperature": 0.5,
            "max_tokens": 2048,
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com",
        }

        config = LLMConfig.from_dict(data)

        assert config.model == "gpt-4"
        assert config.temperature == 0.5
        assert config.max_tokens == 2048
        assert config.api_key_env == "OPENAI_API_KEY"
        assert config.base_url == "https://api.openai.com"

    def test_from_dict_partial(self):
        """Test creating LLMConfig with partial data."""
        data = {"model": "custom-model"}

        config = LLMConfig.from_dict(data)

        assert config.model == "custom-model"
        assert config.temperature == 0.7  # default
        assert config.max_tokens == 4096  # default


class TestMemoryConfigSpec:
    """Tests for MemoryConfigSpec dataclass."""

    def test_default_values(self):
        """Test default MemoryConfigSpec values."""
        config = MemoryConfigSpec()

        assert config.type == "simple"
        assert config.working_budget == 4000
        assert config.episodic_budget == 8000

    def test_from_dict_with_working_memory_budget(self):
        """Test creating MemoryConfigSpec with working_memory_budget alias."""
        data = {
            "type": "tiered",
            "working_memory_budget": 6000,  # alias
            "episodic_budget": 10000,
        }

        config = MemoryConfigSpec.from_dict(data)

        assert config.type == "tiered"
        assert config.working_budget == 6000
        assert config.episodic_budget == 10000

    def test_to_memory_config(self):
        """Test conversion to core.memory.MemoryConfig."""
        spec = MemoryConfigSpec(
            working_budget=5000,
            episodic_budget=9000,
        )

        memory_config = spec.to_memory_config()

        assert memory_config.working_budget == 5000
        assert memory_config.episodic_budget == 9000


class TestRuntimeConfigSpec:
    """Tests for RuntimeConfigSpec dataclass."""

    def test_default_values(self):
        """Test default RuntimeConfigSpec values."""
        config = RuntimeConfigSpec()

        assert config.default_timeout == 30.0
        assert config.max_retries == 2
        assert config.max_concurrent == 10

    def test_to_runtime_config(self):
        """Test conversion to core.types.RuntimeConfig."""
        spec = RuntimeConfigSpec(
            default_timeout=60.0,
            max_retries=3,
        )

        runtime_config = spec.to_runtime_config()

        assert runtime_config.default_timeout == 60.0
        assert runtime_config.max_retries == 3


class TestSkillConfig:
    """Tests for SkillConfig dataclass."""

    def test_default_values(self):
        """Test default SkillConfig values."""
        config = SkillConfig(name="test")

        assert config.name == "test"
        assert config.type == "builtin"
        assert config.path is None
        assert config.enabled is True

    def test_from_dict(self):
        """Test creating SkillConfig from dictionary."""
        data = {
            "name": "custom_skill",
            "type": "markdown",
            "path": "/path/to/skill.md",
            "params": {"key": "value"},
            "enabled": False,
        }

        config = SkillConfig.from_dict(data)

        assert config.name == "custom_skill"
        assert config.type == "markdown"
        assert config.path == "/path/to/skill.md"
        assert config.params == {"key": "value"}
        assert config.enabled is False


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self):
        """Test default AgentConfig values."""
        config = AgentConfig(name="Test Agent")

        assert config.name == "Test Agent"
        assert config.version == "1.0.0"
        assert config.planner_type == "dag"
        assert config.enable_logging is True

    def test_from_dict(self):
        """Test creating AgentConfig from dictionary."""
        data = {
            "name": "My Agent",
            "version": "2.0.0",
            "llm": {"model": "gpt-4", "temperature": 0.5},
            "memory": {"type": "tiered", "working_memory_budget": 5000},
            "runtime": {"default_timeout": 60},
            "skills": [
                {"name": "chat", "type": "builtin"},
                {"name": "search", "type": "builtin"},
            ],
            "system_prompt": "You are helpful.",
            "planner_type": "simple",
        }

        config = AgentConfig.from_dict(data)

        assert config.name == "My Agent"
        assert config.version == "2.0.0"
        assert config.llm.model == "gpt-4"
        assert config.llm.temperature == 0.5
        assert config.memory.type == "tiered"
        assert config.memory.working_budget == 5000
        assert config.runtime.default_timeout == 60
        assert len(config.skills) == 2
        assert config.skills[0].name == "chat"
        assert config.system_prompt == "You are helpful."
        assert config.planner_type == "simple"

    def test_from_yaml(self):
        """Test loading AgentConfig from YAML file."""
        yaml_content = """
name: "YAML Agent"
version: "1.5.0"
llm:
  model: "claude-3-5-sonnet"
  temperature: 0.8
memory:
  type: "simple"
skills:
  - name: "chat"
    type: "builtin"
system_prompt: "YAML system prompt"
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()

            config = AgentConfig.from_yaml(f.name)

            assert config.name == "YAML Agent"
            assert config.version == "1.5.0"
            assert config.llm.temperature == 0.8
            assert config.memory.type == "simple"
            assert len(config.skills) == 1
            assert config.system_prompt == "YAML system prompt"

    def test_from_yaml_file_not_found(self):
        """Test loading non-existent YAML file raises error."""
        with pytest.raises(FileNotFoundError):
            AgentConfig.from_yaml("/nonexistent/path.yaml")

    def test_to_dict(self):
        """Test converting AgentConfig to dictionary."""
        config = AgentConfig(
            name="Test",
            skills=[SkillConfig(name="chat")],
        )

        data = config.to_dict()

        assert data["name"] == "Test"
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "chat"

    def test_to_yaml(self):
        """Test saving AgentConfig to YAML file."""
        config = AgentConfig(
            name="Save Test",
            llm=LLMConfig(model="test-model"),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.yaml"
            config.to_yaml(path)

            assert path.exists()

            # Reload and verify
            loaded = AgentConfig.from_yaml(path)
            assert loaded.name == "Save Test"
            assert loaded.llm.model == "test-model"


class TestAgentFactory:
    """Tests for AgentFactory."""

    def test_create_from_dict_with_mock_llm(self):
        """Test creating agent from dictionary with mock LLM."""
        config = {
            "name": "Test Agent",
            "memory": {"type": "simple"},
            "planner_type": "dag",
            "skills": [],  # No skills to avoid import issues
        }

        mock_llm = MockLLMClient()
        agent = AgentFactory.create_from_dict(config, llm_client=mock_llm)

        assert agent is not None
        assert agent._memory_type == "simple"
        assert agent._planner_type == "dag"

    def test_create_from_config(self):
        """Test creating agent from AgentConfig object."""
        config = AgentConfig(
            name="Config Agent",
            memory=MemoryConfigSpec(type="tiered"),
            runtime=RuntimeConfigSpec(default_timeout=45.0),
            skills=[],
        )

        mock_llm = MockLLMClient()
        agent = AgentFactory.create_from_config(config, llm_client=mock_llm)

        assert agent is not None
        assert agent._memory_type == "tiered"

    def test_create_from_yaml_file(self):
        """Test creating agent from YAML file."""
        yaml_content = """
name: "File Agent"
memory:
  type: "simple"
planner_type: "simple"
skills: []
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()

            mock_llm = MockLLMClient()
            agent = AgentFactory.create(f.name, llm_client=mock_llm)

            assert agent is not None
            assert agent._planner_type == "simple"

    def test_create_agent_convenience_function(self):
        """Test the create_agent convenience function."""
        config = {"name": "Convenience Agent", "skills": []}

        mock_llm = MockLLMClient()
        agent = create_agent(config, llm_client=mock_llm)

        assert agent is not None

    def test_register_llm_factory(self):
        """Test registering a custom LLM factory."""
        def custom_factory(config: LLMConfig):
            return MockLLMClient(f"Custom: {config.model}")

        AgentFactory.register_llm_factory("custom", custom_factory)

        # Verify registration
        assert "custom" in AgentFactory._llm_factories

    def test_register_skill_loader(self):
        """Test registering a custom skill loader."""
        async def custom_skill(**kwargs):
            return "custom result"

        def custom_loader(config: SkillConfig):
            return custom_skill

        AgentFactory.register_skill_loader("custom_type", custom_loader)

        # Verify registration
        assert "custom_type" in AgentFactory._skill_loaders


class TestReplanRequest:
    """Tests for ReplanRequest dataclass."""

    def test_basic_creation(self):
        """Test creating a ReplanRequest."""
        request = ReplanRequest(
            original_goal="Search and summarize",
            completed_tasks={"t1": "search results"},
            remaining_tasks=["t2"],
            reason="checkpoint_reached",
            checkpoint_task_id="t1",
            checkpoint_result="search results",
        )

        assert request.original_goal == "Search and summarize"
        assert request.completed_tasks == {"t1": "search results"}
        assert request.remaining_tasks == ["t2"]
        assert request.reason == "checkpoint_reached"

    def test_get_context_summary_checkpoint(self):
        """Test generating context summary for checkpoint."""
        request = ReplanRequest(
            original_goal="Test goal",
            completed_tasks={"t1": "result1", "t2": "result2"},
            remaining_tasks=["t3", "t4"],
            reason="checkpoint_reached",
            checkpoint_task_id="t2",
        )

        summary = request.get_context_summary()

        assert "Test goal" in summary
        assert "t1" in summary
        assert "t2" in summary
        assert "Remaining tasks" in summary
        assert "Checkpoint reached" in summary

    def test_get_context_summary_failure(self):
        """Test generating context summary for failure."""
        request = ReplanRequest(
            original_goal="Test goal",
            completed_tasks={"t1": "result1"},
            remaining_tasks=["t2"],
            reason="task_failed",
            failed_task_id="t2",
            failed_error="Connection error",
        )

        summary = request.get_context_summary()

        assert "Failed task: t2" in summary
        assert "Connection error" in summary


class TestReplanningStrategy:
    """Tests for ReplanningStrategy enum."""

    def test_strategy_values(self):
        """Test ReplanningStrategy enum values."""
        assert ReplanningStrategy.NONE.value == "none"
        assert ReplanningStrategy.ON_FAILURE.value == "on_failure"
        assert ReplanningStrategy.ON_CHECKPOINT.value == "on_checkpoint"
        assert ReplanningStrategy.ALWAYS.value == "always"


class TestAdaptivePlanner:
    """Tests for AdaptivePlanner."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        class MockLLM:
            def __init__(self, response: str):
                self.response = response

            async def complete(self, prompt: str) -> str:
                return self.response

        return MockLLM

    def test_init_with_strategy(self, mock_llm):
        """Test initializing AdaptivePlanner with strategy."""
        llm = mock_llm('{"mode": "direct", "response": "test"}')
        planner = AdaptivePlanner(llm, strategy=ReplanningStrategy.ON_FAILURE)

        assert planner.strategy == ReplanningStrategy.ON_FAILURE

    def test_should_replan_none_strategy(self, mock_llm):
        """Test _should_replan with NONE strategy."""
        llm = mock_llm("")
        planner = AdaptivePlanner(llm, strategy=ReplanningStrategy.NONE)

        request = ReplanRequest(
            original_goal="test",
            completed_tasks={},
            remaining_tasks=[],
            reason="checkpoint_reached",
        )

        assert planner._should_replan(request) is False

    def test_should_replan_always_strategy(self, mock_llm):
        """Test _should_replan with ALWAYS strategy."""
        llm = mock_llm("")
        planner = AdaptivePlanner(llm, strategy=ReplanningStrategy.ALWAYS)

        request = ReplanRequest(
            original_goal="test",
            completed_tasks={},
            remaining_tasks=[],
            reason="checkpoint_reached",
        )

        assert planner._should_replan(request) is True

    def test_should_replan_on_failure_strategy(self, mock_llm):
        """Test _should_replan with ON_FAILURE strategy."""
        llm = mock_llm("")
        planner = AdaptivePlanner(llm, strategy=ReplanningStrategy.ON_FAILURE)

        # Should not replan for checkpoint
        checkpoint_request = ReplanRequest(
            original_goal="test",
            completed_tasks={},
            remaining_tasks=[],
            reason="checkpoint_reached",
        )
        assert planner._should_replan(checkpoint_request) is False

        # Should replan for failure
        failure_request = ReplanRequest(
            original_goal="test",
            completed_tasks={},
            remaining_tasks=[],
            reason="task_failed",
        )
        assert planner._should_replan(failure_request) is True

    def test_should_evaluate_checkpoint(self, mock_llm):
        """Test should_evaluate_checkpoint method."""
        llm = mock_llm("")
        planner = AdaptivePlanner(llm, strategy=ReplanningStrategy.ON_CHECKPOINT)

        checkpoint_task = TaskNode(
            id="t1", skill="search", params={}, is_checkpoint=True
        )
        non_checkpoint_task = TaskNode(
            id="t2", skill="summarize", params={}, is_checkpoint=False
        )

        assert planner.should_evaluate_checkpoint(checkpoint_task) is True
        assert planner.should_evaluate_checkpoint(non_checkpoint_task) is False

    def test_create_checkpoint_request(self, mock_llm):
        """Test creating a ReplanRequest for checkpoint."""
        llm = mock_llm("")
        planner = AdaptivePlanner(llm)

        dag = TaskDAG.create("test goal", [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]},
        ])

        # Mark t1 as completed
        dag.nodes["t1"].status = TaskStatus.COMPLETED
        dag.nodes["t1"].result = "search results"

        request = planner.create_checkpoint_request(dag, dag.nodes["t1"])

        assert request.original_goal == "test goal"
        assert "t1" in request.completed_tasks
        assert request.completed_tasks["t1"] == "search results"
        assert "t2" in request.remaining_tasks
        assert request.checkpoint_task_id == "t1"

    def test_create_failure_request(self, mock_llm):
        """Test creating a ReplanRequest for failure."""
        llm = mock_llm("")
        planner = AdaptivePlanner(llm)

        dag = TaskDAG.create("test goal", [
            {"id": "t1", "skill": "search", "params": {}, "depends_on": []},
            {"id": "t2", "skill": "process", "params": {}, "depends_on": ["t1"]},
        ])

        # Mark t1 as failed
        dag.nodes["t1"].status = TaskStatus.FAILED
        dag.nodes["t1"].error = "API error"

        request = planner.create_failure_request(dag, dag.nodes["t1"])

        assert request.original_goal == "test goal"
        assert request.reason == "task_failed"
        assert request.failed_task_id == "t1"
        assert request.failed_error == "API error"

    @pytest.mark.asyncio
    async def test_replan_continue(self, mock_llm):
        """Test replan returning continue decision."""
        llm = mock_llm('{"mode": "continue", "reason": "Plan still valid"}')
        planner = AdaptivePlanner(llm, strategy=ReplanningStrategy.ON_CHECKPOINT)

        request = ReplanRequest(
            original_goal="test",
            completed_tasks={"t1": "result"},
            remaining_tasks=["t2"],
            reason="checkpoint_reached",
        )

        result = await planner.replan(request, "", {"search", "summarize"})

        assert result is None  # Continue means no new plan

    @pytest.mark.asyncio
    async def test_replan_new_plan(self, mock_llm):
        """Test replan returning new plan."""
        response = '''{
            "mode": "replan",
            "reason": "Found better approach",
            "tasks": [
                {"id": "t1", "skill": "search", "params": {"query": "new"}, "depends_on": []},
                {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]}
            ]
        }'''
        llm = mock_llm(response)
        planner = AdaptivePlanner(llm, strategy=ReplanningStrategy.ON_CHECKPOINT)

        request = ReplanRequest(
            original_goal="test",
            completed_tasks={},
            remaining_tasks=[],
            reason="checkpoint_reached",
        )

        result = await planner.replan(request, "", {"search", "summarize"})

        assert result is not None
        assert len(result.nodes) == 2
        assert "t1" in result.nodes
        assert "t2" in result.nodes


class TestDAGPlannerCheckpointMarking:
    """Tests for auto checkpoint marking in DAGPlanner."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM client."""
        class MockLLM:
            def __init__(self, response: str):
                self.response = response

            async def complete(self, prompt: str) -> str:
                return self.response

        return MockLLM

    @pytest.mark.asyncio
    async def test_search_tasks_marked_as_checkpoint(self, mock_llm):
        """Test that search tasks are auto-marked as checkpoints."""
        response = '''{
            "mode": "dag",
            "tasks": [
                {"id": "t1", "skill": "search", "params": {"query": "test"}, "depends_on": []},
                {"id": "t2", "skill": "summarize", "params": {}, "depends_on": ["t1"]}
            ]
        }'''
        llm = mock_llm(response)
        planner = DAGPlanner(llm)

        dag = await planner.create_plan("search and summarize", "", {"search", "summarize"})

        assert dag.nodes["t1"].is_checkpoint is True
        assert dag.nodes["t2"].is_checkpoint is False

    @pytest.mark.asyncio
    async def test_tasks_with_multiple_dependents_marked(self, mock_llm):
        """Test that tasks with 2+ dependents are marked as checkpoints."""
        response = '''{
            "mode": "dag",
            "tasks": [
                {"id": "t1", "skill": "fetch", "params": {}, "depends_on": []},
                {"id": "t2", "skill": "process_a", "params": {}, "depends_on": ["t1"]},
                {"id": "t3", "skill": "process_b", "params": {}, "depends_on": ["t1"]}
            ]
        }'''
        llm = mock_llm(response)
        planner = DAGPlanner(llm)

        dag = await planner.create_plan(
            "fetch and process",
            "",
            {"fetch", "process_a", "process_b"}
        )

        # t1 has 2 dependents (t2 and t3), so should be checkpoint
        assert dag.nodes["t1"].is_checkpoint is True
        assert dag.nodes["t2"].is_checkpoint is False
        assert dag.nodes["t3"].is_checkpoint is False

    @pytest.mark.asyncio
    async def test_web_search_marked_as_checkpoint(self, mock_llm):
        """Test that web_search tasks are auto-marked."""
        response = '''{
            "mode": "dag",
            "tasks": [
                {"id": "t1", "skill": "web_search", "params": {}, "depends_on": []}
            ]
        }'''
        llm = mock_llm(response)
        planner = DAGPlanner(llm)

        dag = await planner.create_plan("search web", "", {"web_search"})

        assert dag.nodes["t1"].is_checkpoint is True

    @pytest.mark.asyncio
    async def test_rag_search_marked_as_checkpoint(self, mock_llm):
        """Test that rag_search tasks are auto-marked."""
        response = '''{
            "mode": "dag",
            "tasks": [
                {"id": "t1", "skill": "rag_search", "params": {}, "depends_on": []}
            ]
        }'''
        llm = mock_llm(response)
        planner = DAGPlanner(llm)

        dag = await planner.create_plan("search docs", "", {"rag_search"})

        assert dag.nodes["t1"].is_checkpoint is True

from nimbus.core.agent import AgentConfig, AgentOS
from nimbus.config import NimbusConfig
from nimbus.skills import SkillManager


class DummyAdapter:
    _model = "ollama/gemma4:26b"


def _agent_with_goal_skill() -> AgentOS:
    manager = SkillManager.from_config(NimbusConfig())
    skills = manager.load_enabled(["goal"])
    return AgentOS(
        config=AgentConfig(),
        adapter=DummyAdapter(),
        system_prompt="Base rules.",
        skills=skills,
        skill_context={"session_id": "sess_goal", "workspace": "/workspace"},
    )


def test_goal_skill_instructions_are_pinned():
    agent = _agent_with_goal_skill()
    agent.stream_with_queue("帮忙实现支持一下 nimbus 的 skill 系统", session_id="sess_goal")

    mmu = agent.get_mmu("sess_goal")
    assert mmu is not None
    system = mmu.assemble_context()[0]["content"]
    assert "# Active Skills" in system
    assert "## goal" in system
    assert "CURRENT GOAL" in system


def test_goal_skill_keeps_durable_goal_across_followups():
    agent = _agent_with_goal_skill()
    agent.stream_with_queue("帮忙实现支持一下 nimbus 的 skill 系统", session_id="sess_goal")
    mmu = agent.get_mmu("sess_goal")
    assert mmu is not None
    assert mmu.goal == "帮忙实现支持一下 nimbus 的 skill 系统"

    agent.stream_with_queue("你好", session_id="sess_goal")
    assert mmu.goal == "帮忙实现支持一下 nimbus 的 skill 系统"

    agent.stream_with_queue("继续", session_id="sess_goal")
    assert mmu.goal == "帮忙实现支持一下 nimbus 的 skill 系统"


def test_goal_skill_allows_explicit_goal_replacement():
    agent = _agent_with_goal_skill()
    agent.stream_with_queue("帮忙实现支持一下 nimbus 的 skill 系统", session_id="sess_goal")
    mmu = agent.get_mmu("sess_goal")
    assert mmu is not None

    agent.stream_with_queue("目标：改成只做文档整理", session_id="sess_goal")

    assert mmu.goal == "只做文档整理"


def test_goal_skill_uses_first_line_of_explicit_goal():
    agent = _agent_with_goal_skill()

    agent.stream_with_queue(
        "目标：实现 Nimbus 的 goal skill smoke\n请只回复：goal received",
        session_id="sess_goal",
    )

    mmu = agent.get_mmu("sess_goal")
    assert mmu is not None
    assert mmu.goal == "实现 Nimbus 的 goal skill smoke"


def test_goal_skill_handles_multimodal_list_message():
    """A turn carrying attachments arrives as a multimodal content-block list;
    goal extraction must coerce it to text instead of crashing on `.strip()`."""
    agent = _agent_with_goal_skill()
    multimodal = [
        {"type": "text", "text": "帮忙实现支持一下 nimbus 的 skill 系统"},
        {"type": "text", "text": "\n\n[Attached video: demo.mp4 (/uploads/x.mp4)]"},
    ]
    # Must not raise (regression: AttributeError 'list' object has no attribute 'strip')
    agent.stream_with_queue(multimodal, session_id="sess_goal")

    mmu = agent.get_mmu("sess_goal")
    assert mmu is not None
    # Durable goal is derived from the coerced text of the multimodal turn.
    assert mmu.goal.startswith("帮忙实现支持一下 nimbus 的 skill 系统")


def test_coerce_message_text_extracts_text_blocks():
    assert AgentOS._coerce_message_text("plain") == "plain"
    assert AgentOS._coerce_message_text(
        [{"type": "text", "text": "a"}, {"type": "image", "data": "x"}, {"type": "text", "text": "b"}]
    ) == "a\nb"
    assert AgentOS._coerce_message_text(None) == ""


def test_without_goal_skill_preserves_previous_latest_message_behavior():
    agent = AgentOS(config=AgentConfig(), adapter=DummyAdapter(), system_prompt="Base rules.")
    agent.stream_with_queue("first goal", session_id="sess_no_skill")
    mmu = agent.get_mmu("sess_no_skill")
    assert mmu is not None

    agent.stream_with_queue("second message", session_id="sess_no_skill")

    assert mmu.goal == "second message"

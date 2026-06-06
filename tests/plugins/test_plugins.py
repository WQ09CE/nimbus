import os
from pathlib import Path

import pytest

from nimbus.config import NimbusConfig
from nimbus.config import reset_config
from nimbus.core.agent import AgentConfig, AgentOS
from nimbus.core.storage import SessionStorage
from nimbus.plugins import PluginContext, PluginManager
from nimbus.server.permission import PermissionManager
from nimbus.server.session import SessionManagerV2
from nimbus.server.sse import SSEHub


class DummyAdapter:
    _model = "ollama/gemma4:26b"


def _write_test_plugin(root: Path) -> Path:
    plugin_dir = root / "hello_plugin"
    skill_dir = plugin_dir / "skills" / "hello_skill"
    skill_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text(
        """
[project]
name = "hello-plugin"
version = "0.1.0"
description = "Hello plugin"

[tool.nimbus]
name = "hello"
entry = "plugin.py:nimbus_plugin"
skills = ["skills/hello_skill"]
""".strip(),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
from nimbus.core.tools.registry import ToolParameter, tool
from nimbus.plugins import PluginManifest, hookimpl


@tool(
    name="plugin_hello",
    description="Return a plugin greeting.",
    parameters=[ToolParameter("name", "string", "Name to greet")],
)
def plugin_hello(name: str) -> str:
    return f"hello {name}"


class HelloPlugin:
    @hookimpl
    def nimbus_plugin_manifest(self):
        return PluginManifest(
            name="hello",
            version="0.1.0",
            description="Hello plugin runtime manifest",
        )

    @hookimpl
    def nimbus_register_tools(self, ctx):
        return [plugin_hello]


def nimbus_plugin():
    return HelloPlugin()
""".strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        """
---
name: hello_skill
description: Plugin static skill
---
Use hello skill behavior for {session_id}.
""".strip(),
        encoding="utf-8",
    )
    return plugin_dir


def test_plugin_manager_discovers_local_pyproject_without_activation(tmp_path):
    _write_test_plugin(tmp_path)
    manager = PluginManager([tmp_path])

    plugins = manager.discover()

    assert "hello" in plugins
    descriptor = plugins["hello"]
    assert descriptor.source == "local"
    assert descriptor.entry == "plugin.py:nimbus_plugin"
    assert descriptor.metadata["skills"] == ["skills/hello_skill"]


def test_plugin_snapshot_activates_tools_and_static_skills(tmp_path):
    _write_test_plugin(tmp_path)
    manager = PluginManager([tmp_path])

    snapshot = manager.snapshot(
        ["hello"],
        context=PluginContext(plugin_name="", generation=0, session_id="sess_plugin"),
    )

    assert snapshot.generation == 0
    assert snapshot.manifests["hello"].description == "Hello plugin runtime manifest"
    assert [tool.definition.name for tool in snapshot.tools] == ["plugin_hello"]
    assert [skill.name for skill in snapshot.skills] == ["hello_skill"]


def test_agent_registers_plugin_tools_and_skills(tmp_path):
    _write_test_plugin(tmp_path)
    manager = PluginManager([tmp_path])
    snapshot = manager.snapshot(
        ["hello"],
        context=PluginContext(plugin_name="", generation=0, session_id="sess_plugin"),
    )

    agent = AgentOS(
        config=AgentConfig(),
        adapter=DummyAdapter(),
        system_prompt="Base rules.",
        plugin_snapshot=snapshot,
        skill_context={"session_id": "sess_plugin"},
    )

    assert "plugin_hello" in agent.registry.list_tools()
    assert agent.registry.get_origin("plugin_hello") == "plugin:hello"
    result = agent.registry.get_function("plugin_hello")("Nimbus")
    assert result == "hello Nimbus"

    agent.stream_with_queue("实现 plugin smoke", session_id="sess_plugin")
    mmu = agent.get_mmu("sess_plugin")
    assert mmu is not None
    system = mmu.assemble_context()[0]["content"]
    assert "## hello_skill" in system
    assert "Use hello skill behavior for sess_plugin." in system


def test_plugin_config_loads_json_and_env(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        """
{
  "plugins": {
    "enabled": ["hello"],
    "paths": ["/tmp/nimbus-plugins"]
  }
}
""".strip(),
        encoding="utf-8",
    )

    config = NimbusConfig.load(config_path=config_file)
    assert config.enabled_plugins == ["hello"]
    assert config.plugin_paths == ["/tmp/nimbus-plugins"]

    monkeypatch.setenv("NIMBUS_PLUGINS", "hello, other")
    monkeypatch.setenv("NIMBUS_PLUGIN_PATHS", f"/a{os.pathsep}/b")
    config = NimbusConfig.load(config_path=tmp_path / "missing.json")
    assert config.enabled_plugins == ["hello", "other"]
    assert config.plugin_paths == ["/a", "/b"]


@pytest.mark.asyncio
async def test_session_manager_loads_enabled_plugin_tools(tmp_path, monkeypatch):
    _write_test_plugin(tmp_path)
    monkeypatch.setenv("NIMBUS_LLM", "mock")
    monkeypatch.setenv("NIMBUS_PLUGINS", "hello")
    monkeypatch.setenv("NIMBUS_PLUGIN_PATHS", str(tmp_path))
    reset_config()

    manager = SessionManagerV2(SSEHub(), PermissionManager())
    manager._storage = SessionStorage(base_dir=str(tmp_path / "sessions"))

    session = await manager.create_session(name="plugin-session")
    agent = await manager.get_or_create_agent(session["id"])

    assert session["plugins"] == ["hello"]
    assert "plugin_hello" in agent.registry.list_tools()
    assert agent.registry.get_origin("plugin_hello") == "plugin:hello"

    await manager.delete_session(session["id"])
    await manager.close_all()
    reset_config()

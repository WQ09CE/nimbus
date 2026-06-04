from pathlib import Path

from nimbus.config import NimbusConfig
from nimbus.skills import SkillManager
from nimbus.skills.loader import SkillLoader


def test_skill_loader_reads_frontmatter(tmp_path):
    skill_dir = tmp_path / "hello"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: hello
version: 1.2.3
description: Test skill
default_enabled: true
---
Use hello behavior for {session_id}.
""",
        encoding="utf-8",
    )

    manifest = SkillLoader().load_dir(skill_dir)

    assert manifest is not None
    assert manifest.name == "hello"
    assert manifest.version == "1.2.3"
    assert manifest.description == "Test skill"
    assert manifest.default_enabled is True
    assert manifest.render_instructions({"session_id": "sess_1"}) == "Use hello behavior for sess_1."


def test_skill_manager_discovers_builtin_goal():
    manager = SkillManager.from_config(NimbusConfig())

    skills = manager.discover()

    assert "goal" in skills
    assert skills["goal"].default_enabled is True
    assert "CURRENT GOAL" in skills["goal"].instructions


def test_skill_manager_loads_configured_external_skill(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "custom"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: custom
description: Custom test
---
Custom instructions.
""",
        encoding="utf-8",
    )
    config = NimbusConfig(enabled_skills=["custom"], skill_paths=[str(root)])
    manager = SkillManager.from_config(config)

    loaded = manager.load_enabled(config.enabled_skills)

    assert [skill.name for skill in loaded] == ["custom"]


def test_skill_manager_explicit_empty_enabled_loads_no_default_skills():
    manager = SkillManager.from_config(NimbusConfig())

    loaded = manager.load_enabled([])

    assert loaded == []


def test_render_system_instructions_groups_skills(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "custom"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: custom
---
Use {workspace}.
""",
        encoding="utf-8",
    )
    manager = SkillManager([Path(root)])
    skill = manager.load_enabled(["custom"])[0]

    rendered = SkillManager.render_system_instructions([skill], {"workspace": "/repo"})

    assert rendered.startswith("# Active Skills")
    assert "## custom" in rendered
    assert "Use /repo." in rendered

# Nimbus Skill System

The Skill System allows extending Nimbus capabilities using directory-based modules. 
This follows the [Agent Skills Open Standard](https://github.com/anthropics/agent-skills).

## Structure of a Skill

A skill is a directory containing a `SKILL.md` manifest and executable scripts.

```
/path/to/skill/
  ├── SKILL.md          # Manifest & Instructions
  └── scripts/
      └── my_tool.py    # Executable tool
```

## SKILL.md Format

The `SKILL.md` file defines the skill metadata and tools using YAML frontmatter, followed by usage instructions (System Prompt) in Markdown.

```markdown
---
name: my-skill                  # Skill name (used for namespacing/routing)
version: 1.0.0
description: Description of what this skill does
tools:
  - name: MyTool                # Tool Name (visible to Agent)
    description: Tool description
    entrypoint: scripts/my_tool.py
    args:
      arg1:
        type: string
        description: Argument description
      arg2:
        type: boolean
        default: false
---

# My Skill Instructions

You are an expert at using MyTool.
Use it when the user asks for X.
```

## Adding Skills to Agent

To load skills, add their paths to `AgentOSConfig`:

```python
config = AgentOSConfig(
    skill_paths=[Path("/path/to/skills/dir")]
)
```

The system will scan the directory for subdirectories containing `SKILL.md` and load them automatically.

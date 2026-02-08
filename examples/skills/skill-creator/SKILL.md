---
name: skill-creator
version: 1.0.0
description: Tools for helping users create and manage Nimbus Skills
tools:
  - name: CreateSkill
    description: Create a new Skill directory structure with SKILL.md
    entrypoint: scripts/create_skill.py
    args:
      name:
        type: string
        description: "The name of the new skill (e.g. postgres-expert)"
      description:
        type: string
        description: "A brief description of what the skill does"
      path:
        type: string
        description: "The root path where the skill directory should be created (default: skills/)"
  - name: AddTool
    description: Add a new tool definition to an existing skill
    entrypoint: scripts/add_tool.py
    args:
      skill_path:
        type: string
        description: "Path to the skill directory (e.g. skills/my-skill)"
      tool_name:
        type: string
        description: "Name of the new tool (e.g. AnalyzeTable)"
      tool_description:
        type: string
        description: "Description of the tool functionality"
      script_name:
        type: string
        description: "Name of the script to create (e.g. analyze.py)"
      args:
        type: string
        description: "Comma-separated list of arguments (e.g. table:string,limit:integer)"
---

# Skill Creator Instructions

Use these tools to help the user scaffold new skills quickly.

1.  **CreateSkill**: Start by creating the skill directory.
    - Example: `CreateSkill(name="github-helper", description="Tools for interacting with GitHub", path="skills")`

2.  **AddTool**: Once the skill exists, add tools to it.
    - Example: `AddTool(skill_path="skills/github-helper", tool_name="ListIssues", tool_description="List issues in a repo", script_name="list_issues.py", args="repo:string,limit:integer")`

Always confirm the paths where skills are being created.
The default location for skills is usually `skills/` in the project root.

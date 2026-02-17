---
name: claude-cli-wrapper
version: 1.0.0
description: A skill to interact with the Claude Code CLI, allowing for powerful agentic
  coding capabilities within the Nimbus framework.
tools:
- name: ClaudeCodeCommand
  description: Execute a query using the Claude Code CLI. 'mode' can be 'execute'
    (runs a command) or 'chat' (interactive).
  entrypoint: scripts/run_claude.py
  args:
    query:
      type: string
      description: The query argument
    mode:
      type: string
      description: The mode argument
---


# claude-cli-wrapper Guidelines

Describe how to use the claude-cli-wrapper tools here.

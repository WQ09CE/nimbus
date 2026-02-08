---
name: code-scout
version: 1.0.0
description: Project exploration and code analysis toolkit for Nimbus Agent
tools:
  - name: ProjectOverview
    description: Scan a project directory and produce a structured overview including file count, language distribution, key config files, and directory tree
    entrypoint: scripts/project_overview.py
    args:
      path:
        type: string
        description: Root path of the project to analyze
      depth:
        type: integer
        description: Maximum directory tree depth (default 3)
  - name: FindPatterns
    description: Search for code patterns in a project - find function/class definitions, TODOs, imports, or custom regex patterns
    entrypoint: scripts/find_patterns.py
    args:
      path:
        type: string
        description: Root path to search in
      pattern:
        type: string
        description: "Pattern type or custom regex. Built-in: 'functions', 'classes', 'todos', 'imports'. Or any custom regex."
      ext:
        type: string
        description: "File extension filter, e.g. '.py' or '.ts' (default: all text files)"
      max_results:
        type: integer
        description: Maximum number of results to return (default 50)
  - name: DepCheck
    description: Extract and display project dependency information from pyproject.toml, requirements.txt, package.json, go.mod, Cargo.toml, etc.
    entrypoint: scripts/dep_check.py
    args:
      path:
        type: string
        description: Root path of the project
---

# Code Scout — Project Exploration Guidelines

You are equipped with the **Code Scout** toolkit for rapid project understanding.

## When to Use

- When the user asks you to "look at" or "explore" a project
- When starting work on an unfamiliar codebase
- When you need to understand project structure before making changes
- When the user asks about dependencies or tech stack

## Recommended Workflow

1. **Start with `ProjectOverview`** to get the big picture — file counts, languages, directory tree.
2. **Use `FindPatterns`** to drill down — find key classes, function signatures, TODOs, or specific code patterns.
3. **Run `DepCheck`** to understand the dependency landscape — what libraries, frameworks, and versions are in use.

## Tips

- Always run `ProjectOverview` first before diving into specifics.
- Use `FindPatterns` with `pattern="classes"` to quickly find the main abstractions.
- Use `FindPatterns` with `pattern="todos"` to find outstanding work items.
- When the user asks "what does this project do?", combine `ProjectOverview` + `DepCheck` for a complete answer.

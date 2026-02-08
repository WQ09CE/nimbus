---
name: hello-world
version: 1.0.0
description: A friendly greeting skill
tools:
  - name: Greet
    description: Say hello to someone
    entrypoint: scripts/greet.py
    args:
      name:
        type: string
        description: Name of the person to greet
      loud:
        type: boolean
        description: Whether to shout
        default: false
---

# Hello World Skill
This skill demonstrates how to create a simple skill.
All tools in this skill are for demonstration purposes.

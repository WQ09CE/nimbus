"""Permission management system for tool access control.

This module provides a rule-based permission management system for controlling
access to tools and resources. It supports:

- Permission rules with glob pattern matching for resources
- Priority-based rule evaluation (deny > allow > ask)
- Predefined permission sets for common use cases
- Subset creation for sub-agents with restricted permissions

Example usage:
    manager = PermissionManager(default_action="ask")
    manager.add_rule(PermissionRule("Read", "*", "allow"))
    manager.add_rule(PermissionRule("Bash", "*", "deny"))

    if manager.is_allowed("Read", "/some/file.txt"):
        # execute read

    # Create subset for sub-agent
    sub_manager = manager.create_subset(["Read", "Glob"])
"""

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import List, Literal, Optional


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class PermissionRule:
    """A permission rule defining access control for a tool and resource pattern.

    Attributes:
        tool: Tool name to match, or "*" for all tools.
        pattern: Glob pattern for resource matching (e.g., "/home/*", "**/*.py").
        action: Permission action - "allow", "deny", or "ask".
        priority: Rule priority, higher values take precedence.

    Examples:
        # Allow all read operations
        PermissionRule("Read", "*", "allow")

        # Deny bash for sensitive directories
        PermissionRule("Bash", "/etc/**", "deny", priority=10)

        # Ask for write to any file
        PermissionRule("Write", "*", "ask")
    """
    tool: str
    pattern: str = "*"
    action: Literal["allow", "deny", "ask"] = "allow"
    priority: int = 0

    def matches(self, tool: str, resource: str = "") -> bool:
        """Check if this rule matches the given tool and resource.

        Args:
            tool: Tool name to check.
            resource: Resource path to check against pattern.

        Returns:
            True if this rule matches, False otherwise.
        """
        # Check tool match
        if self.tool != "*" and self.tool != tool:
            return False

        # Check resource pattern match
        if self.pattern == "*":
            return True

        # Handle empty resource
        if not resource:
            return self.pattern == "*"

        # Use path-based matching for proper glob semantics
        # * matches within a single path segment (not including /)
        # ** matches across path segments
        return self._match_path(self.pattern, resource)

    def _match_path(self, pattern: str, resource: str) -> bool:
        """Match resource path against pattern with proper glob semantics.

        * matches any characters within a single path segment (not /)
        ** matches any characters across path segments (including /)

        Args:
            pattern: Glob pattern to match.
            resource: Resource path to match.

        Returns:
            True if resource matches pattern.
        """
        # Split pattern and resource by /
        pattern_parts = self._split_path(pattern)
        resource_parts = self._split_path(resource)

        return self._match_parts(pattern_parts, resource_parts)

    def _split_path(self, path: str) -> List[str]:
        """Split path into segments, preserving leading slash info.

        Args:
            path: Path to split.

        Returns:
            List of path segments.
        """
        if not path:
            return []
        # Handle leading slash
        if path.startswith("/"):
            parts = [""] + [p for p in path[1:].split("/") if p]
        else:
            parts = [p for p in path.split("/") if p]
        return parts

    def _match_parts(self, pattern_parts: List[str], resource_parts: List[str]) -> bool:
        """Recursively match pattern parts against resource parts.

        Args:
            pattern_parts: Pattern segments.
            resource_parts: Resource path segments.

        Returns:
            True if match, False otherwise.
        """
        if not pattern_parts:
            return not resource_parts

        if not resource_parts:
            # Pattern remaining, but no resource left
            # Only match if all remaining patterns are **
            return all(p == "**" for p in pattern_parts)

        p = pattern_parts[0]
        r = resource_parts[0]

        if p == "**":
            # ** can match zero or more path segments
            # Try matching with 0 segments consumed, 1 segment, etc.
            for i in range(len(resource_parts) + 1):
                if self._match_parts(pattern_parts[1:], resource_parts[i:]):
                    return True
            return False
        elif fnmatch(r, p):
            # Single segment match using fnmatch for wildcards within segment
            return self._match_parts(pattern_parts[1:], resource_parts[1:])
        else:
            return False


@dataclass
class PermissionSet:
    """A named collection of permission rules.

    Permission sets provide a convenient way to bundle related permission
    rules for common use cases (e.g., readonly access, developer access).

    Attributes:
        rules: List of permission rules in this set.
        name: Identifier for this permission set.
        description: Human-readable description.

    Example:
        readonly_set = PermissionSet(
            name="readonly",
            description="Read-only access to all files",
            rules=[
                PermissionRule("Read", "*", "allow"),
                PermissionRule("Glob", "*", "allow"),
                PermissionRule("*", "*", "deny"),
            ]
        )
    """
    rules: List[PermissionRule] = field(default_factory=list)
    name: str = "default"
    description: str = ""


# =============================================================================
# Permission Manager
# =============================================================================


class PermissionManager:
    """Manages permission rules for tool access control.

    The PermissionManager evaluates access requests against a set of rules,
    using priority-based ordering with deny taking precedence over allow.

    Evaluation order:
    1. Sort rules by priority (descending)
    2. For rules with same priority: deny > allow > ask
    3. First matching rule determines the action
    4. If no rules match, use default_action

    Attributes:
        rules: List of registered permission rules.
        default_action: Action when no rules match ("allow", "deny", "ask").

    Example:
        manager = PermissionManager(default_action="ask")
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.add_rule(PermissionRule("Bash", "/etc/**", "deny", priority=10))

        result = manager.evaluate("Read", "/home/user/file.txt")  # "allow"
        result = manager.evaluate("Bash", "/etc/passwd")  # "deny"
        result = manager.evaluate("Unknown", "")  # "ask" (default)
    """

    def __init__(self, default_action: Literal["allow", "deny", "ask"] = "ask"):
        """Initialize the permission manager.

        Args:
            default_action: Default action when no rules match.
        """
        self.rules: List[PermissionRule] = []
        self.default_action: Literal["allow", "deny", "ask"] = default_action

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a permission rule.

        Args:
            rule: Permission rule to add.
        """
        self.rules.append(rule)

    def add_rules(self, rules: List[PermissionRule]) -> None:
        """Add multiple permission rules.

        Args:
            rules: List of permission rules to add.
        """
        self.rules.extend(rules)

    def load_permission_set(self, permission_set: PermissionSet) -> None:
        """Load rules from a permission set.

        Args:
            permission_set: Permission set to load rules from.
        """
        self.add_rules(permission_set.rules)

    def clear_rules(self) -> None:
        """Remove all permission rules."""
        self.rules.clear()

    def evaluate(self, tool: str, resource: str = "") -> Literal["allow", "deny", "ask"]:
        """Evaluate permission for a tool and resource.

        The evaluation process:
        1. Find all rules that match the tool and resource
        2. Sort by priority (descending)
        3. For same priority: deny > allow > ask
        4. Return the action of the highest priority matching rule
        5. If no rules match, return default_action

        Args:
            tool: Tool name to check permission for.
            resource: Optional resource path being accessed.

        Returns:
            Permission action: "allow", "deny", or "ask".
        """
        # Find all matching rules
        matching_rules = [
            rule for rule in self.rules
            if rule.matches(tool, resource)
        ]

        if not matching_rules:
            return self.default_action

        # Sort by priority (descending), then by action order (deny > allow > ask)
        action_order = {"deny": 0, "allow": 1, "ask": 2}
        matching_rules.sort(
            key=lambda r: (-r.priority, action_order.get(r.action, 3))
        )

        return matching_rules[0].action

    def is_allowed(self, tool: str, resource: str = "") -> bool:
        """Check if a tool access is allowed.

        This is a convenience method that returns True only if the
        evaluation result is "allow".

        Args:
            tool: Tool name to check.
            resource: Optional resource path.

        Returns:
            True if explicitly allowed, False otherwise.
        """
        return self.evaluate(tool, resource) == "allow"

    def is_denied(self, tool: str, resource: str = "") -> bool:
        """Check if a tool access is denied.

        Args:
            tool: Tool name to check.
            resource: Optional resource path.

        Returns:
            True if explicitly denied, False otherwise.
        """
        return self.evaluate(tool, resource) == "deny"

    def requires_ask(self, tool: str, resource: str = "") -> bool:
        """Check if a tool access requires user confirmation.

        Args:
            tool: Tool name to check.
            resource: Optional resource path.

        Returns:
            True if user confirmation is required, False otherwise.
        """
        return self.evaluate(tool, resource) == "ask"

    def filter_tools(self, tools: List[str]) -> List[str]:
        """Filter a list of tools to only include allowed ones.

        Tools are evaluated without a resource path. Only tools
        that evaluate to "allow" are included.

        Args:
            tools: List of tool names to filter.

        Returns:
            List of tools that are allowed.
        """
        return [tool for tool in tools if self.is_allowed(tool)]

    def get_allowed_tools(self, tools: List[str]) -> List[str]:
        """Get tools that are allowed (not denied).

        Unlike filter_tools(), this returns tools that are either
        "allow" or "ask" (i.e., not explicitly denied).

        Args:
            tools: List of tool names to check.

        Returns:
            List of tools that are not denied.
        """
        return [tool for tool in tools if not self.is_denied(tool)]

    def create_subset(self, allowed_tools: List[str]) -> "PermissionManager":
        """Create a subset permission manager for sub-agents.

        The subset manager inherits the current rules but adds
        deny rules for any tools not in the allowed_tools list.

        Args:
            allowed_tools: List of tools to allow in the subset.

        Returns:
            New PermissionManager with restricted tool access.
        """
        subset = PermissionManager(default_action=self.default_action)

        # Copy existing rules
        for rule in self.rules:
            subset.add_rule(PermissionRule(
                tool=rule.tool,
                pattern=rule.pattern,
                action=rule.action,
                priority=rule.priority,
            ))

        # Add deny rule for tools not in allowed_tools
        # Use high priority to override existing rules
        max_priority = max((r.priority for r in self.rules), default=0) + 100

        # Add explicit allow for allowed tools (to override the catch-all deny)
        for tool in allowed_tools:
            subset.add_rule(PermissionRule(
                tool=tool,
                pattern="*",
                action="allow",
                priority=max_priority + 1,
            ))

        # Add catch-all deny for non-allowed tools
        subset.add_rule(PermissionRule(
            tool="*",
            pattern="*",
            action="deny",
            priority=max_priority,
        ))

        return subset

    def get_rules_for_tool(self, tool: str) -> List[PermissionRule]:
        """Get all rules that apply to a specific tool.

        Args:
            tool: Tool name to find rules for.

        Returns:
            List of rules that match the tool.
        """
        return [
            rule for rule in self.rules
            if rule.tool == "*" or rule.tool == tool
        ]

    def to_dict(self) -> dict:
        """Serialize the permission manager to a dictionary.

        Returns:
            Dictionary representation of the manager.
        """
        return {
            "default_action": self.default_action,
            "rules": [
                {
                    "tool": rule.tool,
                    "pattern": rule.pattern,
                    "action": rule.action,
                    "priority": rule.priority,
                }
                for rule in self.rules
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PermissionManager":
        """Create a PermissionManager from a dictionary.

        Args:
            data: Dictionary with manager configuration.

        Returns:
            New PermissionManager instance.
        """
        manager = cls(default_action=data.get("default_action", "ask"))
        for rule_data in data.get("rules", []):
            manager.add_rule(PermissionRule(
                tool=rule_data["tool"],
                pattern=rule_data.get("pattern", "*"),
                action=rule_data.get("action", "allow"),
                priority=rule_data.get("priority", 0),
            ))
        return manager


# =============================================================================
# Predefined Permission Sets
# =============================================================================


READONLY_PERMISSIONS = PermissionSet(
    name="readonly",
    description="Read-only permissions for safe exploration",
    rules=[
        PermissionRule("Read", "*", "allow", priority=10),
        PermissionRule("Glob", "*", "allow", priority=10),
        PermissionRule("Grep", "*", "allow", priority=10),
        PermissionRule("*", "*", "deny", priority=0),  # Default deny for all others
    ],
)
"""Permission set for read-only access.

Allows:
- Read: Read file contents
- Glob: Find files by pattern
- Grep: Search file contents

Denies:
- All other tools
"""


CODER_PERMISSIONS = PermissionSet(
    name="coder",
    description="Developer permissions with Bash confirmation",
    rules=[
        PermissionRule("Read", "*", "allow", priority=10),
        PermissionRule("Write", "*", "allow", priority=10),
        PermissionRule("Edit", "*", "allow", priority=10),
        PermissionRule("Glob", "*", "allow", priority=10),
        PermissionRule("Grep", "*", "allow", priority=10),
        PermissionRule("Bash", "*", "ask", priority=10),  # Bash requires confirmation
        PermissionRule("*", "*", "deny", priority=0),
    ],
)
"""Permission set for code development.

Allows:
- Read: Read file contents
- Write: Create/overwrite files
- Edit: Modify existing files
- Glob: Find files by pattern
- Grep: Search file contents

Asks:
- Bash: Shell command execution (requires user confirmation)

Denies:
- All other tools
"""


FULL_ACCESS_PERMISSIONS = PermissionSet(
    name="full_access",
    description="Full access to all tools",
    rules=[
        PermissionRule("*", "*", "allow", priority=0),
    ],
)
"""Permission set for unrestricted access.

Allows:
- All tools and resources

Use with caution - provides no access restrictions.
"""


SAFE_BASH_PERMISSIONS = PermissionSet(
    name="safe_bash",
    description="Safe bash execution with path restrictions",
    rules=[
        PermissionRule("Read", "*", "allow", priority=10),
        PermissionRule("Write", "*", "allow", priority=10),
        PermissionRule("Edit", "*", "allow", priority=10),
        PermissionRule("Glob", "*", "allow", priority=10),
        PermissionRule("Grep", "*", "allow", priority=10),
        PermissionRule("Bash", "/tmp/**", "allow", priority=20),  # Allow in /tmp
        PermissionRule("Bash", "/home/**", "allow", priority=20),  # Allow in home
        PermissionRule("Bash", "/etc/**", "deny", priority=30),  # Deny system configs
        PermissionRule("Bash", "/usr/**", "deny", priority=30),  # Deny system dirs
        PermissionRule("Bash", "*", "ask", priority=10),  # Ask for other paths
        PermissionRule("*", "*", "deny", priority=0),
    ],
)
"""Permission set with path-based Bash restrictions.

Allows:
- Read/Write/Edit/Glob/Grep: All paths
- Bash: Only in /tmp and /home directories

Denies:
- Bash: System directories (/etc, /usr)

Asks:
- Bash: Other paths require confirmation
"""


EXPLORER_PERMISSIONS = PermissionSet(
    name="explorer",
    description="Exploration with web access",
    rules=[
        PermissionRule("Read", "*", "allow", priority=10),
        PermissionRule("Glob", "*", "allow", priority=10),
        PermissionRule("Grep", "*", "allow", priority=10),
        PermissionRule("WebSearch", "*", "allow", priority=10),
        PermissionRule("WebFetch", "*", "allow", priority=10),
        PermissionRule("*", "*", "deny", priority=0),
    ],
)
"""Permission set for exploration with web access.

Allows:
- Read: Read file contents
- Glob: Find files by pattern
- Grep: Search file contents
- WebSearch: Web search queries
- WebFetch: Fetch web pages

Denies:
- All modifying operations
"""


# =============================================================================
# Factory Functions
# =============================================================================


def create_permission_manager(
    permission_set: Optional[PermissionSet] = None,
    default_action: Literal["allow", "deny", "ask"] = "ask",
) -> PermissionManager:
    """Create a PermissionManager with optional preset permissions.

    Args:
        permission_set: Optional permission set to load.
        default_action: Default action when no rules match.

    Returns:
        Configured PermissionManager.

    Example:
        # Create with readonly permissions
        manager = create_permission_manager(READONLY_PERMISSIONS)

        # Create with custom default
        manager = create_permission_manager(default_action="deny")
    """
    manager = PermissionManager(default_action=default_action)
    if permission_set:
        manager.load_permission_set(permission_set)
    return manager


def create_subagent_permissions(
    allowed_tools: List[str],
    base_permissions: Optional[PermissionManager] = None,
) -> PermissionManager:
    """Create a permission manager for sub-agents with restricted tool access.

    This is a convenience function for creating sub-agent permissions
    that only allow specific tools.

    Args:
        allowed_tools: List of tools the sub-agent can use.
        base_permissions: Optional base manager to inherit from.

    Returns:
        PermissionManager restricted to allowed tools.

    Example:
        # Create permissions for a read-only sub-agent
        perms = create_subagent_permissions(["Read", "Glob", "Grep"])

        # Create from existing manager
        main_manager = create_permission_manager(CODER_PERMISSIONS)
        sub_perms = create_subagent_permissions(
            ["Read", "Glob"],
            base_permissions=main_manager
        )
    """
    if base_permissions:
        return base_permissions.create_subset(allowed_tools)

    manager = PermissionManager(default_action="deny")
    for tool in allowed_tools:
        manager.add_rule(PermissionRule(
            tool=tool,
            pattern="*",
            action="allow",
            priority=10,
        ))
    return manager

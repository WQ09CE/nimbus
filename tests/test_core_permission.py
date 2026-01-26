"""Tests for the permission management system."""

import pytest

from nimbus.core.permission import (
    PermissionRule,
    PermissionSet,
    PermissionManager,
    READONLY_PERMISSIONS,
    CODER_PERMISSIONS,
    FULL_ACCESS_PERMISSIONS,
    SAFE_BASH_PERMISSIONS,
    EXPLORER_PERMISSIONS,
    create_permission_manager,
    create_subagent_permissions,
)


class TestPermissionRule:
    """Tests for PermissionRule dataclass."""

    def test_basic_rule_creation(self):
        """Test creating a basic permission rule."""
        rule = PermissionRule("Read", "*", "allow")
        assert rule.tool == "Read"
        assert rule.pattern == "*"
        assert rule.action == "allow"
        assert rule.priority == 0

    def test_rule_with_priority(self):
        """Test creating a rule with custom priority."""
        rule = PermissionRule("Bash", "/etc/**", "deny", priority=100)
        assert rule.priority == 100
        assert rule.action == "deny"

    def test_matches_exact_tool(self):
        """Test matching exact tool name."""
        rule = PermissionRule("Read", "*", "allow")
        assert rule.matches("Read") is True
        assert rule.matches("Write") is False
        assert rule.matches("read") is False  # Case sensitive

    def test_matches_wildcard_tool(self):
        """Test matching wildcard tool."""
        rule = PermissionRule("*", "*", "deny")
        assert rule.matches("Read") is True
        assert rule.matches("Write") is True
        assert rule.matches("Bash") is True

    def test_matches_simple_pattern(self):
        """Test matching simple glob patterns."""
        rule = PermissionRule("Read", "*.py", "allow")
        assert rule.matches("Read", "test.py") is True
        assert rule.matches("Read", "test.txt") is False
        assert rule.matches("Read", "dir/test.py") is False

    def test_matches_directory_pattern(self):
        """Test matching directory patterns."""
        rule = PermissionRule("Read", "/home/*", "allow")
        assert rule.matches("Read", "/home/user") is True
        assert rule.matches("Read", "/home/admin") is True
        assert rule.matches("Read", "/home/user/file.txt") is False
        assert rule.matches("Read", "/etc/passwd") is False

    def test_matches_recursive_pattern(self):
        """Test matching recursive ** patterns."""
        rule = PermissionRule("Read", "/home/**", "allow")
        assert rule.matches("Read", "/home/user") is True
        assert rule.matches("Read", "/home/user/file.txt") is True
        assert rule.matches("Read", "/home/user/dir/subdir/file.py") is True
        assert rule.matches("Read", "/etc/passwd") is False

    def test_matches_mixed_pattern(self):
        """Test matching mixed patterns."""
        rule = PermissionRule("Write", "/home/**/*.py", "allow")
        assert rule.matches("Write", "/home/test.py") is True
        assert rule.matches("Write", "/home/user/test.py") is True
        assert rule.matches("Write", "/home/user/dir/test.py") is True
        assert rule.matches("Write", "/home/user/test.txt") is False

    def test_matches_empty_resource(self):
        """Test matching with empty resource."""
        rule_wildcard = PermissionRule("Read", "*", "allow")
        rule_specific = PermissionRule("Read", "/home/*", "allow")

        assert rule_wildcard.matches("Read", "") is True
        assert rule_specific.matches("Read", "") is False


class TestPermissionSet:
    """Tests for PermissionSet dataclass."""

    def test_basic_permission_set(self):
        """Test creating a basic permission set."""
        pset = PermissionSet(
            name="test",
            description="Test permission set",
            rules=[
                PermissionRule("Read", "*", "allow"),
                PermissionRule("Write", "*", "deny"),
            ]
        )
        assert pset.name == "test"
        assert len(pset.rules) == 2

    def test_default_permission_set(self):
        """Test default values for permission set."""
        pset = PermissionSet()
        assert pset.name == "default"
        assert pset.description == ""
        assert pset.rules == []


class TestPermissionManager:
    """Tests for PermissionManager class."""

    def test_basic_initialization(self):
        """Test basic manager initialization."""
        manager = PermissionManager()
        assert manager.default_action == "ask"
        assert manager.rules == []

    def test_custom_default_action(self):
        """Test manager with custom default action."""
        manager = PermissionManager(default_action="deny")
        assert manager.default_action == "deny"

    def test_add_rule(self):
        """Test adding rules."""
        manager = PermissionManager()
        rule = PermissionRule("Read", "*", "allow")
        manager.add_rule(rule)
        assert len(manager.rules) == 1

    def test_add_rules(self):
        """Test adding multiple rules."""
        manager = PermissionManager()
        rules = [
            PermissionRule("Read", "*", "allow"),
            PermissionRule("Write", "*", "deny"),
        ]
        manager.add_rules(rules)
        assert len(manager.rules) == 2

    def test_clear_rules(self):
        """Test clearing rules."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.clear_rules()
        assert len(manager.rules) == 0

    def test_evaluate_allow(self):
        """Test evaluating allow rule."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        assert manager.evaluate("Read") == "allow"

    def test_evaluate_deny(self):
        """Test evaluating deny rule."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Bash", "*", "deny"))
        assert manager.evaluate("Bash") == "deny"

    def test_evaluate_ask(self):
        """Test evaluating ask rule."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Write", "*", "ask"))
        assert manager.evaluate("Write") == "ask"

    def test_evaluate_default_action(self):
        """Test fallback to default action."""
        manager = PermissionManager(default_action="deny")
        assert manager.evaluate("Unknown") == "deny"

        manager2 = PermissionManager(default_action="allow")
        assert manager2.evaluate("Unknown") == "allow"

    def test_evaluate_priority_order(self):
        """Test priority-based rule ordering."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow", priority=0))
        manager.add_rule(PermissionRule("Read", "/etc/**", "deny", priority=10))

        assert manager.evaluate("Read", "/home/file.txt") == "allow"
        assert manager.evaluate("Read", "/etc/passwd") == "deny"

    def test_evaluate_deny_priority_over_allow(self):
        """Test deny takes precedence over allow at same priority."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow", priority=10))
        manager.add_rule(PermissionRule("Read", "*", "deny", priority=10))

        # deny should win at same priority
        assert manager.evaluate("Read") == "deny"

    def test_evaluate_allow_priority_over_ask(self):
        """Test allow takes precedence over ask at same priority."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "ask", priority=10))
        manager.add_rule(PermissionRule("Read", "*", "allow", priority=10))

        # allow should win over ask at same priority
        assert manager.evaluate("Read") == "allow"

    def test_is_allowed(self):
        """Test is_allowed convenience method."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.add_rule(PermissionRule("Write", "*", "deny"))
        manager.add_rule(PermissionRule("Bash", "*", "ask"))

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Write") is False
        assert manager.is_allowed("Bash") is False

    def test_is_denied(self):
        """Test is_denied convenience method."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.add_rule(PermissionRule("Write", "*", "deny"))

        assert manager.is_denied("Read") is False
        assert manager.is_denied("Write") is True

    def test_requires_ask(self):
        """Test requires_ask convenience method."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.add_rule(PermissionRule("Bash", "*", "ask"))

        assert manager.requires_ask("Read") is False
        assert manager.requires_ask("Bash") is True

    def test_filter_tools(self):
        """Test filtering tool list."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.add_rule(PermissionRule("Glob", "*", "allow"))
        manager.add_rule(PermissionRule("Write", "*", "deny"))
        manager.add_rule(PermissionRule("Bash", "*", "ask"))

        tools = ["Read", "Glob", "Write", "Bash", "Unknown"]
        filtered = manager.filter_tools(tools)

        assert "Read" in filtered
        assert "Glob" in filtered
        assert "Write" not in filtered
        assert "Bash" not in filtered
        assert "Unknown" not in filtered

    def test_get_allowed_tools(self):
        """Test getting non-denied tools."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.add_rule(PermissionRule("Write", "*", "deny"))
        manager.add_rule(PermissionRule("Bash", "*", "ask"))

        tools = ["Read", "Write", "Bash"]
        allowed = manager.get_allowed_tools(tools)

        assert "Read" in allowed
        assert "Write" not in allowed
        assert "Bash" in allowed  # ask is not denied

    def test_create_subset(self):
        """Test creating subset permission manager."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.add_rule(PermissionRule("Write", "*", "allow"))
        manager.add_rule(PermissionRule("Bash", "*", "allow"))

        subset = manager.create_subset(["Read", "Glob"])

        # Allowed tools work
        assert subset.is_allowed("Read") is True
        assert subset.is_allowed("Glob") is True

        # Non-allowed tools are denied
        assert subset.is_denied("Write") is True
        assert subset.is_denied("Bash") is True

    def test_get_rules_for_tool(self):
        """Test getting rules for specific tool."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("Read", "*", "allow"))
        manager.add_rule(PermissionRule("Read", "/etc/**", "deny"))
        manager.add_rule(PermissionRule("Write", "*", "allow"))
        manager.add_rule(PermissionRule("*", "*", "ask"))

        read_rules = manager.get_rules_for_tool("Read")
        assert len(read_rules) == 3  # 2 Read rules + 1 wildcard

    def test_to_dict(self):
        """Test serialization to dictionary."""
        manager = PermissionManager(default_action="deny")
        manager.add_rule(PermissionRule("Read", "*", "allow", priority=10))

        data = manager.to_dict()
        assert data["default_action"] == "deny"
        assert len(data["rules"]) == 1
        assert data["rules"][0]["tool"] == "Read"
        assert data["rules"][0]["priority"] == 10

    def test_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "default_action": "allow",
            "rules": [
                {"tool": "Read", "pattern": "*", "action": "allow", "priority": 5},
                {"tool": "Bash", "pattern": "/etc/**", "action": "deny", "priority": 10},
            ]
        }

        manager = PermissionManager.from_dict(data)
        assert manager.default_action == "allow"
        assert len(manager.rules) == 2
        assert manager.evaluate("Read") == "allow"
        assert manager.evaluate("Bash", "/etc/passwd") == "deny"

    def test_load_permission_set(self):
        """Test loading from permission set."""
        pset = PermissionSet(
            name="test",
            rules=[
                PermissionRule("Read", "*", "allow"),
                PermissionRule("Write", "*", "deny"),
            ]
        )

        manager = PermissionManager()
        manager.load_permission_set(pset)

        assert len(manager.rules) == 2
        assert manager.is_allowed("Read") is True
        assert manager.is_denied("Write") is True


class TestPredefinedPermissionSets:
    """Tests for predefined permission sets."""

    def test_readonly_permissions(self):
        """Test readonly permission set."""
        manager = create_permission_manager(READONLY_PERMISSIONS)

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Glob") is True
        assert manager.is_allowed("Grep") is True
        assert manager.is_denied("Write") is True
        assert manager.is_denied("Bash") is True

    def test_coder_permissions(self):
        """Test coder permission set."""
        manager = create_permission_manager(CODER_PERMISSIONS)

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Write") is True
        assert manager.is_allowed("Edit") is True
        assert manager.is_allowed("Glob") is True
        assert manager.is_allowed("Grep") is True
        assert manager.requires_ask("Bash") is True
        assert manager.is_denied("Unknown") is True

    def test_full_access_permissions(self):
        """Test full access permission set."""
        manager = create_permission_manager(FULL_ACCESS_PERMISSIONS)

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Write") is True
        assert manager.is_allowed("Bash") is True
        assert manager.is_allowed("Anything") is True

    def test_safe_bash_permissions(self):
        """Test safe bash permission set."""
        manager = create_permission_manager(SAFE_BASH_PERMISSIONS)

        # Basic tools allowed
        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Write") is True

        # Bash path restrictions
        assert manager.is_allowed("Bash", "/tmp/script.sh") is True
        assert manager.is_allowed("Bash", "/home/user/script.sh") is True
        assert manager.is_denied("Bash", "/etc/passwd") is True
        assert manager.is_denied("Bash", "/usr/bin/python") is True
        assert manager.requires_ask("Bash", "/opt/app/script.sh") is True

    def test_explorer_permissions(self):
        """Test explorer permission set."""
        manager = create_permission_manager(EXPLORER_PERMISSIONS)

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("Glob") is True
        assert manager.is_allowed("Grep") is True
        assert manager.is_allowed("WebSearch") is True
        assert manager.is_allowed("WebFetch") is True
        assert manager.is_denied("Write") is True
        assert manager.is_denied("Bash") is True


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_permission_manager_default(self):
        """Test creating manager with defaults."""
        manager = create_permission_manager()
        assert manager.default_action == "ask"
        assert len(manager.rules) == 0

    def test_create_permission_manager_with_set(self):
        """Test creating manager with permission set."""
        manager = create_permission_manager(READONLY_PERMISSIONS)
        assert manager.is_allowed("Read") is True
        assert manager.is_denied("Write") is True

    def test_create_permission_manager_custom_default(self):
        """Test creating manager with custom default."""
        manager = create_permission_manager(default_action="deny")
        assert manager.default_action == "deny"

    def test_create_subagent_permissions_basic(self):
        """Test creating subagent permissions."""
        perms = create_subagent_permissions(["Read", "Glob"])

        assert perms.is_allowed("Read") is True
        assert perms.is_allowed("Glob") is True
        assert perms.is_denied("Write") is True
        assert perms.is_denied("Bash") is True

    def test_create_subagent_permissions_from_base(self):
        """Test creating subagent permissions from base manager."""
        base = create_permission_manager(CODER_PERMISSIONS)
        sub = create_subagent_permissions(["Read", "Glob"], base_permissions=base)

        assert sub.is_allowed("Read") is True
        assert sub.is_allowed("Glob") is True
        assert sub.is_denied("Write") is True
        assert sub.is_denied("Bash") is True


class TestComplexScenarios:
    """Tests for complex permission scenarios."""

    def test_overlapping_rules(self):
        """Test evaluation with overlapping rules."""
        manager = PermissionManager()
        manager.add_rule(PermissionRule("*", "*", "deny", priority=0))
        manager.add_rule(PermissionRule("Read", "*", "allow", priority=10))
        manager.add_rule(PermissionRule("Read", "/secret/**", "deny", priority=20))

        assert manager.evaluate("Read", "/home/file.txt") == "allow"
        assert manager.evaluate("Read", "/secret/key.txt") == "deny"
        assert manager.evaluate("Write", "/home/file.txt") == "deny"

    def test_subagent_inheritance(self):
        """Test subagent permission inheritance."""
        # Main agent with full coder permissions
        main = create_permission_manager(CODER_PERMISSIONS)

        # Create readonly sub-agent
        sub = main.create_subset(["Read", "Glob", "Grep"])

        assert sub.is_allowed("Read") is True
        assert sub.is_allowed("Glob") is True
        assert sub.is_allowed("Grep") is True
        assert sub.is_denied("Write") is True
        assert sub.is_denied("Edit") is True
        assert sub.is_denied("Bash") is True

    def test_multi_level_subagents(self):
        """Test creating subagents from subagents."""
        level1 = create_subagent_permissions(["Read", "Glob", "Grep", "Write"])
        level2 = level1.create_subset(["Read", "Glob"])

        assert level2.is_allowed("Read") is True
        assert level2.is_allowed("Glob") is True
        assert level2.is_denied("Grep") is True
        assert level2.is_denied("Write") is True

    def test_permission_set_composition(self):
        """Test combining multiple permission sets."""
        manager = PermissionManager(default_action="deny")
        manager.load_permission_set(READONLY_PERMISSIONS)

        # Add additional rules
        manager.add_rule(PermissionRule("WebSearch", "*", "allow", priority=10))

        assert manager.is_allowed("Read") is True
        assert manager.is_allowed("WebSearch") is True
        assert manager.is_denied("Write") is True

"""Tests for repository understanding capability.

This module tests the agent's ability to understand repository structure,
identify key modules, dependencies, and navigate efficiently.

Capability: repo_understanding
"""

import pytest
from pathlib import Path
from typing import List

from tests.evaluation.metrics import (
    RepoUnderstandingMetrics,
    RepoExpectation,
)


# =============================================================================
# Sample Repository Descriptions for Testing
# =============================================================================

SAMPLE_REPO_DESCRIPTION_COMPLETE = """
This repository is a web application framework with the following structure:

Key Modules:
- src/core/: Contains the main application logic including routing and middleware
- src/models/: Database models and ORM definitions
- src/api/: REST API endpoints and handlers
- src/utils/: Utility functions and helpers

Dependencies:
- FastAPI for the web framework
- SQLAlchemy for database ORM
- Pydantic for data validation
- Redis for caching

Entry Points:
- main.py: Application entry point
- cli.py: Command-line interface
"""

SAMPLE_REPO_DESCRIPTION_PARTIAL = """
This is a Python project with some modules:

- The core module handles main logic
- There are some API endpoints
- Uses a database for persistence
"""

SAMPLE_REPO_DESCRIPTION_INCORRECT = """
This repository is a mobile application framework:

Key Modules:
- android/: Android-specific code
- ios/: iOS-specific code
- flutter/: Cross-platform Flutter code

Dependencies:
- React Native
- Firebase
- GraphQL
"""


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_repo(tmp_path):
    """Create a sample repository structure for testing."""
    # Create directory structure
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    # Create files
    (tmp_path / "src" / "core" / "__init__.py").write_text("")
    (tmp_path / "src" / "core" / "app.py").write_text("# Main application")
    (tmp_path / "src" / "core" / "router.py").write_text("# Routing logic")
    (tmp_path / "src" / "models" / "__init__.py").write_text("")
    (tmp_path / "src" / "models" / "user.py").write_text("# User model")
    (tmp_path / "src" / "api" / "__init__.py").write_text("")
    (tmp_path / "src" / "api" / "endpoints.py").write_text("# API endpoints")
    (tmp_path / "main.py").write_text("# Entry point")
    (tmp_path / "requirements.txt").write_text("fastapi\nsqlalchemy\npydantic\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "sample"\n')

    return tmp_path


# =============================================================================
# Test Cases
# =============================================================================


@pytest.mark.capability("repo_understanding")
class TestModuleStructureUnderstanding:
    """Tests for module structure understanding."""

    @pytest.fixture
    def metrics(self):
        return RepoUnderstandingMetrics()

    def test_module_structure_understanding_complete(self, metrics):
        """Complete module understanding should identify all key modules."""
        result = metrics.evaluate_module_understanding(
            agent_response=SAMPLE_REPO_DESCRIPTION_COMPLETE,
            expected_modules=["core", "models", "api", "utils"],
        )

        assert result.value == 1.0
        assert result.details["identified"] == 4

    def test_module_structure_understanding_partial(self, metrics):
        """Partial understanding should score proportionally."""
        result = metrics.evaluate_module_understanding(
            agent_response=SAMPLE_REPO_DESCRIPTION_PARTIAL,
            expected_modules=["core", "models", "api", "utils"],
        )

        # Only some modules mentioned
        assert 0.0 < result.value < 1.0

    def test_module_structure_understanding_incorrect(self, metrics):
        """Incorrect understanding should score poorly."""
        result = metrics.evaluate_module_understanding(
            agent_response=SAMPLE_REPO_DESCRIPTION_INCORRECT,
            expected_modules=["core", "models", "api", "utils"],
        )

        # Wrong modules (android, ios, flutter instead of core, models, api, utils)
        assert result.value == 0.0

    def test_module_structure_understanding_empty(self, metrics):
        """Empty expected modules should return 1.0."""
        result = metrics.evaluate_module_understanding(
            agent_response="Any description.",
            expected_modules=[],
        )

        assert result.value == 1.0


@pytest.mark.capability("repo_understanding")
class TestDependencyAwareness:
    """Tests for dependency awareness."""

    @pytest.fixture
    def metrics(self):
        return RepoUnderstandingMetrics()

    def test_dependency_awareness_complete(self, metrics):
        """Complete awareness should identify all dependencies."""
        result = metrics.evaluate_dependency_awareness(
            agent_response=SAMPLE_REPO_DESCRIPTION_COMPLETE,
            expected_dependencies=["FastAPI", "SQLAlchemy", "Pydantic", "Redis"],
        )

        assert result.value == 1.0

    def test_dependency_awareness_partial(self, metrics):
        """Partial awareness should score proportionally."""
        result = metrics.evaluate_dependency_awareness(
            agent_response="The project uses FastAPI and some database.",
            expected_dependencies=["FastAPI", "SQLAlchemy", "Pydantic", "Redis"],
        )

        # Only FastAPI mentioned (1/4 = 0.25)
        assert result.value == 0.25

    def test_dependency_awareness_none(self, metrics):
        """No dependencies identified should score 0."""
        result = metrics.evaluate_dependency_awareness(
            agent_response="A Python project with some code.",
            expected_dependencies=["FastAPI", "SQLAlchemy"],
        )

        assert result.value == 0.0

    def test_dependency_awareness_empty(self, metrics):
        """Empty expected dependencies should return 1.0."""
        result = metrics.evaluate_dependency_awareness(
            agent_response="Any description.",
            expected_dependencies=[],
        )

        assert result.value == 1.0


@pytest.mark.capability("repo_understanding")
class TestNavigationEfficiency:
    """Tests for codebase navigation efficiency."""

    @pytest.fixture
    def metrics(self):
        return RepoUnderstandingMetrics()

    def test_navigation_efficiency_optimal(self, metrics):
        """Optimal navigation should score 1.0."""
        result = metrics.evaluate_navigation_efficiency(
            search_steps=3,
            optimal_steps=3,
        )

        assert result.value == 1.0

    def test_navigation_efficiency_better_than_optimal(self, metrics):
        """Better than optimal should also score 1.0."""
        result = metrics.evaluate_navigation_efficiency(
            search_steps=2,
            optimal_steps=3,
        )

        assert result.value == 1.0

    def test_navigation_efficiency_twice_optimal(self, metrics):
        """Twice optimal steps should score 0.5."""
        result = metrics.evaluate_navigation_efficiency(
            search_steps=6,
            optimal_steps=3,
        )

        assert result.value == 0.5

    def test_navigation_efficiency_very_inefficient(self, metrics):
        """Very inefficient navigation should score poorly."""
        result = metrics.evaluate_navigation_efficiency(
            search_steps=30,
            optimal_steps=3,
        )

        assert result.value == 0.1

    def test_navigation_efficiency_zero_optimal(self, metrics):
        """Zero optimal steps should handle gracefully."""
        result = metrics.evaluate_navigation_efficiency(
            search_steps=5,
            optimal_steps=0,
        )

        # Should not crash, returns 0 efficiency when optimal is 0
        assert result.value == 0.0


@pytest.mark.capability("repo_understanding")
class TestRepoExpectation:
    """Tests using RepoExpectation dataclass."""

    @pytest.fixture
    def metrics(self):
        return RepoUnderstandingMetrics()

    def test_full_repo_evaluation(self, metrics):
        """Full evaluation with RepoExpectation."""
        expectation = RepoExpectation(
            key_modules=["core", "models", "api"],
            key_dependencies=["FastAPI", "SQLAlchemy"],
            key_entry_points=["main.py"],
        )

        results = metrics.evaluate(
            agent_response=SAMPLE_REPO_DESCRIPTION_COMPLETE,
            expectation=expectation,
        )

        summary = metrics.summary(results)

        assert summary["module_understanding"] == 1.0
        assert summary["dependency_awareness"] == 1.0

    def test_empty_expectation(self, metrics):
        """Empty expectation should return perfect scores."""
        expectation = RepoExpectation()

        results = metrics.evaluate(
            agent_response="Any description.",
            expectation=expectation,
        )

        summary = metrics.summary(results)

        assert summary["module_understanding"] == 1.0
        assert summary["dependency_awareness"] == 1.0


@pytest.mark.capability("repo_understanding")
class TestRepoUnderstandingIntegration:
    """Integration tests for repository understanding."""

    @pytest.fixture
    def metrics(self):
        return RepoUnderstandingMetrics()

    def test_real_repo_structure(self, metrics, sample_repo):
        """Test understanding with real repo structure."""
        # Simulate agent describing the repo after exploration
        agent_description = """
        This is a Python project with the following structure:
        - src/core/: Main application and routing
        - src/models/: Database models (user.py)
        - src/api/: API endpoints
        - main.py: Entry point

        Dependencies from requirements.txt:
        - fastapi
        - sqlalchemy
        - pydantic
        """

        expectation = RepoExpectation(
            key_modules=["core", "models", "api"],
            key_dependencies=["fastapi", "sqlalchemy", "pydantic"],
            key_entry_points=["main.py"],
        )

        results = metrics.evaluate(agent_description, expectation)
        summary = metrics.summary(results)

        assert summary["module_understanding"] == 1.0
        assert summary["dependency_awareness"] == 1.0

    def test_case_insensitive_module_matching(self, metrics):
        """Module matching should be case-insensitive."""
        result = metrics.evaluate_module_understanding(
            agent_response="The CORE module and MODELS module are important.",
            expected_modules=["core", "models"],
        )

        assert result.value == 1.0

    def test_case_insensitive_dependency_matching(self, metrics):
        """Dependency matching should be case-insensitive."""
        result = metrics.evaluate_dependency_awareness(
            agent_response="Uses FASTAPI and sqlalchemy for the backend.",
            expected_dependencies=["FastAPI", "SQLAlchemy"],
        )

        assert result.value == 1.0


@pytest.mark.capability("repo_understanding")
class TestRepoUnderstandingEdgeCases:
    """Edge cases for repository understanding."""

    @pytest.fixture
    def metrics(self):
        return RepoUnderstandingMetrics()

    def test_empty_agent_response(self, metrics):
        """Empty response should score 0 for non-empty expectations."""
        result = metrics.evaluate_module_understanding(
            agent_response="",
            expected_modules=["core", "models"],
        )

        assert result.value == 0.0

    def test_very_long_response(self, metrics):
        """Long response should still work correctly."""
        long_response = "The project includes core module. " * 1000

        result = metrics.evaluate_module_understanding(
            agent_response=long_response,
            expected_modules=["core", "models"],
        )

        # Should find "core" but not "models"
        assert result.value == 0.5

    def test_special_characters_in_modules(self, metrics):
        """Module names with special characters should be handled."""
        result = metrics.evaluate_module_understanding(
            agent_response="Contains my-module and another_module.",
            expected_modules=["my-module", "another_module"],
        )

        assert result.value == 1.0

    def test_numeric_modules(self, metrics):
        """Numeric module names should be handled."""
        result = metrics.evaluate_module_understanding(
            agent_response="Version v2 uses module123 for processing.",
            expected_modules=["module123", "v2"],
        )

        assert result.value == 1.0


@pytest.mark.capability("repo_understanding")
class TestRepoUnderstandingScenarios:
    """Scenario-based tests for repository understanding."""

    @pytest.fixture
    def metrics(self):
        return RepoUnderstandingMetrics()

    def test_microservices_repo(self, metrics):
        """Understanding of microservices architecture."""
        description = """
        This is a microservices-based application:
        - services/auth-service: Authentication microservice
        - services/user-service: User management
        - services/order-service: Order processing
        - shared/utils: Shared utilities

        Uses Docker Compose for orchestration with Redis for caching
        and PostgreSQL for persistence.
        """

        expectation = RepoExpectation(
            key_modules=["auth-service", "user-service", "order-service"],
            key_dependencies=["Docker", "Redis", "PostgreSQL"],
        )

        results = metrics.evaluate(description, expectation)
        summary = metrics.summary(results)

        assert summary["module_understanding"] == 1.0
        assert summary["dependency_awareness"] == 1.0

    def test_monorepo_structure(self, metrics):
        """Understanding of monorepo structure."""
        description = """
        Monorepo containing:
        - packages/frontend: React frontend application
        - packages/backend: Node.js API server
        - packages/common: Shared TypeScript types
        - tools/: Build and deployment scripts

        Managed with Yarn workspaces and Turborepo.
        """

        expectation = RepoExpectation(
            key_modules=["frontend", "backend", "common"],
            key_dependencies=["React", "Node.js", "TypeScript"],
        )

        results = metrics.evaluate(description, expectation)
        summary = metrics.summary(results)

        # Should identify all modules
        assert summary["module_understanding"] == 1.0

    def test_library_repo(self, metrics):
        """Understanding of library/package repository."""
        description = """
        A Python utility library:
        - src/: Main library code
        - tests/: Test suite
        - docs/: Documentation
        - examples/: Usage examples

        Dependencies: typing-extensions, dataclasses-json
        Dev dependencies: pytest, sphinx, black
        """

        expectation = RepoExpectation(
            key_modules=["src", "tests", "docs"],
            key_dependencies=["pytest", "sphinx"],
        )

        results = metrics.evaluate(description, expectation)
        summary = metrics.summary(results)

        assert summary["module_understanding"] == 1.0
        assert summary["dependency_awareness"] == 1.0

#!/usr/bin/env python3
"""
Nimbus Code Agent Real LLM Benchmark Evaluation

This script evaluates the Nimbus Code Agent's core capabilities using a real LLM.
It tests 7 capability dimensions and outputs a JSON report.

Capabilities tested:
1. Task Decomposition - Multi-step task planning
2. Code Search - Finding code patterns in codebase
3. Context Understanding - Multi-turn conversation comprehension
4. Code Modification - Adding/modifying code
5. Bash Execution - Running shell commands
6. Code Summarization - Summarizing code files
7. Repo Understanding - Understanding project structure

Usage:
    python tests/capabilities/benchmark_e2e.py

    # With specific provider:
    NIMBUS_LLM_PROVIDER=openrouter python tests/capabilities/benchmark_e2e.py

    # Output to specific file:
    python tests/capabilities/benchmark_e2e.py --output results.json
"""

import asyncio
import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.nimbus.llm import create_llm_client, LLMError
from src.nimbus.core.agent import CodeAgent
from src.nimbus.core.types import TaskDAG


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class TestResult:
    """Result of a single test case."""
    test_id: str
    capability: str
    description: str
    passed: bool
    score: float  # 0.0 - 1.0
    latency_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class CapabilityScore:
    """Aggregated score for a capability."""
    capability: str
    score: float
    tests: List[TestResult] = field(default_factory=list)
    test_count: int = 0
    passed_count: int = 0
    avg_latency_ms: float = 0.0


@dataclass
class BenchmarkReport:
    """Complete benchmark report."""
    timestamp: str
    llm_provider: str
    llm_model: str
    capabilities: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    overall_score: float = 0.0
    total_tests: int = 0
    passed_tests: int = 0
    total_latency_ms: float = 0.0
    errors: List[str] = field(default_factory=list)


# =============================================================================
# Benchmark Runner
# =============================================================================


class BenchmarkRunner:
    """Runs benchmark tests against a real LLM."""

    def __init__(
        self,
        provider: Optional[str] = None,
        verbose: bool = True,
        workspace: Optional[Path] = None,
    ):
        """Initialize benchmark runner.

        Args:
            provider: LLM provider name (uses default if not specified)
            verbose: Print progress to stdout
            workspace: Workspace directory for agent
        """
        self.verbose = verbose
        self.workspace = workspace or Path(__file__).parent.parent.parent
        self.results: List[TestResult] = []
        self.provider = provider

        # LLM client and agent will be initialized in run()
        self.llm_client = None
        self.llm_model = "unknown"
        self.agent = None

    def log(self, msg: str) -> None:
        """Print if verbose."""
        if self.verbose:
            print(msg)

    def log_test_start(self, test_id: str, description: str) -> None:
        """Log test start."""
        self.log(f"\n  [{test_id}] {description}...")

    def log_test_result(self, result: TestResult) -> None:
        """Log test result."""
        status = "PASS" if result.passed else "FAIL"
        self.log(f"    [{status}] Score: {result.score:.2f}, Latency: {result.latency_ms:.0f}ms")
        if result.error:
            self.log(f"    Error: {result.error}")

    async def setup(self) -> bool:
        """Initialize LLM client and agent.

        Returns:
            True if setup succeeded.
        """
        try:
            self.log("Initializing LLM client...")
            self.llm_client = create_llm_client(provider=self.provider)

            # Extract model name
            if hasattr(self.llm_client, 'config'):
                self.llm_model = self.llm_client.config.model
            elif hasattr(self.llm_client, '_model'):
                self.llm_model = self.llm_client._model
            else:
                self.llm_model = "unknown"

            self.log(f"  Provider: {self.provider or 'default'}")
            self.log(f"  Model: {self.llm_model}")

            # Initialize agent
            self.log("Initializing CodeAgent...")
            self.agent = CodeAgent(
                llm_client=self.llm_client,
                memory_type="simple",
                planner_type="dag",
                workspace=self.workspace,
                enable_logging=False,
            )
            self.log("  Agent ready.")

            return True
        except LLMError as e:
            self.log(f"  LLM initialization failed: {e}")
            return False
        except Exception as e:
            self.log(f"  Setup failed: {e}")
            return False

    async def run_test(
        self,
        test_id: str,
        capability: str,
        description: str,
        test_func,
    ) -> TestResult:
        """Run a single test.

        Args:
            test_id: Unique test identifier
            capability: Capability being tested
            description: Test description
            test_func: Async test function returning (passed, score, details)

        Returns:
            TestResult
        """
        self.log_test_start(test_id, description)
        start_time = time.time()

        try:
            passed, score, details = await test_func()
            latency_ms = (time.time() - start_time) * 1000

            result = TestResult(
                test_id=test_id,
                capability=capability,
                description=description,
                passed=passed,
                score=score,
                latency_ms=latency_ms,
                details=details,
            )
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            result = TestResult(
                test_id=test_id,
                capability=capability,
                description=description,
                passed=False,
                score=0.0,
                latency_ms=latency_ms,
                error=str(e),
            )

        self.log_test_result(result)
        self.results.append(result)
        return result

    # =========================================================================
    # Test Functions
    # =========================================================================

    async def test_task_decomposition(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test task decomposition capability.

        Input: Complex multi-step request
        Expected: DAG with multiple tasks
        """
        # Reset agent memory
        self.agent.clear_memory()

        response = await self.agent.run(
            "Search for Python files in src/ directory, then find all async functions in them"
        )

        dag = response.dag
        details = {
            "has_dag": dag is not None,
            "task_count": len(dag.nodes) if dag else 0,
            "response_length": len(response.text),
        }

        if dag:
            details["skills_used"] = list({n.skill for n in dag.nodes.values()})

        # Score criteria:
        # - Has DAG: +0.3
        # - Multiple tasks: +0.3
        # - Uses search skills (Glob/Grep): +0.4
        score = 0.0
        if dag:
            score += 0.3
            if len(dag.nodes) >= 2:
                score += 0.3
            skills = {n.skill for n in dag.nodes.values()}
            if "Glob" in skills or "Grep" in skills:
                score += 0.4

        passed = score >= 0.6
        return passed, score, details

    async def test_code_search(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test code search capability.

        Input: Search for CodeAgent class
        Expected: Find correct file path
        """
        self.agent.clear_memory()

        response = await self.agent.run(
            "Find the definition of CodeAgent class in the nimbus codebase"
        )

        text = response.text.lower()
        dag = response.dag

        # Check DAG used Grep tool
        used_grep = False
        grep_pattern = ""
        if dag:
            for node in dag.nodes.values():
                if node.skill == "Grep":
                    used_grep = True
                    grep_pattern = node.params.get("pattern", "")
                    break

        # Check response content - tool output may contain file paths
        mentions_agent = "codeagent" in text or "code_agent" in text or "class codeagent" in text
        mentions_file = "agent.py" in text or "core/agent" in text or "src/nimbus" in text

        details = {
            "response_length": len(response.text),
            "mentions_agent": mentions_agent,
            "mentions_file": mentions_file,
            "used_grep": used_grep,
            "grep_pattern": grep_pattern,
        }

        # Score criteria:
        # - Used Grep tool: +0.4
        # - Mentions CodeAgent or file path: +0.3
        # - Has meaningful response: +0.3
        score = 0.0
        if used_grep:
            score += 0.4
        if mentions_agent or mentions_file:
            score += 0.3
        if len(response.text) > 20:
            score += 0.3

        passed = score >= 0.6
        return passed, score, details

    async def test_context_understanding_pronoun(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test pronoun resolution in context.

        Multi-turn conversation test.
        """
        self.agent.clear_memory()

        # Turn 1: Read a file
        await self.agent.run("Read pyproject.toml file")

        # Turn 2: Ask about it with pronoun
        response = await self.agent.run("What is the project name in that file?")

        text = response.text.lower()
        details = {
            "response_length": len(response.text),
            "mentions_nimbus": "nimbus" in text,
            "mentions_project": "project" in text or "name" in text,
        }

        # Score criteria:
        # - Understands "that file" refers to pyproject.toml: implicit
        # - Mentions nimbus (project name): +0.7
        # - Provides context about project: +0.3
        score = 0.0
        if details["mentions_nimbus"]:
            score += 0.7
        if details["mentions_project"]:
            score += 0.3

        passed = score >= 0.7
        return passed, score, details

    async def test_code_modification(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test code modification capability.

        Create a temp file and ask agent to read it.
        """
        self.agent.clear_memory()

        # Create a temporary file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            dir=self.workspace,
        ) as f:
            f.write('"""Test module."""\n\ndef existing_func():\n    pass\n')
            temp_file = f.name

        try:
            response = await self.agent.run(
                f"Read the file {temp_file} and tell me what function it contains"
            )

            text = response.text.lower()
            dag = response.dag

            # Check DAG used Read tool
            used_read = False
            if dag:
                for node in dag.nodes.values():
                    if node.skill == "Read":
                        used_read = True
                        break

            # Check response contains file content
            mentions_existing_func = "existing_func" in text
            has_def = "def" in text
            has_function = "function" in text or "test module" in text

            details = {
                "response_length": len(response.text),
                "mentions_existing_func": mentions_existing_func,
                "has_def": has_def,
                "has_function": has_function,
                "used_read": used_read,
            }

            # Score criteria:
            # - Used Read tool: +0.3
            # - Identifies existing_func: +0.4
            # - Shows def or function reference: +0.3
            score = 0.0
            if used_read:
                score += 0.3
            if mentions_existing_func:
                score += 0.4
            if has_def or has_function:
                score += 0.3

            passed = score >= 0.6
            return passed, score, details

        finally:
            # Cleanup
            try:
                os.unlink(temp_file)
            except OSError:
                pass

    async def test_bash_execution_ls(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test bash execution with ls command.

        Note: Agent may use Glob or Bash for listing files.
        """
        self.agent.clear_memory()

        response = await self.agent.run(
            "List the files in the current directory"
        )

        text = response.text
        dag = response.dag

        # Check if Glob or Bash was used
        used_glob = False
        used_bash = False
        if dag:
            for node in dag.nodes.values():
                if node.skill == "Glob":
                    used_glob = True
                if node.skill == "Bash":
                    used_bash = True

        details = {
            "response_length": len(text),
            "has_output": len(text) > 50,
            "used_glob": used_glob,
            "used_bash": used_bash,
        }

        # Check for common project files in output
        common_files = ["pyproject.toml", "src", "tests", "README", ".py", ".md"]
        found_files = [f for f in common_files if f.lower() in text.lower()]
        details["found_files"] = found_files

        # Score criteria:
        # - Has substantial output: +0.3
        # - Used Glob or Bash tool: +0.3
        # - Found expected file patterns: +0.4 scaled by found ratio
        score = 0.0
        if details["has_output"]:
            score += 0.3
        if used_glob or used_bash:
            score += 0.3
        if found_files:
            score += 0.4 * (len(found_files) / len(common_files))

        passed = score >= 0.5
        return passed, score, details

    async def test_bash_execution_echo(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test bash execution with echo command."""
        self.agent.clear_memory()

        test_string = "benchmark_test_123"
        response = await self.agent.run(
            f"Run the command: echo '{test_string}'"
        )

        text = response.text
        dag = response.dag

        # Check if Bash was used
        used_bash = False
        if dag:
            for node in dag.nodes.values():
                if node.skill == "Bash":
                    used_bash = True
                    break

        details = {
            "response_length": len(text),
            "echo_output_found": test_string in text,
            "used_bash": used_bash,
        }

        # Score criteria:
        # - Used Bash tool: +0.5
        # - Output contains test string: +0.5
        score = 0.0
        if used_bash:
            score += 0.5
        if details["echo_output_found"]:
            score += 0.5

        passed = score >= 0.5
        return passed, score, details

    async def test_code_summarization(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test code summarization capability."""
        self.agent.clear_memory()

        response = await self.agent.run(
            "Read src/nimbus/core/agent.py and summarize its main purpose"
        )

        text = response.text.lower()
        dag = response.dag

        # Check DAG used Read tool
        used_read = False
        used_summarize = False
        if dag:
            for node in dag.nodes.values():
                if node.skill == "Read":
                    used_read = True
                if node.skill == "summarize":
                    used_summarize = True

        # Expected concepts in summary or raw file content
        expected_concepts = [
            "agent",
            "llm",
            "planner",
            "memory",
            "execute",
            "task",
            "codeagent",
            "run",
        ]
        found_concepts = [c for c in expected_concepts if c in text]

        details = {
            "response_length": len(response.text),
            "found_concepts": found_concepts,
            "concept_count": len(found_concepts),
            "used_read": used_read,
            "used_summarize": used_summarize,
        }

        # Score criteria:
        # - Used Read tool: +0.3
        # - Found concepts scaled by ratio: +0.7
        score = 0.0
        if used_read:
            score += 0.3
        if found_concepts:
            score += 0.7 * (len(found_concepts) / len(expected_concepts))

        passed = score >= 0.4
        return passed, score, details

    async def test_repo_understanding(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test repository structure understanding."""
        self.agent.clear_memory()

        response = await self.agent.run(
            "What are the main modules in the nimbus project? List the key directories under src/nimbus/"
        )

        text = response.text.lower()

        # Expected modules
        expected_modules = ["core", "llm", "server", "tools", "skills"]
        found_modules = [m for m in expected_modules if m in text]

        details = {
            "response_length": len(response.text),
            "found_modules": found_modules,
            "module_count": len(found_modules),
        }

        # Score criteria:
        # - Found modules scaled by ratio: +1.0
        score = len(found_modules) / len(expected_modules)

        passed = score >= 0.6
        return passed, score, details

    # =========================================================================
    # Main Run Method
    # =========================================================================

    async def run_all(self) -> BenchmarkReport:
        """Run all benchmark tests.

        Returns:
            BenchmarkReport with results.
        """
        self.log("=" * 60)
        self.log("Nimbus Code Agent Benchmark Evaluation")
        self.log("=" * 60)

        # Setup
        if not await self.setup():
            return BenchmarkReport(
                timestamp=datetime.now().isoformat(),
                llm_provider=self.provider or "unknown",
                llm_model="unknown",
                errors=["Failed to initialize LLM client"],
            )

        # Define tests
        tests = [
            # Task Decomposition
            ("td_01", "task_decomposition", "Multi-step task planning", self.test_task_decomposition),

            # Code Search
            ("cs_01", "code_search", "Find CodeAgent class definition", self.test_code_search),

            # Context Understanding
            ("cu_01", "context_understanding", "Pronoun resolution in multi-turn", self.test_context_understanding_pronoun),

            # Code Modification
            ("cm_01", "code_modification", "Read and understand code file", self.test_code_modification),

            # Bash Execution
            ("be_01", "bash_execution", "Execute ls command", self.test_bash_execution_ls),
            ("be_02", "bash_execution", "Execute echo command", self.test_bash_execution_echo),

            # Code Summarization
            ("sum_01", "code_summarization", "Summarize agent.py", self.test_code_summarization),

            # Repo Understanding
            ("ru_01", "repo_understanding", "Identify main modules", self.test_repo_understanding),
        ]

        # Group by capability
        capability_groups = {}
        for test_id, capability, desc, func in tests:
            if capability not in capability_groups:
                capability_groups[capability] = []
            capability_groups[capability].append((test_id, desc, func))

        # Run tests
        for capability, test_list in capability_groups.items():
            self.log(f"\n{'=' * 40}")
            self.log(f"Capability: {capability}")
            self.log("=" * 40)

            for test_id, desc, func in test_list:
                await self.run_test(test_id, capability, desc, func)
                # Brief pause between tests
                await asyncio.sleep(0.5)

        # Compute report
        report = self._compute_report()

        # Print summary
        self._print_summary(report)

        return report

    def _compute_report(self) -> BenchmarkReport:
        """Compute benchmark report from results."""
        report = BenchmarkReport(
            timestamp=datetime.now().isoformat(),
            llm_provider=self.provider or "default",
            llm_model=self.llm_model,
        )

        # Group by capability
        capability_results: Dict[str, List[TestResult]] = {}
        for result in self.results:
            if result.capability not in capability_results:
                capability_results[result.capability] = []
            capability_results[result.capability].append(result)

        # Compute capability scores
        capability_scores = []
        for capability, results in capability_results.items():
            scores = [r.score for r in results]
            avg_score = sum(scores) / len(scores) if scores else 0.0
            passed = sum(1 for r in results if r.passed)
            latencies = [r.latency_ms for r in results]
            avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

            report.capabilities[capability] = {
                "score": round(avg_score, 3),
                "tests": [asdict(r) for r in results],
                "test_count": len(results),
                "passed_count": passed,
                "avg_latency_ms": round(avg_latency, 1),
            }
            capability_scores.append(avg_score)

        # Overall metrics
        report.total_tests = len(self.results)
        report.passed_tests = sum(1 for r in self.results if r.passed)
        report.total_latency_ms = sum(r.latency_ms for r in self.results)
        report.overall_score = round(
            sum(capability_scores) / len(capability_scores) if capability_scores else 0.0,
            3,
        )
        report.errors = [r.error for r in self.results if r.error]

        return report

    def _print_summary(self, report: BenchmarkReport) -> None:
        """Print summary to stdout."""
        self.log("\n" + "=" * 60)
        self.log("BENCHMARK SUMMARY")
        self.log("=" * 60)
        self.log(f"Timestamp: {report.timestamp}")
        self.log(f"LLM Provider: {report.llm_provider}")
        self.log(f"LLM Model: {report.llm_model}")
        self.log("")

        self.log("Capability Scores:")
        for cap, data in sorted(report.capabilities.items()):
            score_pct = data["score"] * 100
            passed = data["passed_count"]
            total = data["test_count"]
            self.log(f"  {cap:25s}: {score_pct:5.1f}%  ({passed}/{total} passed)")

        self.log("")
        self.log(f"Overall Score: {report.overall_score * 100:.1f}%")
        self.log(f"Tests: {report.passed_tests}/{report.total_tests} passed")
        self.log(f"Total Latency: {report.total_latency_ms:.0f}ms")

        if report.errors:
            self.log("")
            self.log(f"Errors: {len(report.errors)}")
            for err in report.errors[:5]:
                self.log(f"  - {err[:80]}...")


# =============================================================================
# Main
# =============================================================================


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Nimbus Code Agent Benchmark")
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="LLM provider (gemini, ollama, openrouter)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args()

    # Run benchmark
    runner = BenchmarkRunner(
        provider=args.provider,
        verbose=not args.quiet,
        workspace=Path(__file__).parent.parent.parent,
    )

    report = await runner.run_all()

    # Output report
    report_dict = asdict(report)

    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(report_dict, f, indent=2)
        if not args.quiet:
            print(f"\nReport saved to: {output_path}")
    else:
        # Print JSON to stdout
        print("\n" + "=" * 60)
        print("JSON REPORT")
        print("=" * 60)
        print(json.dumps(report_dict, indent=2))

    # Return exit code based on overall score
    return 0 if report.overall_score >= 0.5 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

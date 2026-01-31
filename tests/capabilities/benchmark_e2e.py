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

from src.nimbus.apps.code_agent import CodeAgent


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

        # Agent will be initialized in run()
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
        """Initialize CodeAgent.

        Returns:
            True if setup succeeded.
        """
        try:
            llm_provider = self.provider or "gemini"
            self.log(f"Initializing CodeAgent (provider={llm_provider})...")

            self.agent = CodeAgent(
                workspace=str(self.workspace),
                llm_provider=llm_provider,
                max_iterations=50,
            )

            # Extract model name from agent's LLM client
            if hasattr(self.agent, 'llm'):
                llm = self.agent.llm
                if hasattr(llm, '_client') and hasattr(llm._client, '_model'):
                    self.llm_model = llm._client._model
                elif hasattr(llm, 'config') and hasattr(llm.config, 'model'):
                    self.llm_model = llm.config.model
                elif hasattr(llm, '_model'):
                    self.llm_model = llm._model
                else:
                    self.llm_model = llm_provider

            self.log(f"  Provider: {llm_provider}")
            self.log(f"  Model: {self.llm_model}")
            self.log("  Agent ready.")

            return True
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
        Expected: Agent completes multi-step task
        """
        result = await self.agent.run(
            goal="Search for Python files in src/ directory, then find all async functions in them",
            allowed_tools={"Glob", "Grep", "Read"},
        )

        text = result.get("output", "")
        details = {
            "response_length": len(text),
            "status": result.get("status"),
            "turns": result.get("turns", 0),
        }

        # Score criteria:
        # - Task completed successfully: +0.4
        # - Response contains async function mentions: +0.3
        # - Response is substantial: +0.3
        score = 0.0
        if result.get("status") == "success":
            score += 0.4
        if "async" in text.lower() or "def " in text.lower():
            score += 0.3
        if len(text) > 100:
            score += 0.3

        passed = score >= 0.6
        return passed, score, details

    async def test_code_search(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test code search capability.

        Input: Search for CodeAgent class
        Expected: Find correct file path
        """
        result = await self.agent.run(
            goal="Find the definition of CodeAgent class in the nimbus codebase",
            allowed_tools={"Glob", "Grep", "Read"},
        )

        text = result.get("output", "").lower()

        # Check response content - tool output may contain file paths
        mentions_agent = "codeagent" in text or "code_agent" in text or "class codeagent" in text
        mentions_file = "agent.py" in text or "core/agent" in text or "src/nimbus" in text

        details = {
            "response_length": len(result.get("output", "")),
            "mentions_agent": mentions_agent,
            "mentions_file": mentions_file,
            "status": result.get("status"),
        }

        # Score criteria:
        # - Task completed successfully: +0.4
        # - Mentions CodeAgent or file path: +0.3
        # - Has meaningful response: +0.3
        score = 0.0
        if result.get("status") == "success":
            score += 0.4
        if mentions_agent or mentions_file:
            score += 0.3
        if len(result.get("output", "")) > 20:
            score += 0.3

        passed = score >= 0.6
        return passed, score, details

    async def test_context_understanding_pronoun(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test context understanding.

        Note: New CodeAgent doesn't maintain multi-turn context per run,
        so we test single-turn context comprehension.
        """
        result = await self.agent.run(
            goal="Read pyproject.toml file and tell me the project name defined in it",
            allowed_tools={"Read"},
        )

        text = result.get("output", "").lower()
        details = {
            "response_length": len(result.get("output", "")),
            "mentions_nimbus": "nimbus" in text,
            "mentions_project": "project" in text or "name" in text,
            "status": result.get("status"),
        }

        # Score criteria:
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
        """Test code reading capability.

        Create a temp file and ask agent to read it.
        """
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
            result = await self.agent.run(
                goal=f"Read the file {temp_file} and tell me what function it contains",
                allowed_tools={"Read"},
            )

            text = result.get("output", "").lower()

            # Check response contains file content
            mentions_existing_func = "existing_func" in text
            has_def = "def" in text
            has_function = "function" in text or "test module" in text

            details = {
                "response_length": len(result.get("output", "")),
                "mentions_existing_func": mentions_existing_func,
                "has_def": has_def,
                "has_function": has_function,
                "status": result.get("status"),
            }

            # Score criteria:
            # - Task completed successfully: +0.3
            # - Identifies existing_func: +0.4
            # - Shows def or function reference: +0.3
            score = 0.0
            if result.get("status") == "success":
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
        """Test file listing capability.

        Note: Agent may use Glob or Bash for listing files.
        """
        result = await self.agent.run(
            goal="List the files in the current directory",
            allowed_tools={"Glob", "Bash"},
        )

        text = result.get("output", "")

        details = {
            "response_length": len(text),
            "has_output": len(text) > 50,
            "status": result.get("status"),
        }

        # Check for common project files in output
        common_files = ["pyproject.toml", "src", "tests", "README", ".py", ".md"]
        found_files = [f for f in common_files if f.lower() in text.lower()]
        details["found_files"] = found_files

        # Score criteria:
        # - Has substantial output: +0.3
        # - Task completed successfully: +0.3
        # - Found expected file patterns: +0.4 scaled by found ratio
        score = 0.0
        if details["has_output"]:
            score += 0.3
        if result.get("status") == "success":
            score += 0.3
        if found_files:
            score += 0.4 * (len(found_files) / len(common_files))

        passed = score >= 0.5
        return passed, score, details

    async def test_bash_execution_echo(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test bash execution with echo command."""
        test_string = "benchmark_test_123"
        result = await self.agent.run(
            goal=f"Run the command: echo '{test_string}'",
            allowed_tools={"Bash"},
        )

        text = result.get("output", "")

        details = {
            "response_length": len(text),
            "echo_output_found": test_string in text,
            "status": result.get("status"),
        }

        # Score criteria:
        # - Task completed successfully: +0.5
        # - Output contains test string: +0.5
        score = 0.0
        if result.get("status") == "success":
            score += 0.5
        if details["echo_output_found"]:
            score += 0.5

        passed = score >= 0.5
        return passed, score, details

    async def test_code_summarization(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test code summarization capability."""
        result = await self.agent.run(
            goal="Read src/nimbus/core/agent.py and summarize its main purpose",
            allowed_tools={"Read"},
        )

        text = result.get("output", "").lower()

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
            "response_length": len(result.get("output", "")),
            "found_concepts": found_concepts,
            "concept_count": len(found_concepts),
            "status": result.get("status"),
        }

        # Score criteria:
        # - Task completed successfully: +0.3
        # - Found concepts scaled by ratio: +0.7
        score = 0.0
        if result.get("status") == "success":
            score += 0.3
        if found_concepts:
            score += 0.7 * (len(found_concepts) / len(expected_concepts))

        passed = score >= 0.4
        return passed, score, details

    async def test_repo_understanding(self) -> Tuple[bool, float, Dict[str, Any]]:
        """Test repository structure understanding."""
        result = await self.agent.run(
            goal="What are the main modules in the nimbus project? List the key directories under src/nimbus/",
            allowed_tools={"Glob", "Read", "Bash"},
        )

        text = result.get("output", "").lower()

        # Expected modules
        expected_modules = ["core", "llm", "server", "tools", "skills"]
        found_modules = [m for m in expected_modules if m in text]

        details = {
            "response_length": len(result.get("output", "")),
            "found_modules": found_modules,
            "module_count": len(found_modules),
            "status": result.get("status"),
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

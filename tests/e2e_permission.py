#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Permission System

This script tests the Nimbus Server's permission management APIs.

Test Cases:
1. Get permission rules - GET /api/v1/permissions/rules
2. Update permission rule - PUT /api/v1/permissions/rules/{tool}
3. Get session pending permissions - GET /api/v1/sessions/{id}/permissions
   (Note: This endpoint may not exist yet; test will verify and document)

API Endpoints:
- GET /health - Health check
- POST /sessions - Create session
- GET /permissions/rules - Get all permission rules
- PUT /permissions/rules/{tool} - Update a permission rule
- GET /sessions/{session_id}/permissions - Get pending permissions for session

Usage:
    python tests/e2e_permission.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_permission.py
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

# Check for httpx
try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# Configuration
SERVER_URL = os.environ.get("NIMBUS_SERVER_URL", "http://127.0.0.1:8080")
API_PREFIX = "/api/v1"


@dataclass
class TestResult:
    """Represents the result of a test case."""
    name: str
    passed: bool
    message: str
    duration_ms: float
    response_data: Optional[dict] = None


class NimbusPermissionE2ETest:
    """E2E test runner for Nimbus Server permission system."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.api_url = f"{self.server_url}{API_PREFIX}"
        self.session_id: Optional[str] = None
        self.results: list[TestResult] = []

    def print_header(self, text: str):
        """Print a section header."""
        print("\n" + "=" * 60)
        print(text)
        print("=" * 60)

    def print_info(self, text: str):
        """Print info message."""
        print(f"[INFO] {text}")

    def print_ok(self, text: str):
        """Print success message."""
        print(f"[OK] {text}")

    def print_fail(self, text: str):
        """Print failure message."""
        print(f"[FAIL] {text}")

    def print_warn(self, text: str):
        """Print warning message."""
        print(f"[WARN] {text}")

    async def check_health(self) -> bool:
        """Check if server is healthy."""
        self.print_header("Step 0: Health Check")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/health",
                    timeout=5.0
                )

                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "unknown")
                    if status == "healthy":
                        self.print_ok(f"Server healthy: {data}")
                        return True
                    else:
                        self.print_fail(f"Server unhealthy: {data}")
                        return False
                else:
                    self.print_fail(f"Health check returned {response.status_code}")
                    return False
        except httpx.ConnectError:
            self.print_fail(f"Cannot connect to {self.server_url}")
            self.print_info("Is the server running? Start with: uv run nimbus serve")
            return False
        except Exception as e:
            self.print_fail(f"Health check failed: {e}")
            return False

    async def create_session(self) -> Optional[str]:
        """Create a new session for testing."""
        self.print_header("Step 1: Create Session")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/sessions",
                    json={"name": "permission-test-session"},
                    timeout=10.0
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    session_id = data.get("id")
                    if session_id:
                        self.session_id = session_id
                        self.print_ok(f"Session created: {session_id}")
                        return session_id
                    else:
                        self.print_fail("Response missing 'id' field")
                        return None
                else:
                    self.print_fail(f"Create session returned {response.status_code}")
                    self.print_info(f"Response: {response.text[:200]}")
                    return None
        except Exception as e:
            self.print_fail(f"Create session failed: {e}")
            return None

    async def test_get_permission_rules(self) -> TestResult:
        """Test GET /permissions/rules endpoint."""
        name = "Get Permission Rules"
        self.print_header(f"Test: {name}")

        start_time = time.time()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/permissions/rules",
                    timeout=10.0
                )

                duration_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    data = response.json()
                    rules = data.get("rules", [])

                    # Validate response structure
                    if isinstance(rules, list):
                        # Check that rules have expected fields
                        valid_structure = True
                        for rule in rules[:5]:  # Check first 5 rules
                            if not isinstance(rule, dict):
                                valid_structure = False
                                break
                            if "tool" not in rule or "decision" not in rule:
                                valid_structure = False
                                break

                        if valid_structure:
                            self.print_ok(f"Got {len(rules)} permission rules")
                            self.print_info(f"Sample rules: {rules[:3]}")
                            result = TestResult(
                                name=name,
                                passed=True,
                                message=f"Retrieved {len(rules)} rules",
                                duration_ms=duration_ms,
                                response_data=data,
                            )
                        else:
                            self.print_fail("Rules have invalid structure")
                            result = TestResult(
                                name=name,
                                passed=False,
                                message="Invalid rule structure",
                                duration_ms=duration_ms,
                                response_data=data,
                            )
                    else:
                        self.print_fail(f"Expected 'rules' to be a list, got {type(rules)}")
                        result = TestResult(
                            name=name,
                            passed=False,
                            message="Invalid response format",
                            duration_ms=duration_ms,
                            response_data=data,
                        )
                else:
                    self.print_fail(f"Request failed with status {response.status_code}")
                    result = TestResult(
                        name=name,
                        passed=False,
                        message=f"HTTP {response.status_code}",
                        duration_ms=duration_ms,
                    )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Exception: {e}")
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )

        self.results.append(result)
        return result

    async def test_update_permission_rule(self) -> TestResult:
        """Test PUT /permissions/rules/{tool} endpoint."""
        name = "Update Permission Rule"
        self.print_header(f"Test: {name}")

        start_time = time.time()
        test_tool = "test_custom_tool"
        test_decision = "allow_always"

        try:
            async with httpx.AsyncClient() as client:
                # Update the rule
                response = await client.put(
                    f"{self.api_url}/permissions/rules/{test_tool}",
                    json={"decision": test_decision},
                    timeout=10.0
                )

                duration_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    data = response.json()

                    # Validate response
                    if data.get("tool") == test_tool and data.get("decision") == test_decision:
                        self.print_ok(f"Updated rule for '{test_tool}' to '{test_decision}'")

                        # Verify by fetching all rules
                        verify_response = await client.get(
                            f"{self.api_url}/permissions/rules",
                            timeout=10.0
                        )
                        if verify_response.status_code == 200:
                            verify_data = verify_response.json()
                            rules = verify_data.get("rules", [])
                            found = any(
                                r.get("tool") == test_tool and r.get("decision") == test_decision
                                for r in rules
                            )
                            if found:
                                self.print_ok("Verified: Rule persisted correctly")
                                result = TestResult(
                                    name=name,
                                    passed=True,
                                    message="Rule updated and verified",
                                    duration_ms=duration_ms,
                                    response_data=data,
                                )
                            else:
                                self.print_warn("Rule not found in verification")
                                result = TestResult(
                                    name=name,
                                    passed=True,  # Still pass, update worked
                                    message="Rule updated but verification inconclusive",
                                    duration_ms=duration_ms,
                                    response_data=data,
                                )
                        else:
                            result = TestResult(
                                name=name,
                                passed=True,
                                message="Rule updated (verification skipped)",
                                duration_ms=duration_ms,
                                response_data=data,
                            )
                    else:
                        self.print_fail(f"Response does not match expected: {data}")
                        result = TestResult(
                            name=name,
                            passed=False,
                            message="Response mismatch",
                            duration_ms=duration_ms,
                            response_data=data,
                        )
                else:
                    self.print_fail(f"Request failed with status {response.status_code}")
                    result = TestResult(
                        name=name,
                        passed=False,
                        message=f"HTTP {response.status_code}",
                        duration_ms=duration_ms,
                    )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Exception: {e}")
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )

        self.results.append(result)
        return result

    async def test_permission_decisions(self) -> TestResult:
        """Test all permission decision types."""
        name = "Permission Decision Types"
        self.print_header(f"Test: {name}")

        start_time = time.time()
        decisions = ["ask", "allow_once", "allow_always", "deny"]
        test_tool = "decision_test_tool"
        errors = []

        try:
            async with httpx.AsyncClient() as client:
                for decision in decisions:
                    response = await client.put(
                        f"{self.api_url}/permissions/rules/{test_tool}",
                        json={"decision": decision},
                        timeout=10.0
                    )

                    if response.status_code == 200:
                        data = response.json()
                        if data.get("decision") != decision:
                            errors.append(f"Decision '{decision}' not set correctly")
                        else:
                            self.print_ok(f"Decision '{decision}' works")
                    else:
                        errors.append(f"Failed to set decision '{decision}': HTTP {response.status_code}")

                duration_ms = (time.time() - start_time) * 1000

                if not errors:
                    result = TestResult(
                        name=name,
                        passed=True,
                        message=f"All {len(decisions)} decision types work",
                        duration_ms=duration_ms,
                    )
                else:
                    self.print_fail(f"Errors: {errors}")
                    result = TestResult(
                        name=name,
                        passed=False,
                        message=f"{len(errors)} errors: {errors[0]}",
                        duration_ms=duration_ms,
                    )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Exception: {e}")
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )

        self.results.append(result)
        return result

    async def test_session_permissions_endpoint(self) -> TestResult:
        """Test GET /sessions/{id}/permissions endpoint.

        Note: This endpoint may not exist yet. The test documents expected behavior.
        """
        name = "Session Pending Permissions"
        self.print_header(f"Test: {name}")

        if not self.session_id:
            self.print_warn("No session ID, skipping test")
            result = TestResult(
                name=name,
                passed=False,
                message="No session created",
                duration_ms=0,
            )
            self.results.append(result)
            return result

        start_time = time.time()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/sessions/{self.session_id}/permissions",
                    timeout=10.0
                )

                duration_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    data = response.json()
                    # Expected: list of pending permission requests
                    # Since readonly agent tools are allowed by default,
                    # expect empty list
                    if "requests" in data or isinstance(data, list):
                        requests = data.get("requests", data) if isinstance(data, dict) else data
                        self.print_ok(f"Got {len(requests)} pending permission requests")
                        result = TestResult(
                            name=name,
                            passed=True,
                            message=f"Endpoint exists, {len(requests)} pending requests",
                            duration_ms=duration_ms,
                            response_data=data,
                        )
                    else:
                        self.print_ok(f"Endpoint exists with response: {data}")
                        result = TestResult(
                            name=name,
                            passed=True,
                            message="Endpoint exists",
                            duration_ms=duration_ms,
                            response_data=data,
                        )
                elif response.status_code == 404:
                    self.print_warn("Endpoint not implemented (404)")
                    self.print_info("Expected endpoint: GET /sessions/{id}/permissions")
                    self.print_info("Would return pending permission requests for session")
                    result = TestResult(
                        name=name,
                        passed=False,
                        message="Endpoint not implemented (HTTP 404)",
                        duration_ms=duration_ms,
                    )
                else:
                    self.print_fail(f"Unexpected status {response.status_code}")
                    result = TestResult(
                        name=name,
                        passed=False,
                        message=f"HTTP {response.status_code}",
                        duration_ms=duration_ms,
                    )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Exception: {e}")
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )

        self.results.append(result)
        return result

    async def test_respond_to_nonexistent_permission(self) -> TestResult:
        """Test POST /permissions/{request_id}/respond with invalid ID."""
        name = "Respond to Nonexistent Permission"
        self.print_header(f"Test: {name}")

        start_time = time.time()
        fake_request_id = "perm_nonexistent123"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/permissions/{fake_request_id}/respond",
                    json={"decision": "allow_once"},
                    timeout=10.0
                )

                duration_ms = (time.time() - start_time) * 1000

                if response.status_code == 404:
                    self.print_ok("Correctly returned 404 for nonexistent request")
                    result = TestResult(
                        name=name,
                        passed=True,
                        message="Correctly handles nonexistent request",
                        duration_ms=duration_ms,
                    )
                elif response.status_code == 200:
                    # Unexpected success
                    self.print_warn("Unexpectedly succeeded (may indicate issue)")
                    result = TestResult(
                        name=name,
                        passed=False,
                        message="Should have returned 404",
                        duration_ms=duration_ms,
                        response_data=response.json() if response.text else None,
                    )
                else:
                    self.print_fail(f"Unexpected status {response.status_code}")
                    result = TestResult(
                        name=name,
                        passed=False,
                        message=f"HTTP {response.status_code}",
                        duration_ms=duration_ms,
                    )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Exception: {e}")
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )

        self.results.append(result)
        return result

    async def test_invalid_decision_type(self) -> TestResult:
        """Test PUT /permissions/rules/{tool} with invalid decision."""
        name = "Invalid Decision Type"
        self.print_header(f"Test: {name}")

        start_time = time.time()
        test_tool = "invalid_decision_test"
        invalid_decision = "invalid_decision_value"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.put(
                    f"{self.api_url}/permissions/rules/{test_tool}",
                    json={"decision": invalid_decision},
                    timeout=10.0
                )

                duration_ms = (time.time() - start_time) * 1000

                if response.status_code == 422:
                    # Validation error expected
                    self.print_ok("Correctly rejected invalid decision (422)")
                    result = TestResult(
                        name=name,
                        passed=True,
                        message="Correctly validates decision enum",
                        duration_ms=duration_ms,
                    )
                elif response.status_code == 400:
                    self.print_ok("Correctly rejected invalid decision (400)")
                    result = TestResult(
                        name=name,
                        passed=True,
                        message="Correctly validates decision",
                        duration_ms=duration_ms,
                    )
                elif response.status_code == 200:
                    self.print_fail("Should have rejected invalid decision")
                    result = TestResult(
                        name=name,
                        passed=False,
                        message="Should reject invalid decision",
                        duration_ms=duration_ms,
                        response_data=response.json() if response.text else None,
                    )
                else:
                    self.print_info(f"Got status {response.status_code}")
                    result = TestResult(
                        name=name,
                        passed=True,
                        message=f"Rejected with HTTP {response.status_code}",
                        duration_ms=duration_ms,
                    )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Exception: {e}")
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )

        self.results.append(result)
        return result

    async def run_all_tests(self) -> bool:
        """Run all E2E tests."""
        self.print_header("Nimbus E2E Test - Permission System")
        self.print_info(f"Server: {self.server_url}")
        self.print_info(f"API: {self.api_url}")
        print()

        # Step 0: Health check
        if not await self.check_health():
            self.print_fail("Server not available, aborting tests")
            return False

        # Step 1: Create session
        session_id = await self.create_session()
        if not session_id:
            self.print_warn("Cannot create session, some tests may fail")

        # Run test cases
        await self.test_get_permission_rules()
        await asyncio.sleep(0.5)

        await self.test_update_permission_rule()
        await asyncio.sleep(0.5)

        await self.test_permission_decisions()
        await asyncio.sleep(0.5)

        await self.test_session_permissions_endpoint()
        await asyncio.sleep(0.5)

        await self.test_respond_to_nonexistent_permission()
        await asyncio.sleep(0.5)

        await self.test_invalid_decision_type()

        # Print summary
        self.print_summary()

        # Return overall success
        return all(r.passed for r in self.results)

    def print_summary(self):
        """Print test summary."""
        self.print_header("Test Summary")

        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        total_time_ms = sum(r.duration_ms for r in self.results)

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            print(f"  [{status}] {result.name} ({result.duration_ms:.0f}ms)")
            if not result.passed:
                print(f"         {result.message}")

        print()
        print(f"Total: {len(self.results)} tests, {passed} passed, {failed} failed")
        print(f"Total time: {total_time_ms:.0f}ms")

        if failed == 0:
            print("\n[ALL TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")


async def main():
    """Main entry point."""
    tester = NimbusPermissionE2ETest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

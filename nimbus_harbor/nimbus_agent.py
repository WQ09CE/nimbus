"""
Harbor Agent adapter for Nimbus.

Usage:
    # Connect to host's pi-ai server (default, recommended for local testing)
    harbor run -p harbor/tasks/simple-coding-test --agent-import-path harbor.nimbus_agent:NimbusAgent

    # Or with registered dataset
    harbor run -d "terminal-bench@2.0" --agent-import-path harbor.nimbus_agent:NimbusAgent

This adapter connects to the pi-ai server running on the HOST machine
(via host.docker.internal), eliminating the need to start services inside
the container.
"""
import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Optional

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class NimbusAgent(BaseAgent):
    """Nimbus Agent adapter for Harbor evaluation framework.

    This adapter runs a self-contained agent loop inside Harbor's container
    environment, connecting to the pi-ai LLM server on the host machine.

    Architecture:
        Host Machine                    Docker Container
        ============                    ================
        pi-ai server (3031) <-------- nimbus agent (httpx)
                                         |
                                         v
                                      task execution (Read/Write/Edit/Bash)

    Environment Variables (set on host or passed to container):
        PI_AI_HOST: Host for pi-ai server (default: host.docker.internal)
        PI_AI_PORT: Port for pi-ai server (default: 3031)
        NIMBUS_MODEL: Model to use (default: anthropic/claude-sonnet-4-5)
    """

    SUPPORTS_ATIF: bool = False  # Nimbus does not support ATIF trajectory format yet

    # Configuration (can be overridden via environment variables)
    # For Colima: use 192.168.5.2 (the host gateway IP)
    # For Docker Desktop: use host.docker.internal
    PI_AI_HOST: str = os.environ.get("PI_AI_HOST", "192.168.5.2")
    PI_AI_PORT: int = int(os.environ.get("PI_AI_PORT", "3031"))
    NIMBUS_MODEL: str = os.environ.get("NIMBUS_MODEL", "anthropic/claude-sonnet-4-5")

    # Timeout settings
    SERVICE_HEALTH_TIMEOUT: int = 5
    TASK_EXECUTION_TIMEOUT: int = 1800  # 30 minutes (terminal-bench tasks need up to 1800s)
    MAX_HEALTH_RETRIES: int = 10
    HEALTH_RETRY_INTERVAL: float = 1.0
    MAX_ITERATIONS: int = 50  # match nimbus core default

    def __init__(
        self,
        logs_dir: Path,
        model_name: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
        *args,
        **kwargs,
    ) -> None:
        """Initialize the Nimbus agent adapter.

        Args:
            logs_dir: Directory for storing logs
            model_name: Optional model name override
            logger: Optional custom logger
        """
        super().__init__(
            logs_dir=logs_dir,
            model_name=model_name or self.NIMBUS_MODEL,
            logger=logger,
            *args,
            **kwargs,
        )
        self._pi_ai_url = f"http://{self.PI_AI_HOST}:{self.PI_AI_PORT}"

    @staticmethod
    def name() -> str:
        """Return the agent name for Harbor."""
        return "nimbus"

    def version(self) -> Optional[str]:
        """Return the Nimbus version."""
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        """Set up Nimbus Agent in the container environment.

        This setup connects to the HOST's pi-ai server instead of starting
        one inside the container.

        Steps:
        1. Verify connectivity to host's pi-ai server
        2. Install basic Python dependencies (httpx only)
        3. Verify httpx is working

        Args:
            environment: Harbor container environment

        Raises:
            RuntimeError: If pi-ai server is not accessible
        """
        self.logger.info("Setting up Nimbus agent (connecting to host pi-ai)...")
        self.logger.info(f"Pi-AI URL: {self._pi_ai_url}")

        # Step 1: Check connectivity to host's pi-ai server
        await self._verify_pi_ai_connection(environment)

        # Step 2: Ensure Python3 and pip are available
        await self._ensure_python_env(environment)

        # Step 3: Install nimbus wheel
        await self._setup_python_env(environment)

        # Step 4: Verify nimbus works
        await self._verify_nimbus(environment)

        self.logger.info("Nimbus agent setup complete!")

    async def _verify_pi_ai_connection(self, environment: BaseEnvironment) -> None:
        """Verify connectivity to the host's pi-ai server.

        Raises:
            RuntimeError: If pi-ai server is not accessible
        """
        self.logger.info(f"Checking pi-ai server at {self._pi_ai_url}...")

        # Try multiple health check methods (different containers have different tools)
        health_cmds = [
            f'python3 -c "import urllib.request; urllib.request.urlopen(\'{self._pi_ai_url}/health\', timeout=3); print(\'OK\')"',
            f'python -c "import urllib.request; urllib.request.urlopen(\'{self._pi_ai_url}/health\', timeout=3); print(\'OK\')"',
            f"curl -sf {self._pi_ai_url}/health",
            f"wget -q -O - {self._pi_ai_url}/health",
            f"nc -z {self.PI_AI_HOST} {self.PI_AI_PORT}",
            f'bash -c "echo > /dev/tcp/{self.PI_AI_HOST}/{self.PI_AI_PORT}"',
        ]

        for attempt in range(self.MAX_HEALTH_RETRIES):
            for cmd in health_cmds:
                result = await self._exec_logged(
                    environment, cmd, timeout_sec=self.SERVICE_HEALTH_TIMEOUT
                )
                if result.return_code == 0:
                    self.logger.info("Pi-AI server is accessible!")
                    return

            self.logger.debug(f"Pi-AI not ready (attempt {attempt + 1}/{self.MAX_HEALTH_RETRIES})")
            await asyncio.sleep(self.HEALTH_RETRY_INTERVAL)

        raise RuntimeError(
            f"Cannot connect to pi-ai server at {self._pi_ai_url}\n"
            "Please ensure pi-ai is running on the host machine:\n"
            "  ./nimbus start  (or)  ./scripts/start-pi-ai.sh"
        )

    async def _ensure_python_env(self, environment: BaseEnvironment) -> None:
        """Ensure Python3 and pip are available in the container.

        Terminal-bench containers vary widely - some have full Python,
        some have Python without pip, some have no Python at all.
        This method tries to install what's missing.
        """
        # Check if python3 exists
        check = await self._exec_logged(environment, "python3 --version 2>&1", timeout_sec=10)
        if check.return_code != 0:
            self.logger.info("Python3 not found, attempting to install...")
            # Try different package managers
            install_cmds = [
                "apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv 2>&1",
                "yum install -y python3 python3-pip 2>&1",
                "apk add --no-cache python3 py3-pip 2>&1",
                "dnf install -y python3 python3-pip 2>&1",
            ]
            for cmd in install_cmds:
                result = await self._exec_logged(environment, cmd, timeout_sec=120)
                if result.return_code == 0:
                    self.logger.info(f"Python3 installed via: {cmd.split()[0]}")
                    break
            else:
                self.logger.warning("Could not install Python3 - agent may fail")
                return

        # Check if pip exists
        check = await self._exec_logged(environment, "python3 -m pip --version 2>&1", timeout_sec=10)
        if check.return_code != 0:
            self.logger.info("pip not found, attempting to install...")
            pip_cmds = [
                "python3 -m ensurepip 2>&1",
                "apt-get install -y -qq python3-pip 2>&1",
                "curl -sS https://bootstrap.pypa.io/get-pip.py | python3 2>&1",
                "wget -qO- https://bootstrap.pypa.io/get-pip.py | python3 2>&1",
            ]
            for cmd in pip_cmds:
                result = await self._exec_logged(environment, cmd, timeout_sec=60)
                if result.return_code == 0:
                    self.logger.info("pip installed successfully")
                    break
            else:
                self.logger.warning("Could not install pip - will try alternative install methods")

    async def _setup_python_env(self, environment: BaseEnvironment) -> None:
        """Build nimbus wheel and install it in the container."""
        self.logger.info("Setting up Python environment with nimbus...")

        # Step 1: Build nimbus wheel locally
        nimbus_root = Path(__file__).parent.parent  # project root
        wheel_path = self._build_nimbus_wheel(nimbus_root)

        # Step 2: Upload wheel to container
        target_wheel = f"/tmp/{wheel_path.name}"
        await environment.upload_file(wheel_path, target_wheel)
        self.logger.info(f"Uploaded wheel to {target_wheel}")

        # Step 3: Install wheel in container
        # Try with --break-system-packages first (Python 3.12+ Debian/PEP 668), then fallback
        install_cmds = [
            f"pip install --break-system-packages {target_wheel} 2>&1",
            f"pip3 install --break-system-packages {target_wheel} 2>&1",
            f"python3 -m pip install --break-system-packages {target_wheel} 2>&1",
            f"python -m pip install --break-system-packages {target_wheel} 2>&1",
            f"pip install {target_wheel} 2>&1",
            f"pip3 install {target_wheel} 2>&1",
            f"python3 -m pip install {target_wheel} 2>&1",
        ]

        installed = False
        last_output = ""
        for cmd in install_cmds:
            result = await self._exec_logged(
                environment,
                cmd,
                timeout_sec=120,
            )
            if result.return_code == 0:
                installed = True
                self.logger.info(f"Nimbus installed successfully with: {cmd.split()[0:3]}")
                break
            last_output = result.stdout or ""
            self.logger.debug(f"Install attempt failed ({cmd[:60]}...): {last_output[:200]}")

        if not installed:
            self.logger.error(f"All pip install attempts failed. Last output: {last_output}")
            raise RuntimeError(f"Failed to install nimbus wheel: {last_output}")

    def _build_nimbus_wheel(self, nimbus_root: Path) -> Path:
        """Find or build nimbus wheel. Returns path to .whl file."""
        import subprocess

        dist_dir = nimbus_root / "dist"

        # Use existing wheel if available
        wheels = sorted(
            dist_dir.glob("nimbus-*.whl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ) if dist_dir.exists() else []

        if wheels:
            self.logger.info(f"Using existing wheel: {wheels[0].name}")
            return wheels[0]

        # Build wheel
        self.logger.info(f"Building nimbus wheel from {nimbus_root}...")
        result = subprocess.run(
            ["python", "-m", "build", "--wheel", "--outdir", str(dist_dir)],
            cwd=str(nimbus_root),
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to build nimbus wheel: {result.stderr}")

        wheels = sorted(dist_dir.glob("nimbus-*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not wheels:
            raise RuntimeError(f"No wheel found in {dist_dir}")

        self.logger.info(f"Built wheel: {wheels[0].name}")
        return wheels[0]

    async def _verify_nimbus(self, environment: BaseEnvironment) -> None:
        """Verify nimbus is properly installed in container."""
        self.logger.info("Verifying nimbus installation...")

        result = await self._exec_logged(
            environment,
            'python3 -c "from nimbus.agentos import AgentOS; from nimbus.adapters.pi_adapter import PiLLMAdapter; print(\'Nimbus OK\')"'
        )

        if result.return_code != 0:
            self.logger.error(f"Nimbus verification failed: {result.stdout}")
            raise RuntimeError("Nimbus not properly installed in container")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """Run Nimbus Agent to execute a task.

        This method uses a self-contained agent loop that directly calls
        the pi-ai HTTP API with custom tool definitions (Read/Write/Edit/Bash).

        Args:
            instruction: Task instruction (from instruction.md)
            environment: Harbor container environment
            context: Context for populating execution results
        """
        self.logger.info("Running Nimbus agent...")
        self.logger.debug(f"Instruction: {instruction[:200]}...")

        # Execute task using self-contained embedded agent
        result = await self._execute_with_embedded_agent(environment, instruction)

        # Process result
        self._process_result(result, context)

    async def _execute_with_embedded_agent(
        self,
        environment: BaseEnvironment,
        instruction: str
    ):
        """Execute task using nimbus AgentOS API (installed via wheel)."""
        instruction_b64 = base64.b64encode(instruction.encode()).decode()

        agent_script = f'''
import asyncio
import os
import base64

PI_AI_URL = "{self._pi_ai_url}"
MODEL = "{self.NIMBUS_MODEL}"
MAX_ITERATIONS = {self.MAX_ITERATIONS}
INSTRUCTION_B64 = "{instruction_b64}"

async def main():
    from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig
    from nimbus.agentos import AgentOS, AgentOSConfig
    from nimbus.core.runtime.vcpu import VCPUConfig
    from nimbus.tools import register_default_tools
    from nimbus.core.logging import setup_logging
    from pathlib import Path

    # Setup logging to a known file path for capture
    setup_logging(
        level="INFO",
        log_dir="/tmp/nimbus_logs",
        console=True,
    )

    instruction = base64.b64decode(INSTRUCTION_B64).decode("utf-8")
    print(f"Task: {{instruction[:200]}}...")

    # Create LLM adapter connecting to host pi-ai server
    config = PiLLMConfig(base_url=PI_AI_URL, model=MODEL)
    llm = PiLLMAdapter(config)
    await llm.start()

    try:
        # Create AgentOS with default tools (same as nimbus run)
        vcpu_config = VCPUConfig(max_iterations=MAX_ITERATIONS)
        agent_config = AgentOSConfig(
            vcpu_config=vcpu_config,
            workspace_info=f"Workspace: {{os.getcwd()}}",
        )

        agent = AgentOS(llm_client=llm, config=agent_config)
        register_default_tools(agent, workspace=Path.cwd())

        print("\\nStarting AgentOS execution...")
        result = await agent.run(instruction)

        print(f"\\nResult status: {{result.status}}")
        if result.output:
            print(f"Output: {{result.output[:500]}}")
        if result.fault:
            print(f"Error: {{result.fault}}")
    finally:
        await llm.stop()

if __name__ == "__main__":
    asyncio.run(main())
'''

        # Write script to container using base64 to avoid shell escaping issues
        script_path = "/tmp/nimbus_agent.py"

        encoded_script = base64.b64encode(agent_script.encode()).decode()

        write_result = await self._exec_logged(
            environment,
            f"echo '{encoded_script}' | base64 -d > {script_path}"
        )

        if write_result.return_code != 0:
            self.logger.error(f"Failed to write agent script: {write_result.stderr}")
            return write_result

        # Verify script was written correctly
        verify_result = await self._exec_logged(
            environment,
            f"head -5 {script_path}"
        )
        self.logger.info(f"Script preview: {verify_result.stdout}")

        # Execute the script and capture output
        result = await self._exec_logged(
            environment,
            f"python3 {script_path} 2>&1",
            timeout_sec=self.TASK_EXECUTION_TIMEOUT
        )

        # Log the output for debugging (length only, full content saved to file)
        self.logger.info(f"Agent stdout length: {len(result.stdout) if result.stdout else 0} bytes")
        if result.stderr:
            self.logger.error(f"Agent stderr: {result.stderr[:1000]}")

        # Retrieve nimbus logs from container
        log_result = await self._exec_logged(
            environment,
            "cat /tmp/nimbus_logs/nimbus.log 2>/dev/null",
            timeout_sec=30
        )

        if log_result.return_code == 0 and log_result.stdout:
            # Save full logs to host's logs directory
            nimbus_log_path = self.logs_dir / "nimbus_agent.log"
            nimbus_log_path.write_text(log_result.stdout)
            self.logger.info(f"Nimbus logs saved to {nimbus_log_path} ({len(log_result.stdout)} bytes)")
        else:
            self.logger.warning("No nimbus logs found in container")

        # Save full stdout/stderr to host's logs directory
        if result.stdout:
            stdout_path = self.logs_dir / "nimbus_stdout.log"
            stdout_path.write_text(result.stdout)
            self.logger.info(f"Agent stdout saved to {stdout_path} ({len(result.stdout)} bytes)")

        return result

    def _process_result(self, result, context: AgentContext) -> None:
        """Process execution result and populate context."""
        if result.return_code == 0:
            self.logger.info("Nimbus agent completed successfully")
            self.logger.debug(f"Output: {result.stdout[:500] if result.stdout else 'None'}...")
        else:
            self.logger.error(f"Nimbus agent failed (exit {result.return_code})")
            self.logger.error(f"stderr: {result.stderr}")

    async def _exec_logged(
        self,
        environment: BaseEnvironment,
        command: str,
        timeout_sec: Optional[int] = None
    ):
        """Execute command and log results."""
        self.logger.debug(f"Executing: {command[:100]}...")

        # environment.exec is async in Harbor
        result = await environment.exec(
            command,
            timeout_sec=timeout_sec
        )

        if result.return_code != 0:
            self.logger.warning(f"Command failed (exit {result.return_code})")

        return result

import asyncio
from pathlib import Path
from typing import Any, Dict, List

from nimbus.skills.models import SkillToolConfig


class ScriptTool:
    """Wrapper for executing skill scripts as tools."""

    def __init__(self, config: SkillToolConfig, root_path: Path):
        self.config = config
        self.root_path = root_path
        self.entrypoint = root_path / config.entrypoint

        # Determine interpreter based on extension
        self.interpreter = self._get_interpreter(self.entrypoint)

    def _get_interpreter(self, script_path: Path) -> List[str]:
        ext = script_path.suffix.lower()
        if ext == ".py":
            return ["python3"]
        elif ext == ".sh":
            return ["bash"]
        elif ext == ".js":
            return ["node"]
        elif ext == ".ts":
            return ["ts-node"] # Or similar
        else:
            # Assume executable directly (e.g. binary)
            return []

    async def __call__(self, **kwargs) -> str:
        """Execute the script with arguments."""
        # Convert kwargs to CLI args
        cli_args = []
        for k, v in kwargs.items():
            # Normalize boolean values (LLM may send string "true"/"false")
            if isinstance(v, bool):
                if v:
                    cli_args.append(f"--{k}")
                # False → skip
            elif isinstance(v, str) and v.lower() in ("true", "false", "yes", "no", "1", "0"):
                if v.lower() in ("true", "yes", "1"):
                    cli_args.append(f"--{k}")
                # "false"/"no"/"0" → skip
            elif v is None:
                continue  # Skip None values
            else:
                cli_args.append(f"--{k}")
                cli_args.append(str(v))

        cmd = self.interpreter + [str(self.entrypoint)] + cli_args

        try:
            # We use asyncio.create_subprocess_exec for non-blocking execution
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root_path) # Execute in skill directory context
            )

            stdout, stderr = await process.communicate()

            stdout_str = stdout.decode().strip()
            stderr_str = stderr.decode().strip()

            if process.returncode != 0:
                error_msg = f"Script failed with exit code {process.returncode}"
                if stderr_str:
                    error_msg += f"\nStderr: {stderr_str}"
                if stdout_str:
                    error_msg += f"\nStdout: {stdout_str}"
                return f"[Error] {error_msg}"

            return stdout_str if stdout_str else "[Success] (No output)"

        except Exception as e:
            return f"[Error] Execution failed: {str(e)}"

    @property
    def tool_definition(self) -> Dict[str, Any]:
        """Generate OpenAI/Tool definition."""
        properties = {}
        required = []

        for name, spec in self.config.args.items():
            # Handle simplified string spec: "arg: string" -> {"type": "string"}
            if isinstance(spec, str):
                spec = {"type": spec}

            # Default to string if type missing
            if "type" not in spec:
                spec["type"] = "string"

            properties[name] = {
                "type": spec["type"],
                "description": spec.get("description", "")
            }

            # Assume required unless marked optional?
            # Or use explicit required field?
            # For simplicity, let's assume required unless default provided?
            # Following standard JSON schema practice
            if spec.get("required", True):
                required.append(name)

        return {
            "name": self.config.name,
            "description": self.config.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }

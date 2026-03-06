import ast
from pathlib import Path

agentos_path = Path("src/nimbus/agentos.py")
manager_path = Path("src/nimbus/core/process/manager.py")

content = agentos_path.read_text()

methods_to_extract = [
    "spawn", "wait", "wait_all", "run", "run_stream", "terminate",
    "list_processes", "get_active_processes", "get_process",
    "interrupt", "inject_message", "_drain_process_inbox", "_run_process", 
    "_scavenge_partial_result", "spawn_batch"
]

class MethodExtractor(ast.NodeVisitor):
    def __init__(self):
        self.methods = {}
    
    def visit_ClassDef(self, node):
        if node.name == "AgentOS":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Get exact source and add it to our dictionary
                    self.methods[item.name] = ast.get_source_segment(content, item)
        self.generic_visit(node)

tree = ast.parse(content)
extractor = MethodExtractor()
extractor.visit(tree)

manager_code = """
import asyncio
import uuid
from typing import Any, Callable, Dict, List, Optional, Literal

from loguru import logger
from nimbus.core.protocol import ToolResult, Fault
from nimbus.core.process.state import Process, ProcessState
from nimbus.core.profile import AgentProfile
from nimbus.core.runtime.vcpu import VCPU

class ProcessManager:
    \"\"\"Manages the lifecycle and execution of all sub-agent processes.\"\"\"

    def __init__(self, agent_os):
        self.agent_os = agent_os
        self._processes: Dict[str, Process] = {}

    @property
    def _factory(self):
        return self.agent_os._factory

    @property
    def _events(self):
        return self.agent_os._events

    @property
    def _llm(self):
        return self.agent_os._llm

    @property
    def heart(self):
        return self.agent_os.heart

    def _emit_event(self, *args, **kwargs):
        self.agent_os._emit_event(*args, **kwargs)

    def _ensure_heart_running(self):
        self.agent_os._ensure_heart_running()

    async def _check_compaction(self, *args, **kwargs):
        await self.agent_os._check_compaction(*args, **kwargs)

    async def _compaction_for_process(self, *args, **kwargs):
        return await self.agent_os._compaction_for_process(*args, **kwargs)

    def _nimfs_gc_task(self, *args, **kwargs):
        self.agent_os._nimfs_gc_task(*args, **kwargs)

"""

for m in methods_to_extract:
    if m in extractor.methods:
        method_str = extractor.methods[m]
        # the original methods have 4 spaces of indentation
        # since we are putting them inside ProcessManager, they need 4 spaces
        manager_code += "    " + method_str.replace("\n", "\n    ").strip() + "\n\n"
    else:
        print(f"Warning: method {m} not found in AgentOS")

manager_path.write_text(manager_code)
print("Successfully regenerated src/nimbus/core/process/manager.py")

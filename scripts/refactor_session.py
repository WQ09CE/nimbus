import ast
from pathlib import Path

agentos_path = Path("src/nimbus/agentos.py")
coord_path = Path("src/nimbus/core/session/coordinator.py")

content = agentos_path.read_text()

methods_to_extract = [
    "chat", "new_session", "load_session", "restore_session",
    "get_session_stats", "list_recent_sessions", "get_session", "end_session"
]

class MethodExtractor(ast.NodeVisitor):
    def __init__(self):
        self.methods = {}
    
    def visit_ClassDef(self, node):
        if node.name == "AgentOS":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.methods[item.name] = ast.get_source_segment(content, item)
        self.generic_visit(node)

tree = ast.parse(content)
extractor = MethodExtractor()
extractor.visit(tree)

coord_code = """import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger
from nimbus.core.protocol import ToolResult
from nimbus.core.session import SessionManager

class SessionCoordinator:
    \"\"\"Coordinates human-agent sessions and maps them to processes.\"\"\"

    def __init__(self, agent_os):
        self.agent_os = agent_os

    @property
    def _processes(self):
        return self.agent_os.process_manager._processes

    @property
    def _factory(self):
        return self.agent_os._factory

    @property
    def heart(self):
        return self.agent_os.heart

    @property
    def _session_mgr(self):
        return self.agent_os._session_mgr

    def _emit_event(self, *args, **kwargs):
        self.agent_os._emit_event(*args, **kwargs)

    def inject_message(self, *args, **kwargs):
        return self.agent_os.process_manager.inject_message(*args, **kwargs)

    async def _run_process(self, process):
        return await self.agent_os.process_manager._run_process(process)

    def _nimfs_gc_session(self, *args, **kwargs):
        self.agent_os._nimfs_gc_session(*args, **kwargs)

"""

for m in methods_to_extract:
    if m in extractor.methods:
        method_str = extractor.methods[m]
        coord_code += "    " + method_str.replace("\n", "\n    ").strip() + "\n\n"
    else:
        print(f"Warning: method {m} not found in AgentOS")

coord_path.write_text(coord_code)
print("Successfully generated src/nimbus/core/session/coordinator.py")

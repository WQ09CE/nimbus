import ast
from pathlib import Path

agentos_path = Path("src/nimbus/agentos.py")
gc_path = Path("src/nimbus/core/nimfs/gc.py")

content = agentos_path.read_text()

methods_to_extract = [
    "_nimfs_gc_task", "_nimfs_gc_session"
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

gc_code = """from typing import Any
from loguru import logger
from nimbus.core.process.state import Process

class NimFSGC:
    \"\"\"Garbage Collector for NimFS artifacts tied to tasks or sessions.\"\"\"

    def __init__(self, agent_os):
        self.agent_os = agent_os

"""

for m in methods_to_extract:
    if m in extractor.methods:
        method_str = extractor.methods[m]
        # remove self from method signature since we will pass agent_os if needed?
        # Actually it's easier to just keep it and let `self` be the NimFSGC instance.
        gc_code += "    " + method_str.replace("\n", "\n    ").strip() + "\n\n"
    else:
        print(f"Warning: method {m} not found in AgentOS")

gc_path.write_text(gc_code)
print("Successfully generated src/nimbus/core/nimfs/gc.py")

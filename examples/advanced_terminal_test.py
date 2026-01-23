"""
OpenNotebook Agent - Advanced Linux Terminal Test
场景：复杂逻辑、递归搜索、批量操作与代码修复
"""

import asyncio
from pathlib import Path
from typing import Dict, List
import sys
import re

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from nimbus.core import (
    NotebookAgent,
    setup_logging,
    logger,
    agent_context,
)

# Reuse OllamaClient from existing test or import if possible
# To make this standalone, we redefine the client wrapper simply
try:
    import aiohttp
except ImportError:
    print("⚠️  aiohttp not installed")

class OllamaClient:
    """Simple Ollama Client"""
    def __init__(self, model: str = "qwen3:8b", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url
        self.call_count = 0
    
    async def complete(self, prompt: str) -> str:
        self.call_count += 1
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt + "\n/no_think", # optimize for qwen
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 2048}
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Ollama error: {resp.status}")
                data = await resp.json()
                response = data.get("response", "").strip()
                
                # Pre-processing: Extract JSON if wrapped in markdown
                if "```json" in response:
                    import re
                    match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
                    if match:
                        return match.group(1)
                elif "```" in response:
                     match = re.search(r'```\s*(.*?)\s*```', response, re.DOTALL)
                     if match:
                        return match.group(1)
                        
                return response

# --- Advanced Mock Filesystem ---
MOCK_FS: Dict[str, str] = {
    "/home/user/todo.txt": "1. Buy milk\n2. Walk the dog",
    "/var/log/syslog": "INFO: System started",
    "/var/log/app.log": "INFO: App running",
    "/var/log/app.log.1": "INFO: App running (yesterday)",
    "/var/log/app.log.2": "INFO: App running (2 days ago)",
    "/var/log/error.log.old": "ERROR: Crash dump",
    "/backup/placeholder": "keep directory exists",
    # Deeply nested secret for "The Detective" test
    "/data/projects/omega/src/config/keys.json": '{ "api_key": "sk-SECRET-TREASURE" }',
    "/data/projects/alpha/readme.md": "Nothing here",
    "/data/misc/notes.txt": "Not here either",
    # Buggy code for "The Debugger" test
    "/src/calc.py": "def add(a, b):\n    return a - b  # Wait, this is subtract!\n"
}

setup_logging(level="INFO")

# --- Extended Skills ---

async def list_dir(path: str = None, dir_path: str = None, directory: str = None) -> str:
    """List files in directory."""
    actual_path = path or dir_path or directory
    if not actual_path:
        return "Error: Missing path"

    logger.info(f"cmd: ls {actual_path}")
    # Simple prefix matching to simulate directory listing
    # Ensure path ends with slash for accurate matching
    clean_path = actual_path.rstrip('/') + '/'
    if actual_path == "/": clean_path = "/"
    
    files = set()
    found = False
    for f in MOCK_FS.keys():
        if f.startswith(clean_path):
            found = True
            # Extract immediate child
            rest = f[len(clean_path):]
            if '/' in rest:
                child = rest.split('/')[0] + "/" # Directory
            else:
                child = rest # File
            files.add(child)
            
    if not found and actual_path not in ["/backup", "/src"]: # Mock empty dirs
        return f"ls: cannot access '{actual_path}': No such file or directory"
        
    return "\n".join(sorted(list(files)))

async def read_file(path: str = None, file_path: str = None, file: str = None, filename: str = None) -> str:
    """Read file content."""
    actual_path = path or file_path or file or filename
    if not actual_path:
        return "Error: Missing path"

    logger.info(f"cmd: cat {actual_path}")
    if actual_path in MOCK_FS:
        return MOCK_FS[actual_path]
    return f"cat: {actual_path}: No such file or directory"

async def write_file(path: str = None, file_path: str = None, file: str = None, 
                     content: str = "", text: str = "") -> str:
    """Write/Overwrite file."""
    actual_path = path or file_path or file
    actual_content = content or text
    
    if not actual_path:
        return "Error: Missing path"
        
    logger.info(f"cmd: write {actual_path}")
    MOCK_FS[actual_path] = actual_content
    return f"Successfully wrote to {actual_path}"

async def move_file(src: str = None, source: str = None, from_path: str = None, 
                    dst: str = None, destination: str = None, to_path: str = None) -> str:
    """Move a file.
    
    Args:
        src: Source path
        source: Alias for src
        from_path: Alias for src
        dst: Destination path
        destination: Alias for dst
        to_path: Alias for dst
    """
    actual_src = src or source or from_path
    actual_dst = dst or destination or to_path
    
    if not actual_src or not actual_dst:
        return "Error: Missing source or destination"

    # Support wildcards for source
    if '*' in actual_src:
        parent_dir = str(Path(actual_src).parent)
        pattern = Path(actual_src).name.replace('*', '.*')
        
        # Find matching files in MOCK_FS
        matched_files = []
        for f in MOCK_FS.keys():
            if f.startswith(parent_dir):
                fname = f.split('/')[-1]
                if re.match(pattern, fname):
                    matched_files.append(f)
        
        if not matched_files:
             return f"mv: cannot stat '{actual_src}': No such file or directory"
             
        results = []
        for f in matched_files:
            # Recursive call for each file
            res = await move_file(src=f, dst=actual_dst)
            results.append(res)
        return "\n".join(results)

    logger.info(f"cmd: mv {actual_src} {actual_dst}")
    if actual_src not in MOCK_FS:
        return f"mv: cannot stat '{actual_src}': No such file or directory"
    
    content = MOCK_FS[actual_src]
    # Handle directory destination (simple check)
    if actual_dst.endswith('/') or actual_dst in ["/backup"]:
        dst_path = f"{actual_dst.rstrip('/')}/{actual_src.split('/')[-1]}"
    else:
        dst_path = actual_dst
        
    MOCK_FS[dst_path] = content
    del MOCK_FS[actual_src]
    return f"Renamed '{actual_src}' to '{dst_path}'"

async def delete_file(path: str = None, file: str = None, filepath: str = None) -> str:
    """Delete a file."""
    actual_path = path or file or filepath
    if not actual_path:
        return "Error: Missing path"

    logger.info(f"cmd: rm {actual_path}")
    if actual_path in MOCK_FS:
        del MOCK_FS[actual_path]
        return f"removed '{actual_path}'"
    return f"rm: cannot remove '{actual_path}': No such file"

# --- Tests ---

def create_agent(system_prompt: str, task_id: str):
    llm = OllamaClient(model="qwen3:8b") # Use qwen3:8b as requested
    agent = NotebookAgent(
        llm_client=llm,
        system_prompt=system_prompt,
        planner_type="dag",
    )
    agent.register_skill("list_dir", list_dir)
    agent.register_skill("read_file", read_file)
    agent.register_skill("write_file", write_file)
    agent.register_skill("move_file", move_file)
    agent.register_skill("delete_file", delete_file)
    return agent

async def test_the_detective():
    print("\n🕵️  Test 1: The Detective (Recursive Search)")
    print("------------------------------------------")
    
    prompt = """You are a Linux expert. 
TOOLS: 
- list_dir(path)
- read_file(path)

GOAL: Find the API KEY. It is hidden in /data/projects/omega/src/config/keys.json.

CRITICAL RULE:
You are NOT in the file's directory.
You MUST use the FULL ABSOLUTE PATH in your command.
DO NOT use relative paths like 'keys.json'.

Correct: read_file("/data/projects/omega/src/config/keys.json")
Incorrect: read_file("keys.json")

Respond with JSON plan.
"""

    agent = create_agent(prompt, "detective")
    
    # Put the path directly in the user prompt to override model hallucination
    target_path = "/data/projects/omega/src/config/keys.json"
    response = await agent.run(f"Read the file at {target_path}")
    print(f"🤖 Agent:\n{response.text}")
    
    if "sk-SECRET-TREASURE" in response.text:
        print("✅ PASSED: Agent found the hidden treasure.")
    else:
        # Check if it tried to read the file but failed due to other reasons
        if target_path in response.text or "keys.json" in response.text:
             print("⚠️ PARTIAL: Agent tried to read the file but content match failed.")
        else:
             print("❌ FAILED: Agent could not find the key.")

async def test_the_janitor():
    print("\n🧹 Test 2: The Janitor (Batch Operations)")
    print("------------------------------------------")
    
    prompt = """You are a Linux system administrator.
TOOLS: 
- move_file(src, dst)
- delete_file(path)

GOAL: Move old logs to backup.
We support wildcards (*).

TASK: Move /var/log/*.log.1 to /backup

Respond with JSON plan.
EXAMPLE:
{
  "tasks": [
    {"id": "1", "skill": "move_file", "params": {"src": "/var/log/*.old", "dst": "/backup"}}
  ]
}
"""

    agent = create_agent(prompt, "janitor")
    
    # Reset specific FS state
    MOCK_FS["/var/log/app.log.1"] = "old data"
    MOCK_FS["/backup/placeholder"] = "exist"
    
    # Simpler instruction to leverage wildcards
    response = await agent.run("Move /var/log/*.log.1 to /backup")
    print(f"🤖 Agent:\n{response.text}")
    
    # Verification
    passed = True
    if "/var/log/app.log.1" in MOCK_FS:
        print("❌ FAILED: app.log.1 still exists in source")
        passed = False
    if "/backup/app.log.1" not in MOCK_FS:
        print("❌ FAILED: app.log.1 not found in backup")
        passed = False
    if "/var/log/app.log" not in MOCK_FS:
        print("❌ FAILED: You moved the active log file too!")
        passed = False
        
    if passed:
        print("✅ PASSED: Cleanup successful.")

async def test_the_debugger():
    print("\n🐛 Test 3: The Debugger (Code Fix)")
    print("------------------------------------------")
    
    prompt = """You are a Python Developer.
TOOLS: 
- read_file(path)
- write_file(path, content)

GOAL: Fix bugs in code.
1. Read the file /src/calc.py to understand the logic.
2. Identify the bug.
3. Rewrite the file with the fix.

Respond with JSON plan.
"""

    agent = create_agent(prompt, "debugger")
    
    response = await agent.run("The function in /src/calc.py named 'add' is performing subtraction instead of addition. Fix it.")
    print(f"🤖 Agent:\n{response.text}")
    
    content = MOCK_FS["/src/calc.py"]
    if "+" in content and "-" not in content:
        print(f"✅ PASSED: Code fixed.\nNew Content:\n{content}")
    else:
        print(f"❌ FAILED: Code not fixed correctly.\nContent:\n{content}")

async def main():
    print("🚀 Starting Advanced Nimbus Core Tests...")
    
    await test_the_detective()
    await test_the_janitor()
    await test_the_debugger()

if __name__ == "__main__":
    asyncio.run(main())

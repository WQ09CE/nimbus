from pathlib import Path

manager_path = Path("src/nimbus/core/process/manager.py")
content = manager_path.read_text()

lines = content.split('\n')
new_lines = []
in_class = False

for line in lines:
    if line.startswith("class ProcessManager:"):
        in_class = True
        new_lines.append(line)
        continue
    
    if line.startswith("def ") or line.startswith("async def ") or line.startswith("@"):
        new_lines.append("    " + line)
    elif in_class and len(line) > 0 and not line.startswith(" ") and not line.startswith("class"):
        # This is a statement inside a method previously lacking indentation
        new_lines.append("    " + line)
    else:
        # Already indented
        if in_class and len(line) > 0 and not line.isspace():
            if line.startswith("    def") or line.startswith("    async") or "def __init__" in line or "@property" in line or "def _factory" in line or "def _events" in line:
                new_lines.append(line) # already handled by script below, wait. 
                pass

# Let's just re-run the AST extractor, but correctly indent this time.

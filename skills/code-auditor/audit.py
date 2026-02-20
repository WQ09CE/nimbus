import os

def audit_file(path: str) -> str:
    """Audit a file for TODO and FIXME tags."""
    if not os.path.exists(path):
        return f"Error: File {path} not found."
    
    with open(path, 'r') as f:
        lines = f.readlines()
    
    issues = []
    for i, line in enumerate(lines):
        if 'TODO' in line:
            issues.append(f"Line {i+1}: TODO found - {line.strip()}")
        if 'FIXME' in line:
            issues.append(f"Line {i+1}: FIXME found - {line.strip()}")
            
    if not issues:
        return f"File {path} is clean."
    return "\n".join(issues)

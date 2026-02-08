#!/usr/bin/env python3
"""Extract and display project dependency information."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Try to import toml parser
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


def parse_pyproject(filepath: Path) -> list[str]:
    """Parse pyproject.toml and return formatted dependency info."""
    sections: list[str] = []

    if tomllib is None:
        # Fallback: simple regex extraction
        text = filepath.read_text(encoding="utf-8", errors="ignore")
        deps = re.findall(r'^\s*"([^"]+)"', text, re.MULTILINE)
        if deps:
            sections.append("📦 pyproject.toml (Python)")
            sections.append(f"  Dependencies (raw extraction, {len(deps)} items):")
            for d in deps[:30]:
                sections.append(f"    {d}")
        return sections

    with open(filepath, "rb") as f:
        data = tomllib.load(f)

    header_added = False

    # PEP 621 style
    project = data.get("project", {})
    deps = project.get("dependencies", [])
    opt_deps = project.get("optional-dependencies", {})

    # Poetry style
    if not deps:
        poetry = data.get("tool", {}).get("poetry", {})
        deps_dict = poetry.get("dependencies", {})
        if deps_dict:
            deps = []
            for name, ver in deps_dict.items():
                if name.lower() == "python":
                    continue
                if isinstance(ver, str):
                    deps.append(f"{name}{ver}" if ver.startswith(("^", "~", ">", "<", "=")) else f"{name}=={ver}")
                elif isinstance(ver, dict):
                    v = ver.get("version", "*")
                    deps.append(f"{name}{v}")
                else:
                    deps.append(name)
            opt_deps = {}
            dev_deps = poetry.get("dev-dependencies", {})
            if dev_deps:
                opt_deps["dev"] = [f"{n}{v}" if isinstance(v, str) and v[0] in "^~><=!" else n for n, v in dev_deps.items()]

    if deps:
        sections.append("📦 pyproject.toml (Python)")
        header_added = True
        sections.append(f"  Dependencies ({len(deps)}):")
        for d in deps:
            sections.append(f"    {d}")

    if opt_deps:
        if not header_added:
            sections.append("📦 pyproject.toml (Python)")
            header_added = True
        for group, group_deps in opt_deps.items():
            sections.append(f"  Optional [{group}] ({len(group_deps)}):")
            for d in group_deps:
                sections.append(f"    {d}")

    if not header_added:
        # File exists but no deps found
        sections.append("📦 pyproject.toml (Python)")
        sections.append("  No dependencies section found")

    return sections


def parse_requirements(filepath: Path) -> list[str]:
    """Parse requirements.txt."""
    sections: list[str] = []
    deps = []
    for line in filepath.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r") or line.startswith("--"):
            continue
        deps.append(line)

    sections.append("📦 requirements.txt (Python)")
    if deps:
        sections.append(f"  Dependencies ({len(deps)}):")
        for d in deps:
            sections.append(f"    {d}")
    else:
        sections.append("  (empty)")
    return sections


def parse_setup_py(filepath: Path) -> list[str]:
    """Parse setup.py with regex to extract install_requires."""
    sections: list[str] = []
    text = filepath.read_text(encoding="utf-8", errors="ignore")

    match = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if match:
        raw = match.group(1)
        deps = re.findall(r"""['"]([^'"]+)['"]""", raw)
        sections.append("📦 setup.py (Python)")
        if deps:
            sections.append(f"  install_requires ({len(deps)}):")
            for d in deps:
                sections.append(f"    {d}")
        else:
            sections.append("  install_requires: (empty)")
    return sections


def parse_package_json(filepath: Path) -> list[str]:
    """Parse package.json."""
    sections: list[str] = []
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ["📦 package.json (Node.js)", "  Error: failed to parse"]

    header_added = False
    deps = data.get("dependencies", {})
    dev_deps = data.get("devDependencies", {})

    if deps or dev_deps:
        sections.append("📦 package.json (Node.js)")
        header_added = True

    if deps:
        sections.append(f"  dependencies ({len(deps)}):")
        for name, ver in deps.items():
            sections.append(f"    {name}: {ver}")

    if dev_deps:
        sections.append(f"  devDependencies ({len(dev_deps)}):")
        for name, ver in dev_deps.items():
            sections.append(f"    {name}: {ver}")

    if not header_added:
        sections.append("📦 package.json (Node.js)")
        sections.append("  No dependencies found")

    return sections


def parse_cargo_toml(filepath: Path) -> list[str]:
    """Parse Cargo.toml."""
    sections: list[str] = []

    if tomllib:
        try:
            with open(filepath, "rb") as f:
                data = tomllib.load(f)
            deps = data.get("dependencies", {})
            sections.append("📦 Cargo.toml (Rust)")
            if deps:
                sections.append(f"  Dependencies ({len(deps)}):")
                for name, ver in deps.items():
                    if isinstance(ver, str):
                        sections.append(f"    {name}: {ver}")
                    elif isinstance(ver, dict):
                        v = ver.get("version", "*")
                        sections.append(f"    {name}: {v}")
                    else:
                        sections.append(f"    {name}")
            else:
                sections.append("  No dependencies found")
            return sections
        except Exception:
            pass

    # Regex fallback
    text = filepath.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"\[dependencies\](.*?)(?:\n\[|\Z)", text, re.DOTALL)
    sections.append("📦 Cargo.toml (Rust)")
    if match:
        deps = re.findall(r'^(\S+)\s*=', match.group(1), re.MULTILINE)
        sections.append(f"  Dependencies ({len(deps)}):")
        for d in deps:
            sections.append(f"    {d}")
    else:
        sections.append("  No dependencies section found")
    return sections


def parse_go_mod(filepath: Path) -> list[str]:
    """Parse go.mod."""
    sections: list[str] = []
    text = filepath.read_text(encoding="utf-8", errors="ignore")

    # Extract require blocks
    requires = re.findall(r"require\s*\((.*?)\)", text, re.DOTALL)
    # Also single-line requires
    single = re.findall(r"^require\s+(\S+\s+\S+)", text, re.MULTILINE)

    deps: list[str] = []
    for block in requires:
        for line in block.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("//"):
                deps.append(line)
    deps.extend(single)

    sections.append("📦 go.mod (Go)")
    if deps:
        sections.append(f"  Dependencies ({len(deps)}):")
        for d in deps:
            sections.append(f"    {d}")
    else:
        sections.append("  No dependencies found")
    return sections


DEP_FILES = [
    ("pyproject.toml", parse_pyproject),
    ("requirements.txt", parse_requirements),
    ("setup.py", parse_setup_py),
    ("package.json", parse_package_json),
    ("Cargo.toml", parse_cargo_toml),
    ("go.mod", parse_go_mod),
]


def main():
    parser = argparse.ArgumentParser(description="Check project dependencies")
    parser.add_argument("--path", required=True, help="Root path of the project")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"Error: path does not exist: {root}", file=sys.stderr)
        sys.exit(1)
    if not root.is_dir():
        print(f"Error: path is not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    parts: list[str] = []
    parts.append(f"=== Dependency Check: {root.resolve()} ===")
    parts.append("")

    found_count = 0
    total_deps = 0

    for filename, parser_func in DEP_FILES:
        filepath = root / filename
        if filepath.exists():
            found_count += 1
            section_lines = parser_func(filepath)
            parts.extend(section_lines)
            parts.append("")
            # Count deps (lines starting with 4 spaces that aren't headers)
            total_deps += sum(1 for l in section_lines if l.startswith("    "))

    if found_count == 0:
        parts.append("No dependency files found.")
        parts.append("Supported: pyproject.toml, requirements.txt, setup.py, package.json, Cargo.toml, go.mod")
    else:
        parts.append(f"Summary: Found {found_count} dependency file(s), {total_deps} total dependencies")

    print("\n".join(parts))


if __name__ == "__main__":
    main()

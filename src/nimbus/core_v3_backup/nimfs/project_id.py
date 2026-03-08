"""
NimFS Project ID & Path Utilities

Converts workspace paths to project directory names using the same strategy
as Claude: replace '/' with '-' in the absolute path.

Example:
    /Users/wangqing/sourcecode/nimbus → Users-wangqing-sourcecode-nimbus
"""

from pathlib import Path

# =============================================================================
# Project ID
# =============================================================================


def get_project_id(workspace_path: str | Path) -> str:
    """
    Convert a workspace absolute path to a NimFS project directory name.

    Follows the same convention as Claude's ~/.claude/projects/:
    - Resolve to absolute path
    - Strip leading '/'
    - Replace all '/' with '-'

    Args:
        workspace_path: Workspace root directory (str or Path)

    Returns:
        Project directory name string.

    Examples:
        >>> get_project_id("/Users/wangqing/sourcecode/nimbus")
        'Users-wangqing-sourcecode-nimbus'
        >>> get_project_id("/home/user/project")
        'home-user-project'
    """
    resolved = Path(workspace_path).resolve()
    return str(resolved).lstrip("/").replace("/", "-")


# =============================================================================
# Path Utilities
# =============================================================================


def get_nimfs_root() -> Path:
    """
    Return the global NimFS root directory: ~/.nimbus/fs/

    Creates the directory tree on first access:
        ~/.nimbus/fs/
        ├── global/
        └── projects/
    """
    root = Path.home() / ".nimbus" / "fs"
    root.mkdir(parents=True, exist_ok=True)
    (root / "global").mkdir(exist_ok=True)
    (root / "projects").mkdir(exist_ok=True)
    return root


def get_global_root() -> Path:
    """
    Return the global memory root: ~/.nimbus/fs/global/

    Initializes subdirectories for globally-scoped categories:
        global/
        ├── profile/
        └── preferences/
    """
    root = get_nimfs_root() / "global"
    for category in ("profile", "preferences"):
        (root / category).mkdir(parents=True, exist_ok=True)
    return root


def get_project_root(workspace_path: str | Path) -> Path:
    """
    Return the project-level NimFS root for the given workspace.

    Initializes the full directory tree on first access:
        projects/{project_id}/
        ├── memory/
        │   ├── profile/
        │   ├── preferences/
        │   ├── entities/
        │   ├── events/
        │   ├── cases/
        │   └── patterns/
        └── artifacts/
            └── index.json   ← empty JSON array on first creation

    Args:
        workspace_path: Workspace root directory.

    Returns:
        Path to the project root directory.
    """
    project_id = get_project_id(workspace_path)
    project_root = get_nimfs_root() / "projects" / project_id

    # Initialize memory partition
    memory_root = project_root / "memory"
    for category in ("profile", "preferences", "entities", "events", "cases", "patterns"):
        (memory_root / category).mkdir(parents=True, exist_ok=True)

    # Initialize artifacts partition
    artifacts_root = project_root / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    index_file = artifacts_root / "index.json"
    if not index_file.exists():
        index_file.write_text("[]", encoding="utf-8")

    return project_root


def parse_nimfs_ref(ref: str) -> tuple[str, str]:
    """
    Parse a nimfs:// URI into (kind, id).

    Supported formats:
        nimfs://artifact/{artifact_id}  → ("artifact", artifact_id)
        nimfs://memory/{memory_id}      → ("memory", memory_id)

    Args:
        ref: nimfs:// URI string.

    Returns:
        Tuple of (kind, id).

    Raises:
        ValueError: If the URI format is invalid.
    """
    if not ref.startswith("nimfs://"):
        raise ValueError(f"Invalid NimFS reference: '{ref}'. Must start with 'nimfs://'")

    parts = ref[len("nimfs://"):].split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Invalid NimFS reference: '{ref}'. "
            "Expected format: nimfs://artifact/{{id}} or nimfs://memory/{{id}}"
        )

    kind, item_id = parts
    if kind not in ("artifact", "memory"):
        raise ValueError(f"Unknown NimFS reference kind: '{kind}'. Expected 'artifact' or 'memory'")

    return kind, item_id


def make_artifact_ref(artifact_id: str) -> str:
    """Create a nimfs://artifact/ URI from an artifact ID."""
    return f"nimfs://artifact/{artifact_id}"


def make_memory_ref(memory_id: str) -> str:
    """Create a nimfs://memory/ URI from a memory ID."""
    return f"nimfs://memory/{memory_id}"

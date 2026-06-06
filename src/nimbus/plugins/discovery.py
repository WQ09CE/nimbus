"""Plugin discovery from entry points and local pyproject directories."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore

from .models import PluginDescriptor

logger = logging.getLogger("nimbus.plugins.discovery")

ENTRY_POINT_GROUP = "nimbus.plugins"


def discover_entry_points() -> List[PluginDescriptor]:
    """Discover installed plugins without importing plugin code."""
    try:
        eps = importlib.metadata.entry_points()
        selected = eps.select(group=ENTRY_POINT_GROUP) if hasattr(eps, "select") else eps.get(ENTRY_POINT_GROUP, [])
    except Exception as exc:
        logger.warning("Failed to inspect plugin entry points: %s", exc)
        return []

    descriptors: List[PluginDescriptor] = []
    for ep in selected:
        dist = getattr(ep, "dist", None)
        metadata = getattr(dist, "metadata", {}) if dist else {}
        version = getattr(dist, "version", "") if dist else ""
        descriptors.append(
            PluginDescriptor(
                name=ep.name,
                source="entry_point",
                version=str(version or ""),
                description=str(metadata.get("Summary", "") if metadata else ""),
                entry=ep.value,
                metadata={"group": ENTRY_POINT_GROUP},
            )
        )
    return descriptors


def discover_local_plugins(roots: Iterable[Path]) -> List[PluginDescriptor]:
    """Discover local plugin directories by reading pyproject.toml only."""
    descriptors: List[PluginDescriptor] = []
    for root in roots:
        root = root.expanduser()
        if not root.exists() or not root.is_dir():
            continue
        candidates = [root] if (root / "pyproject.toml").exists() else sorted(p for p in root.iterdir() if p.is_dir())
        for candidate in candidates:
            descriptor = descriptor_from_pyproject(candidate)
            if descriptor:
                descriptors.append(descriptor)
    return descriptors


def descriptor_from_pyproject(root: Path) -> Optional[PluginDescriptor]:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return None

    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Failed to read plugin pyproject %s: %s", pyproject, exc)
        return None

    tool_nimbus = data.get("tool", {}).get("nimbus", {})
    if not isinstance(tool_nimbus, dict):
        return None

    project = data.get("project", {})
    name = str(tool_nimbus.get("name") or project.get("name") or root.name).strip()
    if not name:
        return None

    entry = str(tool_nimbus.get("entry") or tool_nimbus.get("plugin") or "").strip()
    skills = tool_nimbus.get("skills", [])
    if isinstance(skills, str):
        skills = [skills]
    elif not isinstance(skills, list):
        skills = []

    return PluginDescriptor(
        name=name,
        source="local",
        version=str(tool_nimbus.get("version") or project.get("version") or "").strip(),
        description=str(tool_nimbus.get("description") or project.get("description") or "").strip(),
        path=root,
        entry=entry,
        default_enabled=bool(tool_nimbus.get("default_enabled", False)),
        metadata={
            "skills": [str(p) for p in skills],
            "pyproject": str(pyproject),
        },
    )


def load_entry_point_plugin(name: str) -> object:
    eps = importlib.metadata.entry_points()
    selected = eps.select(group=ENTRY_POINT_GROUP) if hasattr(eps, "select") else eps.get(ENTRY_POINT_GROUP, [])
    for ep in selected:
        if ep.name == name:
            loaded = ep.load()
            return _materialize_plugin(loaded)
    raise KeyError(f"Plugin entry point '{name}' not found")


def load_local_plugin(descriptor: PluginDescriptor, generation: int) -> object:
    if not descriptor.path:
        raise ValueError(f"Local plugin '{descriptor.name}' has no path")
    if not descriptor.entry:
        raise ValueError(f"Local plugin '{descriptor.name}' has no [tool.nimbus].entry")

    target, _, attr = descriptor.entry.partition(":")
    root = descriptor.path
    target_path = (root / target).resolve()

    if target_path.exists():
        module = _load_module_from_path(descriptor.name, target_path, generation)
        loaded = getattr(module, attr) if attr else module
        return _materialize_plugin(loaded)

    module_name = target
    with _prepend_sys_path(root):
        module = __import__(module_name, fromlist=[attr] if attr else ["*"])
    loaded = getattr(module, attr) if attr else module
    return _materialize_plugin(loaded)


def _materialize_plugin(loaded: object) -> object:
    return loaded() if callable(loaded) else loaded


def _load_module_from_path(plugin_name: str, path: Path, generation: int) -> object:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in plugin_name)
    module_name = f"_nimbus_plugin_{safe_name}_{generation}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load plugin module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _prepend_sys_path(path: Path) -> Iterator[None]:
    value = str(path)
    sys.path.insert(0, value)
    try:
        yield
    finally:
        try:
            sys.path.remove(value)
        except ValueError:
            pass

"""Plugin manager with lazy activation and generation snapshots."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import pluggy

from nimbus.config import NIMBUS_HOME, NimbusConfig
from nimbus.core.tools.registry import ToolDefinition
from nimbus.skills.loader import SkillLoader
from nimbus.skills.models import SkillManifest

from .discovery import discover_entry_points, discover_local_plugins, load_entry_point_plugin, load_local_plugin
from .models import (
    PluginContext,
    PluginDescriptor,
    PluginManifest,
    PluginSnapshot,
    SkillContribution,
    ToolContribution,
)
from .spec import NimbusPluginSpec, PROJECT_NAME

logger = logging.getLogger("nimbus.plugins.manager")


class PluginManager:
    """Discover plugins without import, then activate selected plugins lazily."""

    def __init__(self, roots: Iterable[Path] | None = None):
        self.roots = list(roots or [])
        self._generation = 0
        self._descriptors: Dict[str, PluginDescriptor] = {}
        self._skill_loader = SkillLoader()

    @classmethod
    def from_config(cls, config: NimbusConfig) -> "PluginManager":
        roots = [NIMBUS_HOME / "plugins"]
        roots.extend(Path(p).expanduser() for p in getattr(config, "plugin_paths", []))
        return cls(roots)

    def discover(self) -> Dict[str, PluginDescriptor]:
        descriptors: Dict[str, PluginDescriptor] = {}
        for descriptor in discover_entry_points():
            descriptors[descriptor.name] = descriptor
        for descriptor in discover_local_plugins(self.roots):
            descriptors[descriptor.name] = descriptor
        self._descriptors = descriptors
        return dict(descriptors)

    def reload(self) -> Dict[str, PluginDescriptor]:
        """Refresh discovery metadata and advance the snapshot generation."""
        self._generation += 1
        return self.discover()

    def list_plugins(self) -> List[dict]:
        return [
            descriptor.to_public_dict()
            for descriptor in self.discover().values()
        ]

    def snapshot(
        self,
        enabled_names: Sequence[str] | None = None,
        context: PluginContext | None = None,
    ) -> PluginSnapshot:
        descriptors = self.discover()
        if enabled_names is None:
            names = [d.name for d in descriptors.values() if d.default_enabled]
        else:
            names = list(enabled_names)

        manifests: Dict[str, PluginManifest] = {}
        tools: List[ToolContribution] = []
        skills: List[SkillManifest] = []

        for name in names:
            descriptor = descriptors.get(name)
            if not descriptor:
                logger.warning("Configured plugin '%s' was not found", name)
                continue

            plugin_context = PluginContext(
                plugin_name=name,
                generation=self._generation,
                session_id=context.session_id if context else "",
                workspace=context.workspace if context else "",
                metadata=dict(context.metadata) if context else {},
            )

            manifest = _manifest_from_descriptor(descriptor)
            if descriptor.entry:
                try:
                    plugin_obj = _activate_plugin(descriptor, self._generation)
                    hook_manifest, hook_tools, hook_skills = self._collect_hook_contributions(
                        plugin_obj,
                        plugin_context,
                        descriptor,
                    )
                    if hook_manifest:
                        manifest = _merge_manifest(manifest, hook_manifest)
                    tools.extend(hook_tools)
                    skills.extend(hook_skills)
                except Exception as exc:
                    logger.warning("Failed to activate plugin '%s': %s", name, exc)
                    continue

            skills.extend(self._load_static_skills(descriptor))
            manifests[name] = manifest

        return PluginSnapshot(
            generation=self._generation,
            manifests=manifests,
            tools=tools,
            skills=skills,
        )

    def _collect_hook_contributions(
        self,
        plugin_obj: object,
        context: PluginContext,
        descriptor: PluginDescriptor,
    ) -> tuple[PluginManifest | None, List[ToolContribution], List[SkillManifest]]:
        pm = pluggy.PluginManager(PROJECT_NAME)
        pm.add_hookspecs(NimbusPluginSpec)
        pm.register(plugin_obj, name=descriptor.name)

        manifest = _first_manifest(pm.hook.nimbus_plugin_manifest())
        tools = [
            _normalize_tool_contribution(item, descriptor.name)
            for result in pm.hook.nimbus_register_tools(ctx=context)
            for item in _as_iterable(result)
        ]
        skills: List[SkillManifest] = []
        for result in pm.hook.nimbus_register_skills(ctx=context):
            for item in _as_iterable(result):
                skills.extend(self._normalize_skill_contribution(item, descriptor))
        return manifest, tools, skills

    def _normalize_skill_contribution(
        self,
        item: object,
        descriptor: PluginDescriptor,
    ) -> List[SkillManifest]:
        if isinstance(item, SkillContribution):
            return [item.manifest]
        if isinstance(item, SkillManifest):
            return [item]
        if isinstance(item, (str, Path)):
            base = descriptor.path or Path.cwd()
            path = Path(item)
            if not path.is_absolute():
                path = base / path
            manifest = self._skill_loader.load_dir(path)
            return [manifest] if manifest else []
        logger.warning("Ignoring unsupported skill contribution from plugin '%s': %r", descriptor.name, item)
        return []

    def _load_static_skills(self, descriptor: PluginDescriptor) -> List[SkillManifest]:
        if not descriptor.path:
            return []
        skills: List[SkillManifest] = []
        for raw_path in descriptor.metadata.get("skills", []):
            path = Path(raw_path)
            if not path.is_absolute():
                path = descriptor.path / path
            manifest = self._skill_loader.load_dir(path)
            if manifest:
                skills.append(manifest)
        return skills


def _activate_plugin(descriptor: PluginDescriptor, generation: int) -> object:
    if descriptor.source == "entry_point":
        return load_entry_point_plugin(descriptor.name)
    return load_local_plugin(descriptor, generation)


def _manifest_from_descriptor(descriptor: PluginDescriptor) -> PluginManifest:
    return PluginManifest(
        name=descriptor.name,
        version=descriptor.version,
        description=descriptor.description,
        default_enabled=descriptor.default_enabled,
        metadata={"source": descriptor.source},
    )


def _merge_manifest(base: PluginManifest, override: PluginManifest) -> PluginManifest:
    return PluginManifest(
        name=override.name or base.name,
        version=override.version or base.version,
        description=override.description or base.description,
        default_enabled=override.default_enabled or base.default_enabled,
        trusted=override.trusted or base.trusted,
        metadata={**base.metadata, **override.metadata},
    )


def _first_manifest(results: Iterable[object]) -> PluginManifest | None:
    for result in results:
        for item in _as_iterable(result):
            if isinstance(item, PluginManifest):
                return item
            if isinstance(item, dict):
                return PluginManifest(
                    name=str(item.get("name", "")).strip(),
                    version=str(item.get("version", "")).strip(),
                    description=str(item.get("description", "")).strip(),
                    default_enabled=bool(item.get("default_enabled", False)),
                    trusted=bool(item.get("trusted", False)),
                    metadata=dict(item.get("metadata", {})),
                )
    return None


def _normalize_tool_contribution(item: object, plugin_name: str) -> ToolContribution:
    if isinstance(item, ToolContribution):
        if item.plugin_name:
            return item
        return ToolContribution(
            definition=item.definition,
            handler=item.handler,
            plugin_name=plugin_name,
            requires_approval=item.requires_approval,
            metadata=dict(item.metadata),
        )
    if callable(item) and hasattr(item, "_tool_definition"):
        return ToolContribution(
            definition=item._tool_definition,
            handler=item,
            plugin_name=plugin_name,
        )
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], ToolDefinition) and callable(item[1]):
        return ToolContribution(
            definition=item[0],
            handler=item[1],
            plugin_name=plugin_name,
        )
    raise TypeError(f"Unsupported tool contribution from plugin '{plugin_name}': {item!r}")


def _as_iterable(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]

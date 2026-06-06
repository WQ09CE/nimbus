"""Nimbus plugin discovery and activation."""

from .manager import PluginManager
from .models import (
    PluginContext,
    PluginDescriptor,
    PluginManifest,
    PluginSnapshot,
    SkillContribution,
    ToolContribution,
)
from .spec import hookimpl, hookspec

__all__ = [
    "PluginContext",
    "PluginDescriptor",
    "PluginManager",
    "PluginManifest",
    "PluginSnapshot",
    "SkillContribution",
    "ToolContribution",
    "hookimpl",
    "hookspec",
]

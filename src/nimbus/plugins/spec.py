"""Pluggy hookspecs for Nimbus plugins."""

from __future__ import annotations

from typing import Iterable

import pluggy

from .models import PluginContext, PluginManifest, SkillContribution, ToolContribution

PROJECT_NAME = "nimbus"

hookspec = pluggy.HookspecMarker(PROJECT_NAME)
hookimpl = pluggy.HookimplMarker(PROJECT_NAME)


class NimbusPluginSpec:
    """Hooks implemented by Nimbus plugins."""

    @hookspec
    def nimbus_plugin_manifest(self) -> PluginManifest | dict | None:
        """Return runtime manifest metadata for this plugin."""

    @hookspec
    def nimbus_register_tools(
        self,
        ctx: PluginContext,
    ) -> Iterable[ToolContribution | object] | None:
        """Return tool contributions for this plugin."""

    @hookspec
    def nimbus_register_skills(
        self,
        ctx: PluginContext,
    ) -> Iterable[SkillContribution | object] | None:
        """Return skill contributions for this plugin."""

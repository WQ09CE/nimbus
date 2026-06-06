# Nimbus Plugins

Nimbus plugins are runtime extension packages for distributing tools and skills.
The v0 implementation uses Python packaging conventions instead of a new
manifest format:

- Installed packages are discovered through the `nimbus.plugins` entry point
  group.
- Local development plugins are discovered by scanning `~/.nimbus/plugins/`
  and configured `plugins.paths` directories for `pyproject.toml`.
- Local plugin metadata lives under `[tool.nimbus]`.
- Plugin code is imported only when the plugin is enabled and activated.

## Local Plugin Shape

```toml
[project]
name = "nimbus-hello-plugin"
version = "0.1.0"
description = "Example Nimbus plugin"

[tool.nimbus]
name = "hello"
entry = "plugin.py:nimbus_plugin"
skills = ["skills/hello"]
```

`entry` points to a factory, object, or module containing Nimbus pluggy
hook implementations.

```python
from nimbus.core.tools.registry import ToolParameter, tool
from nimbus.plugins import PluginManifest, hookimpl


@tool(
    name="hello",
    description="Return a greeting.",
    parameters=[ToolParameter("name", "string", "Name to greet")],
)
def hello(name: str) -> str:
    return f"hello {name}"


class HelloPlugin:
    @hookimpl
    def nimbus_plugin_manifest(self):
        return PluginManifest(name="hello", version="0.1.0")

    @hookimpl
    def nimbus_register_tools(self, ctx):
        return [hello]


def nimbus_plugin():
    return HelloPlugin()
```

## Configuration

```json
{
  "plugins": {
    "enabled": ["hello"],
    "paths": ["/path/to/local/plugins"]
  }
}
```

Environment overrides:

```bash
NIMBUS_PLUGINS=hello,other
NIMBUS_PLUGIN_PATHS=/path/a:/path/b
```

## Runtime Model

Plugins use generation snapshots:

1. Discovery scans metadata without importing plugin code.
2. Activation imports only enabled plugins.
3. Activated contributions are captured in a `PluginSnapshot`.
4. Sessions use the snapshot they were created with.
5. Reload creates a new generation for future activation.

Nimbus does not rely on in-place `importlib.reload()` for plugin code. Local
file plugins are loaded with generation/hash-based module names so new
activations can use new code while old snapshots finish safely.

All plugin tools are registered into the normal `ToolRegistry` and execute
through `KernelGate`; plugins do not bypass the existing tool policy path.

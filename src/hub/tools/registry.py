"""Shared, permission-aware tool registry used by agents and optional MCP adapters."""
from dataclasses import dataclass
from typing import Callable, Any

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    requires_confirmation: bool = False
    source: str = "builtin"

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, *, replace: bool = False):
        if spec.name in self._tools and not replace:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def list(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda x: x.name)

    def execute(self, name: str, *, confirmed: bool = False, **kwargs):
        tool = self.get(name)
        if tool.requires_confirmation and not confirmed:
            raise PermissionError(f"Tool '{name}' requires explicit user confirmation")
        return tool.handler(**kwargs)

_REGISTRY = ToolRegistry()

def get_tool_registry() -> ToolRegistry:
    return _REGISTRY

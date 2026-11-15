"""Optional MCP bridge. Install langchain-mcp-adapters and configure HUB_MCP_SERVERS JSON to enable."""
from __future__ import annotations
import json, os
from .registry import ToolRegistry, ToolSpec

async def load_mcp_tools(registry: ToolRegistry):
    raw = os.getenv("HUB_MCP_SERVERS", "").strip()
    if not raw:
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise RuntimeError("MCP đang được cấu hình nhưng thiếu package: pip install langchain-mcp-adapters") from exc
    servers = json.loads(raw)
    client = MultiServerMCPClient(servers)
    tools = await client.get_tools()
    for tool in tools:
        registry.register(ToolSpec(name=tool.name, description=tool.description or "MCP tool", handler=(lambda _tool=tool, **kwargs: _tool.invoke(kwargs)), requires_confirmation=True, source="mcp"), replace=True)
    return tools


from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent.schemas import ToolResult


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str

    async def run(self, name: str, company: str, **kwargs) -> ToolResult: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def tool_descriptions(self) -> str:
        lines: list[str] = []
        for tool in self._tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)


registry = ToolRegistry()

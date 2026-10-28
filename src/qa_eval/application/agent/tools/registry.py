from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


class ToolNotAllowedError(PermissionError):
    pass


class ToolNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], BaseModel]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str, *, allowed_tools: tuple[str, ...]) -> ToolDefinition:
        if name not in allowed_tools:
            raise ToolNotAllowedError(f"Tool is not allowed by the loaded Skill: {name}")
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool not found: {name}") from exc

    def input_schema(self, name: str, *, allowed_tools: tuple[str, ...]) -> dict[str, Any]:
        return self.get(name, allowed_tools=allowed_tools).input_model.model_json_schema()

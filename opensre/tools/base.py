"""Base tool interface for opensre integrations.

All tools/integrations must inherit from BaseTool and implement
the required abstract methods defined here.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ToolParams:
    """Container for extracted tool parameters."""

    raw: Dict[str, Any] = field(default_factory=dict)
    validated: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


@dataclass
class ToolResult:
    """Standardized result returned by all tools."""

    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, data: Any, **metadata) -> "ToolResult":
        return cls(success=True, data=data, metadata=metadata)

    @classmethod
    def fail(cls, error: str, **metadata) -> "ToolResult":
        return cls(success=False, error=error, metadata=metadata)


class BaseTool(ABC):
    """Abstract base class for all opensre tools.

    Subclasses must implement:
      - tool_name (property)
      - is_available()
      - extract_params()
      - run()

    Example::

        class MyTool(BaseTool):
            @property
            def tool_name(self) -> str:
                return "my_tool"

            def is_available(self) -> bool:
                return True

            def extract_params(self, raw: Dict[str, Any]) -> ToolParams:
                params = ToolParams(raw=raw)
                if "target" not in raw:
                    params.errors.append("'target' is required")
                else:
                    params.validated["target"] = raw["target"]
                return params

            def run(self, params: ToolParams) -> ToolResult:
                return ToolResult.ok(data={"target": params.validated["target"]})
    """

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """Unique snake_case identifier for this tool."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the tool's dependencies/credentials are present."""
        ...

    @abstractmethod
    def extract_params(self, raw: Dict[str, Any]) -> ToolParams:
        """Validate and normalise raw input into a ToolParams instance."""
        ...

    @abstractmethod
    def run(self, params: ToolParams) -> ToolResult:
        """Execute the tool logic and return a ToolResult."""
        ...

    def execute(self, raw: Dict[str, Any]) -> ToolResult:
        """High-level entry point: validate availability, extract params, run.

        Args:
            raw: Arbitrary key/value input from the caller.

        Returns:
            ToolResult with success/failure information.
        """
        if not self.is_available():
            logger.warning("Tool '%s' is not available", self.tool_name)
            return ToolResult.fail(f"Tool '{self.tool_name}' is not available")

        params = self.extract_params(raw)
        if not params.is_valid:
            logger.debug(
                "Tool '%s' param validation failed: %s",
                self.tool_name,
                params.errors,
            )
            return ToolResult.fail(
                f"Invalid params for '{self.tool_name}': {'; '.join(params.errors)}"
            )

        try:
            result = self.run(params)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Tool '%s' raised an unexpected error", self.tool_name)
            return ToolResult.fail(str(exc))

        return result

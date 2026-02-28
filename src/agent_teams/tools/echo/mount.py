"""Mount the echo tool for writing a short echo."""

from __future__ import annotations

from pydantic_ai import Agent

from agent_teams.tools.runtime import ToolDeps, ToolContext
from agent_teams.tools.tool_helpers import execute_tool


def mount(agent: Agent[ToolDeps, str]) -> None:
    @agent.tool
    def echo(ctx: ToolContext, input_string: str) -> str:
        """
        Echo function that returns the input string unchanged.

        Args:
            ctx: The tool context
            input_string (str): The string to echo

        Returns:
            str: The input string unchanged

        Raises:
            None: Function handles errors gracefully with return values
        """

        def _action() -> str:
            # Validate input is a string
            if not isinstance(input_string, str):
                raise TypeError(
                    f"Input must be a string. Received: {type(input_string).__name__}"
                )

            # Return the input string unchanged
            return input_string

        return execute_tool(
            ctx,
            tool_name="echo",
            args_summary={
                "input_length": len(input_string)
                if isinstance(input_string, str)
                else 0
            },
            action=_action,
        )

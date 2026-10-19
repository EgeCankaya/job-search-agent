"""The memory interface (MODE-1). AgentCore Memory (cloud) and the local
store (MacBook mode) arrive in week 4 (MEM-1, MEM-2); until then both modes
remember nothing, and each question on the Ask page stands alone."""

import os
from typing import Protocol

from inbox_agent.mode import agent_mode


class Memory(Protocol):
    def context(self, session_id: str) -> list[str]:
        """What the agent should know for this conversation."""
        ...

    def record(self, session_id: str, role: str, text: str) -> None:
        """One turn of the conversation: role is "user" or "assistant"."""
        ...


class NoMemory:
    def context(self, session_id: str) -> list[str]:
        return []

    def record(self, session_id: str, role: str, text: str) -> None:
        return None


def memory_for(environ=os.environ) -> Memory:
    agent_mode(environ)  # an unknown mode fails here too
    return NoMemory()

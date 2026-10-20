"""The model interface (MODE-1). The model ID is config, never code.

Route 1 was chosen on 2026-10-18 (OQ-39 b, OQ-41 f): the CDK sets
CLASSIFIER_MODEL_ID to the `eu.` Nova Micro profile and CHAT_MODEL_ID to the
`eu.` Nova Lite profile (infra/config.py). The code keeps no default for
them, so each ID lives in one place. In MacBook mode both purposes use the
local Ollama model (settings.LOCAL_MODEL_ID). Tests use a fake model.

LangChain is imported only inside the functions that need it, so the
Lambdas, which ship without it, can still import this.
"""

import os
from dataclasses import dataclass
from typing import Protocol

from inbox_agent import settings
from inbox_agent.mode import agent_mode

CLASSIFIER_MODEL_ID_ENV = "CLASSIFIER_MODEL_ID"
CHAT_MODEL_ID_ENV = "CHAT_MODEL_ID"

# What each call needs: classify asks for one short JSON object (CLS-1), chat
# for an answer to one question (CHT-1).
MAX_TOKENS = {"classify": 300, "chat": 1000}
MODEL_ID_ENV = {"classify": CLASSIFIER_MODEL_ID_ENV, "chat": CHAT_MODEL_ID_ENV}


class Model(Protocol):
    def complete(self, system: str, prompt: str) -> str:
        """One system rule, one user message, the text of the reply."""
        ...


def chat_model(purpose: str, environ=os.environ):
    """The LangChain chat model for one purpose, "classify" or "chat", in
    the configured mode: Nova on Bedrock (cloud) or Ollama (local)."""
    if purpose not in MAX_TOKENS:
        raise ValueError(f"unknown model purpose {purpose!r}")
    if agent_mode(environ) == "local":
        from langchain_ollama import ChatOllama

        # No thinking: classification wants a short JSON answer, and chat
        # replies have a 15 s target (NFR-LAT-3).
        return ChatOllama(
            model=settings.LOCAL_MODEL_ID,
            base_url=settings.OLLAMA_URL,
            temperature=0,
            num_predict=MAX_TOKENS[purpose],
            reasoning=False,
        )

    model_id = environ.get(MODEL_ID_ENV[purpose], "").strip()
    if not model_id:
        raise ValueError(f"Set {MODEL_ID_ENV[purpose]}; the model route (OQ-39 b) picks the ID.")
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model_id=model_id,
        region_name=settings.AWS_REGION,
        temperature=0,
        max_tokens=MAX_TOKENS[purpose],
    )


@dataclass(frozen=True)
class LangChainModel:
    """A LangChain chat model behind the `Model` interface, so classify.py
    runs unchanged in both modes."""

    chat: object  # a LangChain BaseChatModel

    def complete(self, system: str, prompt: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        reply = self.chat.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
        content = reply.content
        if isinstance(content, str):
            return content
        # Some providers return content blocks; only the text counts.
        return "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
            if isinstance(block, str) or block.get("type") == "text"
        )

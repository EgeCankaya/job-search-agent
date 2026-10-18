"""The model interface (MODE-1). The model ID is config, never code.

The route in OQ-39 (b) decides the ID (Nova Micro or Nova Lite, `eu.`
profile or in-Region) and isn't picked yet, so there is no default. Tests
use a fake model.
"""

import os
from dataclasses import dataclass
from typing import Protocol

from inbox_agent import settings

CLASSIFIER_MODEL_ID_ENV = "CLASSIFIER_MODEL_ID"


class Model(Protocol):
    def complete(self, system: str, prompt: str) -> str:
        """One system rule, one user message, the text of the reply."""
        ...


@dataclass(frozen=True)
class BedrockConverseModel:
    client: object  # boto3 bedrock-runtime client
    model_id: str
    max_tokens: int = 200

    def complete(self, system: str, prompt: str) -> str:
        response = self.client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0},
        )
        return "".join(block.get("text", "") for block in response["output"]["message"]["content"])


def classifier_model(environ=os.environ) -> Model:
    model_id = environ.get(CLASSIFIER_MODEL_ID_ENV, "").strip()
    if not model_id:
        raise ValueError(f"Set {CLASSIFIER_MODEL_ID_ENV}; the model route (OQ-39 b) picks the ID.")
    import boto3

    return BedrockConverseModel(boto3.client("bedrock-runtime", region_name=settings.AWS_REGION), model_id)

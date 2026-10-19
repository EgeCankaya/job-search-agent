"""MODE-1: AGENT_MODE picks the model and memory backends. Nothing here
calls Bedrock or Ollama: building a LangChain model makes no request."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from inbox_agent import model, settings
from inbox_agent.classes import EmailClass
from inbox_agent.classify import classify
from inbox_agent.memory import NoMemory, memory_for
from inbox_agent.mode import agent_mode
from inbox_agent.trim import TrimmedEmail

CLOUD = {"CLASSIFIER_MODEL_ID": "eu.amazon.nova-micro-v1:0", "CHAT_MODEL_ID": "eu.amazon.nova-lite-v1:0"}


def test_mode_defaults_to_cloud_and_ignores_case():
    assert agent_mode({}) == "cloud"
    assert agent_mode({"AGENT_MODE": " Local "}) == "local"


def test_unknown_mode_fails():
    with pytest.raises(ValueError, match="AGENT_MODE"):
        agent_mode({"AGENT_MODE": "hybrid"})
    with pytest.raises(ValueError):
        model.chat_model("chat", {"AGENT_MODE": "hybrid", **CLOUD})
    with pytest.raises(ValueError):
        memory_for({"AGENT_MODE": "hybrid"})


@pytest.mark.parametrize(
    ("purpose", "model_id", "max_tokens"),
    [("classify", "eu.amazon.nova-micro-v1:0", 300), ("chat", "eu.amazon.nova-lite-v1:0", 1000)],
)
def test_cloud_uses_the_route_1_profile_for_each_purpose(purpose, model_id, max_tokens):
    chat = model.chat_model(purpose, CLOUD)
    assert type(chat).__name__ == "ChatBedrockConverse"
    assert chat.model_id == model_id
    assert chat.region_name == "eu-north-1"
    assert chat.temperature == 0
    assert chat.max_tokens == max_tokens


@pytest.mark.parametrize("purpose", ["classify", "chat"])
def test_cloud_has_no_default_model_id(purpose):
    with pytest.raises(ValueError, match=model.MODEL_ID_ENV[purpose]):
        model.chat_model(purpose, {})


@pytest.mark.parametrize("purpose", ["classify", "chat"])
def test_local_uses_ollama_on_loopback_without_thinking(purpose):
    chat = model.chat_model(purpose, {"AGENT_MODE": "local"})
    assert type(chat).__name__ == "ChatOllama"
    assert chat.model == settings.LOCAL_MODEL_ID == "qwen3.8:27b"
    assert chat.base_url == "http://127.0.0.1:11434"
    assert chat.reasoning is False
    assert chat.num_predict == model.MAX_TOKENS[purpose]


def test_unknown_purpose_fails():
    with pytest.raises(ValueError, match="purpose"):
        model.chat_model("drafting", CLOUD)


def test_the_classifier_runs_through_a_langchain_model():
    answer = json.dumps(
        {"classes": ["interview_invite"], "asks_by_date": "yes", "company": "Kuzey", "role": None, "date": "2026-11-03"}
    )
    email = TrimmedEmail(subject="Mülakat", sender="ik@kuzey.example.com", date="2026-10-19T09:00:00+00:00", body="Davet")
    result = classify(email, model.LangChainModel(FakeListChatModel(responses=[answer])))
    assert result.email_class is EmailClass.INTERVIEW_INVITE
    assert result.company == "Kuzey"


def test_content_blocks_are_reduced_to_their_text():
    class BlockReply:
        content = [{"type": "reasoning", "text": "hidden"}, {"type": "text", "text": "{"}, "}"]

    class BlockModel:
        def invoke(self, messages):
            return BlockReply()

    assert model.LangChainModel(BlockModel()).complete("system", "prompt") == "{}"


@pytest.mark.parametrize("mode", ["cloud", "local"])
def test_memory_is_empty_in_both_modes_until_week_4(mode):
    memory = memory_for({"AGENT_MODE": mode})
    assert isinstance(memory, NoMemory)
    memory.record("session-1", "user", "Brightpath'da ne açık?")
    assert memory.context("session-1") == []

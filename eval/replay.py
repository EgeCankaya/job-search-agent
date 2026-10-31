"""Record and replay model replies (OQ-47 (a)), so Tier 1 runs the real code
on GitHub's runners, which have no AWS credential and no local model.

A reply is keyed by a SHA-256 of what the model was given: the model ID, the
system rule and the prompt for a single call (classify, draft), or for the
chat path the messages so far and the tool specs. So a change to a prompt,
the trimming, a tool description or a model ID misses, and the run stops
with `StaleReply` until the replies are recorded again on the Mac
(`eval/record.py`).

`RecordingChat` wraps a real LangChain chat model: a key already in the store
is answered from it without a call (so an unchanged case is never paid for
twice), a new key calls the model and is stored. `ReplayChat` answers from
the store only. Both stand where the harnesses put a LangChain chat model:
`.invoke(messages)` for the single calls, `.bind_tools(tools).invoke(...)`
for the chat path.

Each set's replies are one file, `eval/replies/<set>.json`: key → model ID,
case ID, the reply (text, or for the chat path the text and tool calls),
and the recorded token counts. They are replies to fictional emails and
questions only. One reply per key: a call repeated with the same input in
one case (a retry at temperature 0) gets the same reply both times.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPLIES_DIR = ROOT / "eval" / "replies"
SETS = ("ev1s", "ev3", "ev4")
RERECORD = "uv run --all-groups python eval/record.py --sets {set}"


class StaleReply(LookupError):
    """No recorded reply for this input: the code or a prompt changed."""

    def __init__(self, set_name: str, case_id: str | None, key: str) -> None:
        super().__init__(
            f"no recorded reply for {set_name} case {case_id or '?'} (key {key[:12]}): "
            f"the replies are stale; re-record on the Mac with `{RERECORD.format(set=set_name)}`"
        )
        self.set_name, self.case_id, self.key = set_name, case_id, key


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        block if isinstance(block, str) else block.get("text", "")
        for block in content
        if isinstance(block, str) or block.get("type") == "text"
    )


def _message(message) -> dict:
    """What a message contributes to the key: its kind, text, tool calls and
    the call it answers. Provider metadata (IDs, timings) is left out."""
    return {
        "type": message.type,
        "text": _text(message.content),
        "tool_calls": [[c["name"], c["args"], c.get("id")] for c in getattr(message, "tool_calls", None) or []],
        "tool_call_id": getattr(message, "tool_call_id", None),
    }


def _tool(tool) -> dict:
    schema = tool.args_schema if isinstance(tool.args_schema, dict) else tool.args_schema.model_json_schema()
    return {"name": tool.name, "description": tool.description, "schema": schema}


def reply_key(model_id: str, messages, tools=()) -> str:
    payload = {"model": model_id, "messages": [_message(m) for m in messages], "tools": [_tool(t) for t in tools]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass
class ReplyStore:
    """One set's recorded replies, and which keys this run used."""

    set_name: str
    replies: dict = field(default_factory=dict)
    used: set = field(default_factory=set)
    added: set = field(default_factory=set)

    @classmethod
    def load(cls, set_name: str, directory: Path = REPLIES_DIR) -> "ReplyStore":
        path = directory / f"{set_name}.json"
        replies = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(set_name, replies)

    def save(self, directory: Path = REPLIES_DIR, *, keep_unused: bool = False) -> dict:
        """Write the keys this run used, and drop the rest unless
        `keep_unused` (a run that stopped early); the counts."""
        kept = {key: self.replies[key] for key in sorted(self.replies if keep_unused else self.used)}
        summary = {
            "set": self.set_name,
            "kept": len(self.used - self.added),
            "new": len(self.added),
            "deleted": 0 if keep_unused else len(set(self.replies) - self.used),
        }
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{self.set_name}.json").write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return summary


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


def _reply_message(stored: dict):
    from langchain_core.messages import AIMessage

    return AIMessage(
        content=stored["text"],
        tool_calls=[{"name": n, "args": a, "id": i, "type": "tool_call"} for n, a, i in stored.get("tool_calls", [])],
        usage_metadata={
            "input_tokens": stored.get("input_tokens", 0),
            "output_tokens": stored.get("output_tokens", 0),
            "total_tokens": stored.get("input_tokens", 0) + stored.get("output_tokens", 0),
        },
    )


class _Chat:
    """The shared part: a model ID, a store, the case being run, bound tools."""

    def __init__(self, store: ReplyStore, model_id: str, tools=()) -> None:
        self.store, self.model_id, self.tools = store, model_id, tuple(tools)
        self.case_id: str | None = None

    def bind_tools(self, tools):
        bound = type(self).__new__(type(self))
        bound.__dict__.update(self.__dict__, tools=tuple(tools))
        bound._parent = self
        return bound

    def _case(self) -> str | None:
        return getattr(self, "_parent", self).case_id

    def _lookup(self, key: str) -> dict | None:
        stored = self.store.replies.get(key)
        if stored is not None:
            self.store.used.add(key)
        return stored


class ReplayChat(_Chat):
    """Answers from the store only; a miss raises StaleReply."""

    def invoke(self, messages):
        key = reply_key(self.model_id, messages, self.tools)
        stored = self._lookup(key)
        if stored is None:
            raise StaleReply(self.store.set_name, self._case(), key)
        return _reply_message(stored)


class RecordingChat(_Chat):
    """Answers a known key from the store; calls the real model for a new one."""

    def __init__(self, store: ReplyStore, model_id: str, chat, tools=()) -> None:
        super().__init__(store, model_id, tools)
        self.chat = chat

    def bind_tools(self, tools):
        bound = super().bind_tools(tools)
        bound.chat = self.chat.bind_tools(list(tools))
        return bound

    def invoke(self, messages):
        key = reply_key(self.model_id, messages, self.tools)
        stored = self._lookup(key)
        if stored is None:
            reply = self.chat.invoke(messages)
            usage = getattr(reply, "usage_metadata", None) or {}
            stored = {
                "model_id": self.model_id,
                "case": self._case(),
                "text": _text(reply.content),
                "tool_calls": [[c["name"], c["args"], c.get("id")] for c in getattr(reply, "tool_calls", None) or []],
                "input_tokens": int(usage.get("input_tokens", 0)),
                "output_tokens": int(usage.get("output_tokens", 0)),
            }
            self.store.replies[key] = stored
            self.store.used.add(key)
            self.store.added.add(key)
        return _reply_message(stored)


def cloud_model_ids() -> dict[str, str]:
    """The cloud-mode model per purpose, from the one place the CDK reads them."""
    import os

    os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")
    from infra import config

    return {"classify": config.CLASSIFIER_MODEL_ID, "draft": config.CHAT_MODEL_ID, "chat": config.CHAT_MODEL_ID}


def replay_models(store: ReplyStore, purposes=("classify", "draft")) -> dict[str, ReplayChat]:
    ids = cloud_model_ids()
    return {purpose: ReplayChat(store, ids[purpose]) for purpose in purposes}


def recording_models(store: ReplyStore, purposes=("classify", "draft"), per_minute: int = 12, environ=None) -> dict[str, RecordingChat]:
    """Real Nova calls (cloud mode), paced, for keys the store doesn't have."""
    import os

    from eval.pacing import Paced, limiter_for
    from inbox_agent.model import chat_model

    ids = cloud_model_ids()
    environ = dict(environ or os.environ, AGENT_MODE="cloud", CLASSIFIER_MODEL_ID=ids["classify"], CHAT_MODEL_ID=ids["chat"])
    return {
        purpose: RecordingChat(store, ids[purpose], Paced(chat_model(purpose, environ), limiter_for(ids[purpose], per_minute)))
        for purpose in purposes
    }


def set_case(models, case_id: str | None) -> None:
    """Name the case being run, for the stale message and the stored record."""
    for model in models.values() if isinstance(models, dict) else models:
        chat = getattr(model, "chat", model)
        if isinstance(chat, _Chat):
            chat.case_id = case_id

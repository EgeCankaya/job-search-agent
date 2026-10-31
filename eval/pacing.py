"""Pacing for live model calls (OQ-47 (c)): at most 12 calls a minute per
model, because live mail shares the account's Nova quota (20 a minute), and
a throttled poll must not push live mail toward the dead-letter queue.

`Paced` wraps a LangChain chat model; every wrapper of the same model ID
shares one limiter, so the draft and chat purposes (both Nova Lite) count
together.
"""

import threading
import time
from dataclasses import dataclass, field

DEFAULT_PER_MINUTE = 12


@dataclass
class Limiter:
    per_minute: int
    clock: object = time.monotonic
    sleep: object = time.sleep
    _last: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self) -> None:
        with self._lock:
            gap = 60.0 / self.per_minute
            now = self.clock()
            if self._last is not None and now - self._last < gap:
                self.sleep(gap - (now - self._last))
                now = self.clock()
            self._last = now


_LIMITERS: dict[str, Limiter] = {}


def limiter_for(model_id: str, per_minute: int = DEFAULT_PER_MINUTE) -> Limiter:
    if model_id not in _LIMITERS:
        _LIMITERS[model_id] = Limiter(per_minute)
    return _LIMITERS[model_id]


class Paced:
    def __init__(self, chat, limiter: Limiter) -> None:
        self.chat, self.limiter = chat, limiter

    def bind_tools(self, tools):
        return Paced(self.chat.bind_tools(tools), self.limiter)

    def invoke(self, messages):
        self.limiter.wait()
        return self.chat.invoke(messages)

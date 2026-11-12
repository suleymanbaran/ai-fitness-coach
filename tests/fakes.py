from types import SimpleNamespace

from app.rag.demo import HashingEmbedder as FakeEmbedder
from app.rag.generator import LLMReply, LLMUsage

__all__ = ["FakeEmbedder", "FakeCoach"]


class FakeCoach:
    """Records every complete() call. A call carrying json_schema is a memory-extraction call
    and gets an empty update list back, so /ask tests see one answer plus one no-op extraction."""

    def __init__(
        self,
        answer: str = "Fake answer [rec-hrv-autoregulation]",
        usage: LLMUsage | None = None,
    ) -> None:
        self.calls: list[SimpleNamespace] = []
        self.answer = answer
        self.usage = usage or LLMUsage(model="fake-model", input_tokens=100, cached_tokens=40, output_tokens=10)

    def complete(self, stable: str, history: list[dict], volatile: str, **kwargs) -> LLMReply:
        self.calls.append(SimpleNamespace(stable=stable, history=list(history), volatile=volatile, kwargs=kwargs))
        if kwargs.get("json_schema") is not None:
            return LLMReply(text='{"updates": []}', usage=LLMUsage.zero("fake-model"))
        return LLMReply(text=self.answer, usage=self.usage)

    @property
    def last_call(self) -> SimpleNamespace:
        return self.calls[-1]

    @property
    def answer_call(self) -> SimpleNamespace:
        """The first non-extraction call: the answer to the user's question."""
        return next(c for c in self.calls if c.kwargs.get("json_schema") is None)

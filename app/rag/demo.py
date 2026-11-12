"""Offline stand-ins for OpenAI, used when DEMO_MODE=true and in tests."""

import hashlib
import re

import numpy as np

from app.rag.generator import LLMReply, LLMUsage

HASH_DIM = 256


class HashingEmbedder:
    """Bag-of-words hashing: texts that share words get similar vectors. Crude, but free and offline."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]

    def embed_one(self, text: str) -> list[float]:
        vector = np.zeros(HASH_DIM)
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % HASH_DIM
            vector[bucket] += 1.0
        norm = np.linalg.norm(vector)
        return (vector / norm if norm else vector).tolist()


class DemoCoach:
    """Instead of calling an LLM, returns the exact prompt it would have received.
    Matches the CoachClient.complete duck type; never invokes tools."""

    def complete(self, stable: str, history: list[dict], volatile: str, **kwargs) -> LLMReply:
        text = (
            "**Demo mode:** no OpenAI key is configured, so this is not a real AI answer. "
            "Below is exactly what would be sent to the model: the cached instruction prefix, "
            "then the user's computed metrics, the retrieved protocols, and the question.\n\n"
            f"```text\n{stable}\n\n{volatile}\n```"
        )
        return LLMReply(text=text, usage=LLMUsage.zero("demo"))

"""copilot.retrieval.embedder -- embedder profiles, swapped like the LLM (ADR-0006/0004).

`make_embedder(cfg)` dispatches on `cfg.embed_profile` -- swap = one config line.
Real backends load lazily (the model / endpoint is touched on first `encode`, not at
construction) so profile selection is testable air-gapped. `HashEmbedder` is the
deterministic test double (no deps, no network) -- injected directly, NOT
profile-selectable (mirrors llm.ScriptedLLM).
"""
import hashlib
import math
import os

from copilot.config import Config


def make_embedder(cfg: Config):
    """Return the Embedder for the configured profile (ADR-0006)."""
    if cfg.embed_profile == "nim":
        return NimEmbedder(cfg)
    if cfg.embed_profile == "unsloth-local":
        return LocalEmbedder(cfg)
    # config.py validates the enum; defense-in-depth in case it's constructed raw.
    raise ValueError(f"unknown embed_profile {cfg.embed_profile!r}")


class NimEmbedder:
    """Interim: an OpenAI-compatible /embeddings endpoint (NIM). httpx is already a
    dep. Endpoint + model come from env.

    ponytail: url/model live in env, not Config -- they're not secrets, but the
    Config field for them would go in copilot/config.py, another lane's file (F0).
    Env is the seam that doesn't cross lanes; lift to Config if F0 adds the fields.
    The nim/local model ids differ (endpoint id vs HF repo id), so the vars are
    distinct -- flipping embed_profile must not reuse the wrong model name."""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._url = os.environ.get("COPILOT_EMBED_BASE_URL", "http://127.0.0.1:8080/v1")
        self._model = os.environ.get("COPILOT_EMBED_MODEL_NIM", "bge-large-en-v1.5")

    def encode(self, texts: list[str]) -> list[list[float]]:
        import httpx
        key = self._cfg.embed_api_key
        r = httpx.post(
            f"{self._url}/embeddings",
            json={"model": self._model, "input": list(texts)},
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=30,
        )
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]


class LocalEmbedder:
    """Final (air-gapped): local sentence-transformers on the 3080Ti box
    (bge-large / gte-large class). Model loaded lazily on first encode. `cfg`
    param kept for the uniform make_embedder() call; a local model needs no key/url."""

    def __init__(self, cfg: Config):
        self._model_name = os.environ.get("COPILOT_EMBED_MODEL_LOCAL", "BAAI/bge-large-en-v1.5")
        self._model = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
        return [v.tolist() for v in
                self._model.encode(list(texts), normalize_embeddings=True)]


class HashEmbedder:
    """Deterministic bag-of-hashed-tokens embedder for tests -- no deps, no network.

    ponytail: enough to prove ranking on a fixture corpus (shared tokens -> higher
    cosine); NOT a semantic model. Real relevance = the nim/local profiles above.
    """

    def __init__(self, dim: int = 64):
        self.dim = dim

    def encode(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out

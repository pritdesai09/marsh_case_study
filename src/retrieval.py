"""BM25 keyword search over brochure chunks.

Used by the audit when a claim has no valid citation: it finds the brochure passages most
likely to support (or contradict) the claim, so the claim can still be traced to a clause.
BM25 is used rather than embeddings because insurance claims hinge on exact terms and
numbers ("air ambulance", "2,50,000") and the corpus is small (a few hundred chunks).
"""
import math
import re
from collections import Counter
from functools import lru_cache

from src import config, ingest

K1, B = 1.5, 0.75
STOPWORDS = set("a an and are as at be by for from in is it of on or the to up with your you this that".split())


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("`", "₹")
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 2,50,000 -> 250000 so numbers match as one token
    return [t for t in re.findall(r"[a-z]+|\d+(?:\.\d+)?|₹", text) if t not in STOPWORDS]


class BM25Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.docs = [tokenize(c["text"]) for c in chunks]
        self.lengths = [len(d) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0
        self.freqs = [Counter(d) for d in self.docs]
        doc_freq = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in doc_freq.items()}

    def search(self, query: str, policies: list[str] | None = None, k: int = 3) -> list[dict]:
        terms = tokenize(query)
        scored = []
        for i, chunk in enumerate(self.chunks):
            if policies and chunk["policy"] not in policies:
                continue
            f, length, score = self.freqs[i], self.lengths[i], 0.0
            for t in terms:
                if t in f:
                    tf = f[t]
                    score += self.idf.get(t, 0) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / (self.avg_len or 1)))
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        return [{**c, "score": round(s, 2)} for s, c in scored[:k]]


@lru_cache(maxsize=1)
def _index(stamp: float) -> BM25Index:
    return BM25Index(ingest.load_chunks())


def search(query: str, policies: list[str] | None = None, k: int = 3) -> list[dict]:
    """Top-k brochure chunks for the query (index rebuilt automatically if chunks.json changes)."""
    stamp = config.CHUNKS_FILE.stat().st_mtime if config.CHUNKS_FILE.exists() else 0.0
    return _index(stamp).search(query, policies, k)
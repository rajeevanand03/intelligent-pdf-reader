"""Local semantic embeddings (Sentence Transformers) with a persistent SQLite cache."""
import hashlib
import logging
import os
import sqlite3
import threading

import numpy as np

log = logging.getLogger(__name__)

_models = {}
_model_lock = threading.Lock()


def get_model(name):
    with _model_lock:
        if name not in _models:
            from sentence_transformers import SentenceTransformer

            log.info("Loading embedding model %s", name)
            _models[name] = SentenceTransformer(name, device="cpu")
        return _models[name]


class EmbeddingCache:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with sqlite3.connect(self.path) as con:
            con.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v BLOB)")

    def get_many(self, keys):
        out = {}
        with self._lock, sqlite3.connect(self.path) as con:
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                q = "SELECT k, v FROM emb WHERE k IN (%s)" % ",".join("?" * len(chunk))
                for k, v in con.execute(q, chunk):
                    out[k] = np.frombuffer(v, dtype=np.float32)
        return out

    def put_many(self, items):
        with self._lock, sqlite3.connect(self.path) as con:
            con.executemany("INSERT OR REPLACE INTO emb (k, v) VALUES (?, ?)",
                            [(k, v.astype(np.float32).tobytes()) for k, v in items])


class SemanticEncoder:
    def __init__(self, model_name, cache_dir=None, batch_size=64):
        self.model_name = model_name
        self.batch_size = batch_size
        self.cache = EmbeddingCache(os.path.join(cache_dir, "embeddings.sqlite")) if cache_dir else None

    def _key(self, text):
        return hashlib.sha1((self.model_name + "\x00" + text).encode("utf-8")).hexdigest()

    def encode(self, texts, progress=None):
        """Return L2-normalised embeddings (n, d) for ``texts``."""
        texts = [t if t and t.strip() else "(empty)" for t in texts]
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        keys = [self._key(t) for t in texts]
        cached = self.cache.get_many(list(set(keys))) if self.cache else {}
        missing = sorted({k: t for k, t in zip(keys, texts) if k not in cached}.items())
        if missing:
            model = get_model(self.model_name)
            new_vecs = []
            for i in range(0, len(missing), self.batch_size):
                batch = [t for _, t in missing[i:i + self.batch_size]]
                new_vecs.append(model.encode(batch, batch_size=self.batch_size, normalize_embeddings=True,
                                             show_progress_bar=False, convert_to_numpy=True))
                if progress:
                    progress(min(i + self.batch_size, len(missing)), len(missing))
            vecs = np.vstack(new_vecs).astype(np.float32)
            for (k, _), v in zip(missing, vecs):
                cached[k] = v
            if self.cache:
                self.cache.put_many([(k, cached[k]) for k, _ in missing])
        return np.vstack([cached[k] for k in keys])


def cosine_matrix(a, b):
    """Cosine similarity for normalised vectors."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    return np.clip(a @ b.T, -1.0, 1.0)

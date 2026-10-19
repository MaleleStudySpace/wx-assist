"""Regression coverage for persisted RAG failures and restart safety."""

import copy
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("chromadb")

from src.assistant.rag import vector_store
from src.assistant.rag.chunking import SlidingWindowChunker
from src.assistant.rag.embedder import FastEmbedder
from src.assistant.rag.engine import RAGEngine
from src.assistant.rag.models import Chunk
from src.assistant.rag.vector_store import ChromaStore
from src.db.content_cache import ContentCache


def stalled_worker(connection, path):
    connection.send((True, 0))
    connection.recv()
    time.sleep(60)


def failed_startup_worker(connection, path):
    time.sleep(60)


def crashed_worker(connection, path):
    import os
    connection.send((True, 0))
    connection.recv()
    os._exit(1)


@pytest.mark.parametrize("worker", [stalled_worker, crashed_worker])
def test_worker_failure_does_not_block_parent_or_repeat_timeout(tmp_path, monkeypatch, worker):
    monkeypatch.setattr(vector_store, "run_chroma_worker", worker)
    store = ChromaStore(str(tmp_path), timeout=10)
    ticks = []
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(0.02):
            ticks.append(time.monotonic())

    thread = threading.Thread(target=heartbeat)
    thread.start()
    try:
        store.warmup()
        store._timeout = 0.2
        start = time.monotonic()
        with pytest.raises(RuntimeError, match="RAG"):
            store.count()
        assert time.monotonic() - start < 5
        assert ticks
        assert store._process is None
        start = time.monotonic()
        with pytest.raises(RuntimeError, match="RAG"):
            store.count()
        assert time.monotonic() - start < 0.1
    finally:
        store.close()
        stop.set()
        thread.join(timeout=1)


def test_startup_timeout_reaps_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_store, "run_chroma_worker", failed_startup_worker)
    store = ChromaStore(str(tmp_path), timeout=0.2)
    with pytest.raises(RuntimeError, match="超过"):
        store.warmup()
    assert store._process is None
    store.close()


def test_vectors_survive_restart_and_upsert_updates_content(tmp_path):
    chunk = Chunk("id", "msg", "1", "chat", "sender", "old", "1")
    store = ChromaStore(str(tmp_path))
    try:
        store.warmup()
        store.add([chunk], np.array([[1.0, 0.0]], dtype=np.float32))
        chunk.content = "updated"
        store.add([chunk], np.array([[1.0, 0.0]], dtype=np.float32))
        assert store.count() == 1
    finally:
        store.close()
    reopened = ChromaStore(str(tmp_path))
    try:
        reopened.warmup()
        results = reopened.search(np.array([1.0, 0.0]), top_k=1, where={"source": "msg"})
        assert results[0].chunk.content == "updated"
        assert results[0].score == pytest.approx(1)
        assert reopened.delete_older_than(2) == 1
        assert reopened.count() == 0
    finally:
        reopened.close()


def test_failed_cold_start_does_not_checkpoint(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE messages(chat_id, sender_name, content, created_at)")
    connection.executemany("INSERT INTO messages VALUES (?, ?, ?, ?)", [
        ("chat", "sender", "message", int(time.time())) for _ in range(3)
    ])
    store = Mock()
    store.add.side_effect = RuntimeError("write failed")
    embedder = Mock()
    embedder.encode.return_value = np.ones((1, 2))
    engine = RAGEngine(store, embedder)
    engine._state_path = tmp_path / "state.json"
    engine.cold_start(connection)
    assert engine._last_indexed_ids == {}
    assert not engine._state_path.exists()
    connection.close()


def test_cache_failure_keeps_cursor_and_retries_successfully(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = ContentCache(str(tmp_path / "messages.db"))
    cache.query = Mock(return_value=[{
        "post_id": "1", "clean_content": "hello", "nickname": "name",
        "create_time": 1, "cached_at": 100,
    }])
    rag = SimpleNamespace(_embedder=Mock(), _store=Mock())
    rag._store.add.side_effect = RuntimeError("write failed")
    cache.index_to_rag(rag, "sns")
    assert cache._last_indexed_at["sns"] == 0
    rag._store.add.side_effect = None
    cache.index_to_rag(rag, "sns")
    assert cache._last_indexed_at["sns"] == 100


def test_chunk_identity_is_stable_across_retries_and_separates_chats():
    messages = [{"id": i, "chat_id": "chat", "content": "hello"} for i in range(3)]
    chunker = SlidingWindowChunker()
    first = chunker.chunk(messages, "msg")
    retry = chunker.chunk(copy.deepcopy(messages), "msg")
    assert [c.id for c in first] == [c.id for c in retry]
    messages[0]["id"] = 99
    assert chunker.chunk(messages, "msg")[0].id != first[0].id


def test_embedding_calls_are_serialized_and_keep_output_order():
    class Model:
        active = 0
        max_active = 0

        def embed(self, texts, batch_size):
            assert batch_size <= 16
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.02)
                for text in texts:
                    yield [float(text), 0.0]
            finally:
                self.active -= 1

    model = Model()
    embedder = FastEmbedder()
    embedder._model = model
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(embedder.encode, [["1", "2"], ["3"], ["4", "5"]]))
    assert model.max_active == 1
    assert [result[:, 0].tolist() for result in results] == [[1, 2], [3], [4, 5]]

"""Offline RAG rebuild; stop the desktop application before running this script.

Usage: python scripts/rebuild_rag.py --data-dir D:\\wx-assist\\data
Models must already be available locally. No AI or messaging services are used.
"""

import argparse
from datetime import datetime
import json
import logging
import multiprocessing
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def rebuild(data_dir: Path) -> Path:
    from src.assistant.rag import ChromaStore, FastEmbedder, RAGEngine
    from src.assistant.config import _dict_to_config
    from src.db.content_cache import ContentCache
    import psutil

    data_dir = data_dir.resolve()
    for process in psutil.process_iter(["name", "exe"]):
        executable = process.info.get("exe")
        if executable and (process.info.get("name") or "").lower().startswith("wx-assist"):
            if Path(executable).resolve().parent == data_dir.parent:
                raise RuntimeError("请先退出该目录中的微信助手，再重建索引")
    source = data_dir / "messages.db"
    if not source.is_file():
        raise FileNotFoundError(source)
    staging = data_dir / ("rag-rebuild-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    staging.mkdir()
    logging.info("Rebuilding into %s; active index is untouched until verification", staging)

    # SQLite backup includes committed WAL pages and gives all indexers a
    # consistent snapshot. Never connect Chroma to the damaged original here.
    snapshot = staging / "messages.db"
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(snapshot) as copy:
            original.backup(copy)
    original.close()
    copy.close()

    class RebuildCache(ContentCache):
        def _index_cursor_path(self):
            return str(staging / "last_indexed.json")

    config_path = data_dir / "assistant_config.json"
    config = _dict_to_config(json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {})
    embedder = FastEmbedder(device=config.rag_device, batch_size=config.rag_batch_size)
    embedder.warmup()
    store = ChromaStore(str(staging / "chroma"))
    engine = RAGEngine(store, embedder)
    engine._state_path = staging / "rag_state.json"
    try:
        store.warmup()
        connection = sqlite3.connect(snapshot)
        connection.row_factory = sqlite3.Row
        try:
            if not engine.cold_start(connection):
                raise RuntimeError("Message indexing failed; original index was not changed")
        finally:
            connection.close()
        engine._save_state()
        cache = RebuildCache(str(snapshot))
        # These methods propagate write errors; the normal periodic wrapper
        # intentionally logs and swallows them, which is unsuitable for repair.
        cache._index_oa(engine)
        cache._index_sns(engine)
        cache._index_fav(engine)
        cache._save_index_cursor()
        count = store.count()
    finally:
        store.close()

    # Reopen in another process: the user's failure first appeared on restart.
    reopened = ChromaStore(str(staging / "chroma"))
    try:
        reopened.warmup()
        if reopened.count() != count:
            raise RuntimeError("Reopened index count changed; original index was not changed")
    finally:
        reopened.close()

    # Keep all previous derived state together for rollback. Never merge HNSW
    # files from different indexes or clear the user's messages.db/configuration.
    previous = staging / "previous"
    previous.mkdir()
    names = ("chroma", "rag_state.json", "last_indexed.json")
    installed, archived = [], []
    try:
        for name in names:
            old = data_dir / name
            if old.exists():
                old.rename(previous / name)
                archived.append(name)
        for name in names:
            (staging / name).rename(data_dir / name)
            installed.append(name)
    except Exception:
        for name in reversed(installed):
            (data_dir / name).rename(staging / name)
        for name in reversed(archived):
            (previous / name).rename(data_dir / name)
        raise
    (staging / "result.json").write_text(
        json.dumps({"vectors": count, "data_dir": str(data_dir)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logging.info("Rebuilt and verified %d vectors. Previous index: %s", count, previous)
    return staging


if __name__ == "__main__":
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rebuild(args.data_dir)

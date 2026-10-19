"""Offline smoke check shared by source and packaged desktop builds."""

import json
from pathlib import Path


def check(output_dir, existing_index=None):
    from .embedder import FastEmbedder
    from .models import Chunk
    from .vector_store import ChromaStore
    from .. import rag_status

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {"ok": False}
    index = Path(existing_index).resolve() if existing_index else output / "check-index"
    store = ChromaStore(str(index))
    try:
        if existing_index and not (index / "chroma.sqlite3").is_file():
            raise FileNotFoundError(f"Existing index not found: {index}")
        embedder = FastEmbedder(batch_size=8)
        embedder.warmup()
        vectors = embedder.encode(["离线语义索引测试"])
        chunk = Chunk("check", "diagnostic", "check", "", "", "离线语义索引测试", "1")
        store.warmup()
        if not existing_index:
            # Force disk-backed HNSW creation, not just a tiny WAL buffer.
            import numpy as np
            chunks = [Chunk(f"check-{i}", chunk.source, str(i), "", "", chunk.content, "1")
                      for i in range(1100)]
            store.add(chunks, np.repeat(vectors, len(chunks), axis=0))
            if not list(index.glob("*/header.bin")):
                raise RuntimeError("Self-check did not create a persisted HNSW segment")
        count_before = store.count()
        store.close()
        # Reopen to exercise persisted HNSW and Windows spawn in the frozen EXE.
        store = ChromaStore(str(index))
        store.warmup()
        matches = store.search(vectors[0], 1)
        result.update(
            ok=bool(matches) and store.count() == count_before,
            count=store.count(), index=str(index), existing_index=bool(existing_index),
            hardware=rag_status.snapshot()["hardware"],
        )
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        store.close()
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1

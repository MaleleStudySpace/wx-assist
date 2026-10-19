"""Offline smoke check shared by source and packaged desktop builds."""

import json
from pathlib import Path


def check(output_dir):
    from .embedder import FastEmbedder
    from .models import Chunk
    from .vector_store import ChromaStore
    from .. import rag_status

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {"ok": False}
    store = ChromaStore(str(output / "check-index"))
    try:
        embedder = FastEmbedder(batch_size=8)
        embedder.warmup()
        vectors = embedder.encode(["离线语义索引测试"])
        chunk = Chunk("check", "diagnostic", "check", "", "", "离线语义索引测试", "1")
        store.warmup()
        store.add([chunk], vectors)
        store.close()
        # Reopen to exercise persisted HNSW and Windows spawn in the frozen EXE.
        store = ChromaStore(str(output / "check-index"))
        store.warmup()
        matches = store.search(vectors[0], 1)
        result.update(
            ok=bool(matches and matches[0].chunk.id == "check"),
            count=store.count(), hardware=rag_status.snapshot()["hardware"],
        )
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        store.close()
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1

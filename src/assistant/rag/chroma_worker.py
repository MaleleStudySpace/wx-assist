"""Chroma runs here so a native call holding the GIL cannot freeze the UI."""


def run_chroma_worker(connection, path):
    client = None
    try:
        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=path, settings=Settings(anonymized_telemetry=False),
        )
        collection = client.get_or_create_collection(
            name="rag", metadata={"hnsw:space": "cosine"},
            embedding_function=None,
        )
        # Opening the client alone does not load/recover the persisted HNSW
        # index. Both count and query have hung on damaged real-world indexes.
        count = collection.count()
        if count:
            sample = collection.get(limit=1, include=["embeddings"])
            collection.query(
                query_embeddings=sample["embeddings"].tolist(),
                n_results=1, include=["distances"],
            )
        connection.send((True, count))

        while True:
            command, payload = connection.recv()
            try:
                if command == "close":
                    # PersistentClient has no public close() in Chroma 1.5.9.
                    # This process owns its system; stopping it cannot affect
                    # another store in the application.
                    client._system.stop()
                    client = None
                    connection.send((True, None))
                    return
                if command == "upsert":
                    result = collection.upsert(**payload)
                elif command == "query":
                    result = collection.query(**payload)
                elif command == "delete":
                    result = collection.delete(**payload)
                elif command == "count":
                    result = collection.count()
                elif command == "delete_older_than":
                    result = 0
                    while True:
                        ids = collection.get(
                            where={"created_at_ts": {"$lt": payload}},
                            limit=1000, include=[],
                        )["ids"]
                        if not ids:
                            break
                        collection.delete(ids=ids)
                        result += len(ids)
                else:
                    raise ValueError(f"Unknown Chroma operation: {command}")
                connection.send((True, result))
            except Exception as exc:
                connection.send((False, f"{type(exc).__name__}: {exc}"))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            connection.send((False, f"{type(exc).__name__}: {exc}"))
        except (EOFError, OSError):
            pass
    finally:
        if client is not None:
            client._system.stop()
        connection.close()

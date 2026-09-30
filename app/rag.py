"""Documentation search by meaning (RAG) with a ChromaDB vector database.

RAG = Retrieval-Augmented Generation: first *retrieve* the few pieces of text
that matter for a question, then give only those to the model. The docs never
fill the prompt; the model pulls in what it needs, when it needs it.

How it works:
1. Split every file in the docs/ folder into chunks of CHUNK_LINES lines.
2. Embed each chunk: an embedding model turns text into a list of 1536 numbers
   that describes its *meaning*. Texts with similar meaning get similar numbers,
   e.g. "sign in" and "log in" end up close together.
3. Store chunks + numbers in ChromaDB, a database made for finding close number lists.
4. To search, embed the question the same way and ask ChromaDB for the closest chunks.

Safety (which files may be read) is decided in tools.py, not here.
"""

import hashlib
from pathlib import Path

import chromadb
from openai import OpenAI

from app import config

CHUNK_LINES = 40  # lines per chunk: small enough to be precise, big enough for context
MAX_RESULTS = 5  # chunks returned per search
EMBED_BATCH = 100  # chunks sent to the embedding model per request


def embed(texts: list[str]) -> list[list[float]]:
    """Turn each text into its embedding (a list of numbers) using OpenRouter."""
    client = OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.OPENROUTER_BASE_URL)
    vectors = []
    for start in range(0, len(texts), EMBED_BATCH):
        response = client.embeddings.create(
            model=config.EMBEDDING_MODEL, input=texts[start : start + EMBED_BATCH]
        )
        vectors += [item.embedding for item in response.data]
    return vectors


def get_collection(repo: Path):
    """Open this repository's collection (like a table) in the ChromaDB database."""
    database = chromadb.PersistentClient(
        path=config.VECTOR_DB,
        settings=chromadb.Settings(anonymized_telemetry=False),  # send no usage data anywhere
    )
    # Collection names must be short and simple, so we make one from the folder path.
    name = "docs_" + hashlib.md5(str(repo).encode("utf-8")).hexdigest()
    return database.get_or_create_collection(
        name,
        embedding_function=None,  # we make the embeddings ourselves with embed()
        metadata={"hnsw:space": "cosine"},  # measure closeness by angle (cosine distance)
    )


def split_into_chunks(text: str) -> list[tuple[int, str]]:
    """Split text into (first_line_number, chunk_text) pieces of CHUNK_LINES lines."""
    lines = text.splitlines()
    chunks = []
    for start in range(0, len(lines), CHUNK_LINES):
        chunk = "\n".join(lines[start : start + CHUNK_LINES]).strip()
        if chunk:
            chunks.append((start + 1, chunk))
    return chunks


def update_index(repo: Path, files: list[str], collection) -> None:
    """Make the database match the docs: remove deleted/changed files, add new/changed ones.

    A file's "last modified" time (mtime) tells us whether it changed, so
    unchanged files are never embedded twice (embedding costs money).
    """
    stored = collection.get(include=["metadatas"])["metadatas"]
    stored_times = {metadata["path"]: metadata["mtime"] for metadata in stored}
    current_times = {path: (repo / path).stat().st_mtime for path in files}

    # 1. Remove files that were deleted or changed since we indexed them.
    for path, mtime in stored_times.items():
        if current_times.get(path) != mtime:
            collection.delete(where={"path": path})

    # 2. Add files that are new or changed.
    for path, mtime in current_times.items():
        if stored_times.get(path) == mtime:
            continue  # already indexed and unchanged
        try:
            text = (repo / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # not a text file (e.g. an image)
        chunks = split_into_chunks(text)
        if not chunks:
            continue
        collection.add(
            ids=[f"{path}:{line}" for line, _ in chunks],
            documents=[chunk for _, chunk in chunks],
            embeddings=embed([chunk for _, chunk in chunks]),
            metadatas=[{"path": path, "line": line, "mtime": mtime} for line, _ in chunks],
        )


def search(repo: Path, files: list[str], query: str) -> str:
    """Return the doc chunks closest in meaning to `query`, best match first."""
    collection = get_collection(repo)
    update_index(repo, files, collection)
    if collection.count() == 0:
        return f"No readable documents found in {config.DOCS_FOLDER}/"

    results = collection.query(
        query_embeddings=embed([query]),
        n_results=min(MAX_RESULTS, collection.count()),
    )
    # ChromaDB returns one list per question; we asked one question, so take item [0].
    parts = []
    for text, metadata, distance in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        similarity = 1 - distance  # 1.0 = same meaning, 0 = unrelated
        header = f"{metadata['path']} (from line {metadata['line']}, match {similarity:.2f}):"
        parts.append(f"{header}\n{text}")
    return "\n\n".join(parts)

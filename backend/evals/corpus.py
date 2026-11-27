"""The fixture corpus for `rag.golden` (`evals/fixtures/docs/`, all content fictional).

Sources are readable text files; real PDF and DOCX bytes are built from them when needed
(`evals.filebuild`), so the repo holds no binary fixtures. `manifest.json` gives the upload order,
oldest first: the last file is the newest, which is what "the document I just uploaded" means.
"""

import contextlib
import json
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.core.config import Settings
from app.graph.state import RecentFile
from app.llm.factory import build_embeddings_client
from app.rag import formats
from app.rag.chunker import Chunk, chunk_document
from app.rag.embedder import Embedder
from app.rag.index import QdrantIndex
from app.rag.parsers import parse
from app.rag.retriever import Retriever
from app.services.files import RECENT_FILES
from qdrant_client import AsyncQdrantClient

from evals.datasets import REPO_ROOT
from evals.filebuild import docx_bytes, pdf_bytes

CORPUS_DIR = REPO_ROOT / "evals" / "fixtures" / "docs"
_PAGE_BREAK = "---page---"
_FIRST_UPLOAD = date(2026, 11, 1)  # the corpus is "uploaded" one day apart, oldest first


@dataclass(frozen=True)
class CorpusEntry:
    name: str
    format: str
    source: Path


def load_manifest(directory: Path | None = None) -> list[CorpusEntry]:
    base = directory or CORPUS_DIR
    data = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    return [CorpusEntry(f["name"], f["format"], base / f["source"]) for f in data["files"]]


def mime_of(entry: CorpusEntry) -> str:
    return formats.EXTENSIONS[entry.format]


def _bytes_of(entry: CorpusEntry) -> bytes:
    text = entry.source.read_text(encoding="utf-8")
    if entry.format == "pdf":
        return pdf_bytes([page.strip() for page in text.split(_PAGE_BREAK)])
    if entry.format == "docx":
        return docx_bytes([p.strip() for p in text.split("\n\n") if p.strip()])
    return text.encode("utf-8")


def write_file(entry: CorpusEntry, directory: Path) -> Path:
    """Build the real file for `entry` under `directory` and return its path."""
    path = directory / entry.name
    path.write_bytes(_bytes_of(entry))
    return path


def chunks_of(path: Path, mime: str) -> list[Chunk]:
    """Parse and chunk a file exactly as the ingestion job does."""
    return chunk_document(parse(path, mime))


class RagSetupError(Exception):
    """The vector store or embeddings were not reachable, so the RAG step cannot run."""


@dataclass(frozen=True)
class IngestedFile:
    id: uuid.UUID
    name: str
    uploaded_at: str
    chunks: int


@dataclass
class RagEnv:
    """The corpus, ingested for one throwaway user into a throwaway collection."""

    user_id: uuid.UUID
    index: QdrantIndex
    retriever: Retriever
    files: list[IngestedFile]
    embedding_tokens: int = 0

    async def recent_files(self, user_id: uuid.UUID) -> list[RecentFile]:
        """The `load_context` lookup: the newest files first (the last manifest entry is newest)."""
        if user_id != self.user_id:
            return []
        newest = list(reversed(self.files))[:RECENT_FILES]
        return [{"id": str(f.id), "filename": f.name, "uploaded_at": f.uploaded_at} for f in newest]

    async def close(self) -> None:
        try:
            await self.index.client.delete_collection(self.index.collection)
        finally:
            await self.index.client.close()


async def ingest_corpus(
    settings: Settings, *, directory: Path | None = None, workdir: Path | None = None
) -> RagEnv:
    """Build, parse, chunk, embed and index every corpus file, as the ingestion job does (same
    parser, chunker, embedder and index code). Needs Qdrant; embeddings come from the factory, so
    `LLM_MODE=fake` works offline and a real run costs a fraction of a cent."""
    client = AsyncQdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value() or None
    )
    index = QdrantIndex(client, alias=f"eval_documents_{uuid.uuid4().hex[:12]}")
    embedder = Embedder(build_embeddings_client(settings), settings.llm_embedding_model)
    user_id = uuid.uuid4()
    files: list[IngestedFile] = []
    tokens = 0
    try:
        await index.ensure_collection()
        with tempfile.TemporaryDirectory() as tmp:
            for position, entry in enumerate(load_manifest(directory)):
                path = write_file(entry, workdir or Path(tmp))
                chunks = chunks_of(path, mime_of(entry))
                embedded = await embedder.embed([c.text for c in chunks])
                tokens += embedded.tokens
                file_id = uuid.uuid4()
                await index.upsert(
                    user_id=user_id,
                    file_id=file_id,
                    filename=entry.name,
                    chunks=chunks,
                    vectors=embedded.vectors,
                )
                day = _FIRST_UPLOAD + timedelta(days=position)
                files.append(IngestedFile(file_id, entry.name, day.isoformat(), len(chunks)))
    except Exception as exc:
        with contextlib.suppress(Exception):  # best effort: the original failure is what matters
            await client.delete_collection(index.collection)
        await client.close()
        raise RagSetupError(f"could not ingest the corpus: {type(exc).__name__}") from exc
    return RagEnv(
        user_id=user_id,
        index=index,
        retriever=Retriever(embedder, index),
        files=files,
        embedding_tokens=tokens,
    )

from __future__ import annotations

import hashlib
import json
import os
import re
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import chromadb
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer


CHUNK_WORDS = 300
CHUNK_OVERLAP = 50
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
PERSIST_DIRECTORY = os.environ.get("CHROMA_PERSIST_DIRECTORY", "./chroma_db")
CHROMA_HOST = os.environ.get("CHROMA_HOST")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
CHROMA_SSL = os.environ.get("CHROMA_SSL", "false").lower() == "true"
REGISTRY_PATH = Path(PERSIST_DIRECTORY) / "documents.json"


def extract_pages(filename: str, content: bytes) -> list[tuple[str, int | None]]:
    """Extract text while retaining PDF page numbers."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        text = content.decode("utf-8", errors="replace").strip()
        return [(text, None)] if text else []
    if suffix != ".pdf":
        raise ValueError("Only PDF and TXT files are supported.")

    pages: list[tuple[str, int | None]] = []
    for page_number, page in enumerate(PdfReader(BytesIO(content)).pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((text, page_number))
    return pages


def chunk_text(text: str, chunk_size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = chunk_size - overlap
    return [" ".join(words[start : start + chunk_size]) for start in range(0, len(words), step)]


def _safe_filename(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "document"


class DocumentRegistry:
    def __init__(self, path: Path = REGISTRY_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.documents: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(self.documents, indent=2), encoding="utf-8")
        temporary_path.replace(self.path)

    def add(self, document: dict[str, Any]) -> None:
        self.documents[document["id"]] = document
        self._save()

    def remove(self, document_id: str) -> bool:
        removed = self.documents.pop(document_id, None) is not None
        if removed:
            self._save()
        return removed

    def all(self) -> list[dict[str, Any]]:
        return sorted(self.documents.values(), key=lambda item: item["uploaded_at"], reverse=True)


class ChromaDocumentStore:
    def __init__(self, embedding_model: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self.embedding_model = embedding_model
        self.embedder = SentenceTransformer(embedding_model)
        self.client = (
            chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT, ssl=CHROMA_SSL)
            if CHROMA_HOST
            else chromadb.PersistentClient(path=PERSIST_DIRECTORY)
        )
        self.collection = self.client.get_or_create_collection(
            name="uploaded_documents",
            metadata={"hnsw:space": "cosine"},
        )
        self.registry = DocumentRegistry()

    def add_document(self, filename: str, content: bytes) -> dict[str, Any]:
        if not content:
            raise ValueError("The uploaded file is empty.")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".pdf", ".txt"}:
            raise ValueError("Only PDF and TXT files are supported.")

        pages = extract_pages(filename, content)
        if not pages:
            raise ValueError("The document contains no extractable text.")

        document_id = f"doc_{uuid4().hex}"
        records: list[tuple[str, str | None, int]] = []
        for text, page in pages:
            for chunk in chunk_text(text):
                records.append((chunk, page, len(records)))
        if not records:
            raise ValueError("The document contains no chunkable text.")

        chunk_ids = [f"{document_id}_chunk_{index:05d}" for _, _, index in records]
        embeddings = self.embedder.encode(
            [text for text, _, _ in records],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        metadatas = [
            {
                "document_id": document_id,
                "filename": _safe_filename(filename),
                "file_type": suffix[1:],
                "page": page if page is not None else -1,
                "chunk_id": chunk_id,
                "source": "uploaded_document",
            }
            for (_, page, _), chunk_id in zip(records, chunk_ids)
        ]
        self.collection.add(
            ids=chunk_ids,
            documents=[text for text, _, _ in records],
            embeddings=embeddings.tolist(),
            metadatas=metadatas,
        )

        document = {
            "id": document_id,
            "filename": _safe_filename(filename),
            "file_type": suffix[1:],
            "chunks": len(records),
            "status": "processed",
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": hashlib.sha256(content).hexdigest(),
        }
        self.registry.add(document)
        return document

    def retrieve(self, question: str, top_k: int = 3) -> list[dict[str, Any]]:
        if not question.strip() or self.collection.count() == 0:
            return []
        vector = self.embedder.encode([question], normalize_embeddings=True)
        result = self.collection.query(
            query_embeddings=vector.tolist(),
            n_results=min(top_k, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            {
                "text": text,
                "metadata": {
                    **metadata,
                    "page": None if metadata.get("page") == -1 else metadata.get("page"),
                },
                "score": 1.0 - float(distance),
            }
            for text, metadata, distance in zip(documents, metadatas, distances)
        ]

    def delete_document(self, document_id: str) -> bool:
        if not self.registry.remove(document_id):
            return False
        self.collection.delete(where={"document_id": document_id})
        return True

    def documents(self) -> list[dict[str, Any]]:
        return self.registry.all()


def build_context(retrieved: Iterable[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[Source {index}]\n{item['text']}"
        for index, item in enumerate(retrieved, 1)
    )

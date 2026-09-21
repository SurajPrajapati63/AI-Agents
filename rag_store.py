from __future__ import annotations

import hashlib
import json
import os
import re
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer


CHUNK_WORDS = 300
CHUNK_OVERLAP = 50
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
VECTOR_STORE_DIRECTORY = Path(os.environ.get("VECTOR_STORE_DIRECTORY", "./vector_store"))
RECORDS_PATH = VECTOR_STORE_DIRECTORY / "records.json"
EMBEDDINGS_PATH = VECTOR_STORE_DIRECTORY / "embeddings.npy"


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
    def __init__(self, path: Path = RECORDS_PATH) -> None:
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


class VectorDocumentStore:
    def __init__(self, embedding_model: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self.embedding_model = embedding_model
        self.embedder = SentenceTransformer(embedding_model)
        self.registry = DocumentRegistry()
        self.records = self._load_records()
        self.embeddings = self._load_embeddings()

    def _load_records(self) -> list[dict[str, Any]]:
        if not RECORDS_PATH.exists():
            return []
        try:
            return json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _load_embeddings(self) -> np.ndarray:
        if not EMBEDDINGS_PATH.exists():
            return np.empty((0, 0), dtype=np.float32)
        try:
            return np.load(EMBEDDINGS_PATH, allow_pickle=False).astype(np.float32)
        except (OSError, ValueError):
            return np.empty((0, 0), dtype=np.float32)

    def _save(self) -> None:
        VECTOR_STORE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        records_tmp = RECORDS_PATH.with_suffix(".tmp")
        records_tmp.write_text(json.dumps(self.records, indent=2), encoding="utf-8")
        records_tmp.replace(RECORDS_PATH)
        embeddings_tmp = EMBEDDINGS_PATH.with_suffix(".tmp.npy")
        with embeddings_tmp.open("wb") as file:
            np.save(file, self.embeddings)
        embeddings_tmp.replace(EMBEDDINGS_PATH)

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
        self.records.extend(
            {"id": chunk_id, "text": text, "metadata": metadata}
            for chunk_id, (text, _, _), metadata in zip(chunk_ids, records, metadatas)
        )
        normalized_embeddings = np.asarray(embeddings, dtype=np.float32)
        self.embeddings = (
            normalized_embeddings
            if not self.embeddings.size
            else np.vstack((self.embeddings, normalized_embeddings))
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
        self._save()
        return document

    def retrieve(self, question: str, top_k: int = 3) -> list[dict[str, Any]]:
        if not question.strip() or not self.records:
            return []
        query_vector = np.asarray(
            self.embedder.encode([question], normalize_embeddings=True)[0],
            dtype=np.float32,
        )
        scores = self.embeddings @ query_vector
        best_indices = np.argsort(scores)[::-1][:top_k]
        return [
            {
                "text": self.records[index]["text"],
                "metadata": {
                    **self.records[index]["metadata"],
                    "page": self.records[index]["metadata"].get("page") or None,
                },
                "score": float(scores[index]),
            }
            for index in best_indices
        ]

    def delete_document(self, document_id: str) -> bool:
        if not self.registry.remove(document_id):
            return False
        keep = [record["metadata"]["document_id"] != document_id for record in self.records]
        self.records = [record for record, should_keep in zip(self.records, keep) if should_keep]
        self.embeddings = self.embeddings[np.asarray(keep, dtype=bool)]
        self._save()
        return True

    def documents(self) -> list[dict[str, Any]]:
        return self.registry.all()

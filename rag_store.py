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
from PyPDF2 import PdfReader


CHUNK_WORDS = 300
CHUNK_OVERLAP = 50
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
LOCAL_EMBEDDING_DIMENSIONS = 768
VECTOR_STORE_DIRECTORY = Path(os.environ.get("VECTOR_STORE_DIRECTORY", "./vector_store"))
DOCUMENTS_PATH = VECTOR_STORE_DIRECTORY / "documents.json"
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
    def __init__(self, path: Path = DOCUMENTS_PATH) -> None:
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
        self.registry: DocumentRegistry | None = None
        self.records: list[dict[str, Any]] | None = None
        self.embeddings: np.ndarray | None = None

    def _ensure_loaded(self) -> None:
        if self.registry is None:
            self.registry = DocumentRegistry()
        if self.records is None:
            self.records = self._load_records()
        if self.embeddings is None:
            self.embeddings = self._load_embeddings()
        # Keep saved chunk records and their vectors in sync. Older or
        # partially persisted stores can contain records with missing or
        # differently sized vectors, which otherwise crashes document Q&A at
        # the NumPy matrix multiplication step.
        if self.embeddings.shape != (len(self.records), LOCAL_EMBEDDING_DIMENSIONS):
            self.embeddings = self._embed([record["text"] for record in self.records])
            self._save()

    def _embed(self, texts: list[str]) -> np.ndarray:
        result = np.zeros((len(texts), LOCAL_EMBEDDING_DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = re.findall(r"[a-z0-9]+", text.lower())
            features = tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]
            for feature in features:
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "little") % LOCAL_EMBEDDING_DIMENSIONS
                sign = 1.0 if digest[4] & 1 else -1.0
                result[row, bucket] += sign
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        return result / np.maximum(norms, 1e-12)

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
        self._ensure_loaded()
        VECTOR_STORE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        records_tmp = RECORDS_PATH.with_suffix(".tmp")
        records_tmp.write_text(json.dumps(self.records, indent=2), encoding="utf-8")
        records_tmp.replace(RECORDS_PATH)
        embeddings_tmp = EMBEDDINGS_PATH.with_suffix(".tmp.npy")
        with embeddings_tmp.open("wb") as file:
            np.save(file, self.embeddings)
        embeddings_tmp.replace(EMBEDDINGS_PATH)

    def add_document(self, filename: str, content: bytes, owner_id: str) -> dict[str, Any]:
        self._ensure_loaded()
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
        embeddings = self._embed([text for text, _, _ in records])
        metadatas = [
            {
                "document_id": document_id,
                "owner_id": owner_id,
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
        self.embeddings = (
            embeddings
            if not self.embeddings.size
            else np.vstack((self.embeddings, embeddings))
        )

        document = {
            "id": document_id,
            "owner_id": owner_id,
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

    def retrieve(self, question: str, owner_id: str, top_k: int = 3) -> list[dict[str, Any]]:
        self._ensure_loaded()
        if not question.strip() or not self.records:
            return []
        owned_indices = [
            index for index, record in enumerate(self.records)
            if record["metadata"].get("owner_id") == owner_id
        ]
        owned_records = [self.records[index] for index in owned_indices]
        if not owned_records:
            return []
        query_vector = self._embed([question])[0]
        if self.embeddings.shape[1] != query_vector.shape[0]:
            raise ValueError(
                "Stored embeddings use a different embedding model. "
                "Delete vector_store/ and upload the documents again."
            )
        owned_embeddings = self.embeddings[np.asarray(owned_indices)]
        semantic_scores = owned_embeddings @ query_vector
        question_terms = set(re.findall(r"[a-z0-9]+", question.lower()))
        lexical_scores = np.asarray(
            [
                len(question_terms & set(re.findall(r"[a-z0-9]+", record["text"].lower())))
                / max(len(question_terms), 1)
                for record in owned_records
            ],
            dtype=np.float32,
        )
        scores = semantic_scores + lexical_scores
        best_indices = np.argsort(scores)[::-1][:top_k]
        return [
            {
                "text": owned_records[index]["text"],
                "metadata": {
                    **owned_records[index]["metadata"],
                    "page": owned_records[index]["metadata"].get("page") or None,
                },
                "score": float(scores[index]),
            }
            for index in best_indices
        ]

    def delete_document(self, document_id: str, owner_id: str) -> bool:
        self._ensure_loaded()
        document = self.registry.documents.get(document_id)
        if not document or document.get("owner_id") != owner_id:
            return False
        self.registry.remove(document_id)
        keep = [record["metadata"]["document_id"] != document_id for record in self.records]
        self.records = [record for record, should_keep in zip(self.records, keep) if should_keep]
        self.embeddings = self.embeddings[np.asarray(keep, dtype=bool)]
        self._save()
        return True

    def retrieve_all(self, question: str, owner_id: str) -> list[dict[str, Any]]:
        """Return every owned chunk, ordered by relevance, for document operations."""
        self._ensure_loaded()
        if not question.strip() or not self.records:
            return []
        owned_indices = [
            index for index, record in enumerate(self.records)
            if record["metadata"].get("owner_id") == owner_id
        ]
        if not owned_indices:
            return []
        query_vector = self._embed([question])[0]
        if self.embeddings.shape[1] != query_vector.shape[0]:
            raise ValueError(
                "Stored document vectors are incompatible. The vector store was rebuilt; "
                "please retry your question."
            )
        owned_embeddings = self.embeddings[np.asarray(owned_indices)]
        semantic_scores = owned_embeddings @ query_vector
        question_terms = set(re.findall(r"[a-z0-9]+", question.lower()))
        scores = semantic_scores + np.asarray(
            [
                len(question_terms & set(re.findall(r"[a-z0-9]+", self.records[index]["text"].lower())))
                / max(len(question_terms), 1)
                for index in owned_indices
            ],
            dtype=np.float32,
        )
        return [
            {
                "text": self.records[owned_indices[index]]["text"],
                "metadata": {
                    **self.records[owned_indices[index]]["metadata"],
                    "page": self.records[owned_indices[index]]["metadata"].get("page") or None,
                },
                "score": float(scores[index]),
            }
            for index in np.argsort(scores)[::-1]
        ]

    def documents(self, owner_id: str) -> list[dict[str, Any]]:
        self._ensure_loaded()
        return [
            document for document in self.registry.all()
            if document.get("owner_id") == owner_id
        ]

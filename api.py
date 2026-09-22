from __future__ import annotations

import os
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

from main import DEFAULT_LLM_MODEL, generate_answer
from rag_store import VectorDocumentStore


MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(10 * 1024 * 1024)))
ALLOWED_TYPES = {".pdf", ".txt"}

app = FastAPI(title="Document Q&A API", version="1.0.0")
allowed_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS"
    )
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allowed_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store: VectorDocumentStore | None = None


def get_store() -> VectorDocumentStore:
    global store
    if store is None:
        store = VectorDocumentStore()
    return store


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    chat_history: list[dict[str, str]] = Field(default_factory=list, max_length=50)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/upload")
async def upload_documents(files: Annotated[list[UploadFile], File(...)]) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF or TXT file is required.")

    processed = []
    try:
        for upload in files:
            filename = upload.filename or "document"
            suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if suffix not in ALLOWED_TYPES:
                raise HTTPException(status_code=415, detail=f"Unsupported file type: {filename}")
            content = await upload.read()
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail=f"{filename} exceeds the file size limit.")
            processed.append(get_store().add_document(filename, content))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"Upload failed: {error}") from error

    return {
        "success": True,
        "message": "Documents uploaded successfully",
        "documents": processed,
    }


@app.post("/ask")
def ask_question(request: AskRequest) -> dict:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    retrieved = get_store().retrieve(question)
    if not retrieved:
        return {
            "success": True,
            "question": question,
            "answer": "I couldn't find that information in the uploaded documents.",
            "sources": [],
        }

    try:
        answer = generate_answer(
            question,
            [item["text"] for item in retrieved],
            DEFAULT_LLM_MODEL,
            request.chat_history,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Answer generation failed: {error}") from error

    sources = []
    seen = set()
    for item in retrieved:
        metadata = item["metadata"]
        source_key = metadata["chunk_id"]
        if source_key in seen:
            continue
        seen.add(source_key)
        sources.append(
            {
                "document_id": metadata["document_id"],
                "filename": metadata["filename"],
                "page": metadata.get("page"),
                "chunk_id": metadata["chunk_id"],
            }
        )

    return {"success": True, "question": question, "answer": answer, "sources": sources}


@app.get("/documents")
def list_documents() -> dict:
    return {"success": True, "documents": get_store().documents()}


@app.delete("/documents/{document_id}")
def delete_document(document_id: str) -> dict[str, object]:
    if not get_store().delete_document(document_id):
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"success": True, "message": "Document deleted successfully"}

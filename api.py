from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

load_dotenv()

from auth import (
    CredentialsRequest,
    authenticate_user,
    create_user,
    database_error,
    get_bearer_token,
    get_current_user,
    get_database,
    revoke_session,
)

from main import DEFAULT_LLM_MODEL, generate_answer
from rag_store import VectorDocumentStore


MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", str(10 * 1024 * 1024)))
ALLOWED_TYPES = {".pdf", ".txt"}

app = FastAPI(title="Document Q&A API", version="1.0.0")
configured_origins = os.environ.get("CORS_ORIGINS", "")
allowed_origins = {
    origin.strip()
    for origin in configured_origins.split(",")
    if origin.strip()
}
allowed_origins.update(
    {
        "http://localhost:5173",
        "http://localhost:8501",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8501",
        "http://ai-agent-rag.vercel.app",
        "https://ai-agent-rag.vercel.app",
        "https://ai-agents-inky-two.vercel.app",
    }
)
allowed_origin_regex = os.environ.get(
    "CORS_ORIGIN_REGEX",
    r"https://ai-agents-[a-z0-9-]+-suraj-prajapatis-projects-b7c1f72a\.vercel\.app",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_origin_regex=allowed_origin_regex,
    allow_credentials=True,
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


class ConversationsRequest(BaseModel):
    conversations: list[dict] = Field(default_factory=list, max_length=100)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/auth/signup")
def signup(request: CredentialsRequest) -> dict:
    try:
        return create_user(get_database(), request)
    except PyMongoError as error:
        raise database_error(error) from error


@app.post("/auth/login")
def login(request: CredentialsRequest) -> dict:
    try:
        return authenticate_user(get_database(), request)
    except PyMongoError as error:
        raise database_error(error) from error


@app.get("/auth/me")
def current_user(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    return {"success": True, "user": user}


@app.post("/auth/logout")
def logout(token: Annotated[str, Depends(get_bearer_token)]) -> dict[str, bool]:
    try:
        revoke_session(token)
    except PyMongoError as error:
        raise database_error(error) from error
    return {"success": True}


@app.get("/conversations")
def list_conversations(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    try:
        conversation = get_database().conversations.find_one({"user_id": user["id"]})
        return {
            "success": True,
            "conversations": conversation.get("conversations", []) if conversation else [],
        }
    except PyMongoError as error:
        raise database_error(error) from error


@app.put("/conversations")
def save_conversations(
    request: ConversationsRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, bool]:
    try:
        get_database().conversations.update_one(
            {"user_id": user["id"]},
            {"$set": {"conversations": request.conversations}},
            upsert=True,
        )
        return {"success": True}
    except PyMongoError as error:
        raise database_error(error) from error


@app.post("/upload")
async def upload_documents(
    files: Annotated[list[UploadFile], File(...)],
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
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
            processed.append(get_store().add_document(filename, content, user["id"]))
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
def ask_question(
    request: AskRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    retrieved = get_store().retrieve(question, user["id"])
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
def list_documents(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    return {"success": True, "documents": get_store().documents(user["id"])}


@app.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, object]:
    if not get_store().delete_document(document_id, user["id"]):
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"success": True, "message": "Document deleted successfully"}

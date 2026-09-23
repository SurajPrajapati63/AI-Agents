from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from uuid import uuid4
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

from main import (
    DEFAULT_LLM_MODEL,
    GENERAL_RESPONSE,
    calculate_explicit_math,
    classify_question,
    generate_answer,
)
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


class NewConversationRequest(BaseModel):
    title: str = Field(default="New conversation", max_length=120)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


def _conversation_filter(user: dict, session_id: str) -> dict[str, str]:
    return {"user_id": user["id"], "session_id": session_id}


def _load_memories(database, user_id: str) -> list[str]:
    return [item["content"] for item in database.memories.find({"user_id": user_id}).sort("updated_at", -1).limit(20)]


def _remember_explicit_fact(database, user_id: str, content: str) -> None:
    patterns = (
        ("name", r"\bmy name is ([A-Za-z][A-Za-z .'-]{1,80})\b"),
        ("preference", r"\bi (?:prefer|like|love) ([^.!?\n]{2,120})"),
        ("interest", r"\bi(?: am|'m) interested in ([^.!?\n]{2,120})"),
    )
    now = datetime.now(timezone.utc)
    for kind, pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" .,!?")
        fact_key = f"{kind}:{value.lower()}"
        database.memories.update_one(
            {"user_id": user_id, "kind": kind, "fact_key": fact_key},
            {"$set": {"content": f"The user's {kind} is {value}.", "updated_at": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )


def _conversation_messages(database, user: dict, session_id: str) -> list[dict]:
    if not database.conversations.find_one(_conversation_filter(user, session_id)):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return list(database.messages.find(_conversation_filter(user, session_id), {"_id": 0}).sort("timestamp", 1))


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
        conversations = get_database().conversations.find(
            {"user_id": user["id"], "session_id": {"$exists": True}},
            {"_id": 0, "session_id": 1, "title": 1, "created_at": 1, "updated_at": 1},
        ).sort("updated_at", -1)
        return {
            "success": True,
            "conversations": list(conversations),
        }
    except PyMongoError as error:
        raise database_error(error) from error


@app.post("/conversations")
def create_conversation(
    request: NewConversationRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    session_id = f"chat_{uuid4().hex}"
    now = datetime.now(timezone.utc)
    try:
        get_database().conversations.insert_one({
            "user_id": user["id"],
            "session_id": session_id,
            "title": request.title.strip() or "New conversation",
            "created_at": now,
            "updated_at": now,
        })
        return {"success": True, "session_id": session_id, "title": request.title.strip() or "New conversation"}
    except PyMongoError as error:
        raise database_error(error) from error


@app.get("/conversations/{session_id}/messages")
def get_conversation_messages(
    session_id: str,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    try:
        return {"success": True, "session_id": session_id, "messages": _conversation_messages(get_database(), user, session_id)}
    except PyMongoError as error:
        raise database_error(error) from error


@app.delete("/conversations/{session_id}")
def delete_conversation(
    session_id: str,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, bool]:
    try:
        database = get_database()
        result = database.conversations.delete_one(_conversation_filter(user, session_id))
        if not result.deleted_count:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        database.messages.delete_many(_conversation_filter(user, session_id))
        return {"success": True}
    except PyMongoError as error:
        raise database_error(error) from error


@app.post("/conversations/{session_id}/messages")
def send_conversation_message(
    session_id: str,
    request: MessageRequest,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    question = request.content.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    try:
        database = get_database()
        messages = _conversation_messages(database, user, session_id)
        now = datetime.now(timezone.utc)
        database.messages.insert_one({
            "user_id": user["id"], "session_id": session_id, "role": "user",
            "content": question, "timestamp": now, "message_id": f"msg_{uuid4().hex}",
        })
        _remember_explicit_fact(database, user["id"], question)
        category = classify_question(question)
        if category == "GENERAL/META":
            answer = GENERAL_RESPONSE
            retrieved = []
        elif category == "CALCULATION":
            answer = calculate_explicit_math(question)
            retrieved = []
            if answer is None:
                answer = "I couldn't calculate that because the required values or rule were not provided."
        else:
            retrieved = (
                get_store().retrieve_all(question, user["id"])
                if category in {"COUNT", "AGGREGATION", "COMPARISON"}
                else get_store().retrieve(question, user["id"])
            )
        history = [{"role": item["role"], "content": item["content"]} for item in messages[-50:]]
        if category not in {"GENERAL/META", "CALCULATION"}:
            memories = _load_memories(database, user["id"])
            if retrieved:
                answer = generate_answer(
                    question,
                    [item["text"] for item in retrieved],
                    DEFAULT_LLM_MODEL,
                    history,
                    memories,
                    category,
                )
            elif history or memories:
                answer = generate_answer(
                    question,
                    [],
                    DEFAULT_LLM_MODEL,
                    history,
                    memories,
                    "CONVERSATION",
                )
            else:
                answer = "I couldn't find that information in the uploaded documents."
        database.messages.insert_one({
            "user_id": user["id"], "session_id": session_id, "role": "assistant",
            "content": answer, "timestamp": datetime.now(timezone.utc), "message_id": f"msg_{uuid4().hex}",
        })
        database.conversations.update_one(
            _conversation_filter(user, session_id),
            {"$set": {"updated_at": datetime.now(timezone.utc), "title": question[:120]}},
        )
        sources = []
        seen = set()
        for item in retrieved:
            metadata = item["metadata"]
            if metadata["chunk_id"] in seen:
                continue
            seen.add(metadata["chunk_id"])
            sources.append({
                "document_id": metadata["document_id"],
                "filename": metadata["filename"],
                "page": metadata.get("page"),
                "chunk_id": metadata["chunk_id"],
            })
        return {"success": True, "session_id": session_id, "answer": answer, "sources": sources}
    except HTTPException:
        raise
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

    category = classify_question(question)
    if category == "GENERAL/META":
        return {"success": True, "question": question, "answer": GENERAL_RESPONSE, "sources": []}
    if category == "CALCULATION":
        answer = calculate_explicit_math(question)
        return {
            "success": True,
            "question": question,
            "answer": answer or "I couldn't calculate that because the required values or rule were not provided.",
            "sources": [],
        }
    retrieved = (
        get_store().retrieve_all(question, user["id"])
        if category in {"COUNT", "AGGREGATION", "COMPARISON"}
        else get_store().retrieve(question, user["id"])
    )
    if not retrieved:
        if request.chat_history:
            try:
                answer = generate_answer(
                    question,
                    [],
                    DEFAULT_LLM_MODEL,
                    request.chat_history,
                    operation="CONVERSATION",
                )
            except Exception as error:
                raise HTTPException(status_code=502, detail=f"Answer generation failed: {error}") from error
            return {"success": True, "question": question, "answer": answer, "sources": []}
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
            operation=category,
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

from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Annotated, Any

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient
from pymongo.collation import Collation
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

from memory_store import ensure_memory_schema


MONGO_SCHEMES = ("mongodb://", "mongodb+srv://")


def _resolve_mongodb_uri() -> str:
    """Read the connection string from MONGODB_URI or the MONGO_URI alias."""
    for key in ("MONGODB_URI", "MONGO_URI"):
        value = os.environ.get(key, "").strip().strip('"').strip("'").strip()
        if not value:
            continue
        if value.startswith(f"{key}="):
            value = value[len(key) + 1 :].strip().strip('"').strip("'").strip()
        if not value.startswith(MONGO_SCHEMES):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Environment variable {key} is not a valid MongoDB connection "
                    "string: it must start with 'mongodb://' or 'mongodb+srv://' "
                    "(lowercase, no surrounding quotes, spaces, or line breaks). "
                    "Fix the value in Render's Environment settings and redeploy."
                ),
            )
        return value
    return "mongodb://localhost:27017"


MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "sourcewise")
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "24"))
BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_bearer = HTTPBearer(auto_error=False)
_client: MongoClient | None = None
_indexes_ready = False
_indexes_lock = Lock()


class CredentialsRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(email):
            raise ValueError("Enter a valid email address.")
        return email

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer.")
        return value


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            _resolve_mongodb_uri(),
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            appname="sourcewise",
        )
    return _client


def get_database() -> Database:
    database = get_client()[MONGODB_DATABASE]
    ensure_indexes(database)
    return database


def ensure_indexes(database: Database) -> None:
    global _indexes_ready
    if _indexes_ready:
        return
    with _indexes_lock:
        if _indexes_ready:
            return
        database.users.create_index(
            "email",
            unique=True,
            name="email_unique",
            collation=Collation(locale="en", strength=2),
        )
        database.sessions.create_index("token_hash", unique=True, name="token_hash_unique")
        database.sessions.create_index("expires_at", expireAfterSeconds=0, name="sessions_expiry")
        database.conversations.create_index(
            [("user_id", 1), ("session_id", 1)],
            unique=True,
            name="conversation_owner_session_unique",
        )
        database.messages.create_index(
            [("user_id", 1), ("session_id", 1), ("timestamp", 1)],
            name="messages_owner_session_time",
        )
        database.memories.create_index(
            [("user_id", 1), ("kind", 1), ("fact_key", 1)],
            unique=True,
            name="memory_owner_key_unique",
        )
        ensure_memory_schema(database)
        _indexes_ready = True


def hash_password(password: str) -> str:
    rounds = max(4, min(BCRYPT_ROUNDS, 31))
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=rounds)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "created_at": user.get("created_at"),
    }


def create_session(database: Database, user_id: Any) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session = {
        "user_id": user_id,
        "token_hash": hash_token(token),
        "created_at": now,
        "expires_at": now + timedelta(hours=SESSION_TTL_HOURS),
    }
    database.sessions.insert_one(session)
    return token


def create_user(database: Database, request: CredentialsRequest) -> dict[str, Any]:
    existing = database.users.find_one({"email": request.email})
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = {
        "email": request.email,
        "password_hash": hash_password(request.password),
        "created_at": datetime.now(timezone.utc),
    }
    try:
        user_id = database.users.insert_one(user).inserted_id
    except DuplicateKeyError as error:
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from error

    token = create_session(database, user_id)
    user["_id"] = user_id
    return {"success": True, "token": token, "user": public_user(user)}


def authenticate_user(database: Database, request: CredentialsRequest) -> dict[str, Any]:
    user = database.users.find_one({"email": request.email})
    if not user or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token = create_session(database, user["_id"])
    return {"success": True, "token": token, "user": public_user(user)}


def get_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def get_current_user(token: Annotated[str, Depends(get_bearer_token)]) -> dict[str, Any]:
    try:
        database: Database = get_database()
        token_hash = hash_token(token)
        now = datetime.now(timezone.utc)
        session = database.sessions.find_one(
            {"token_hash": token_hash, "expires_at": {"$gt": now}},
        )
        if session is None:
            raise HTTPException(status_code=401, detail="Your session is invalid or has expired.")

        user = database.users.find_one({"_id": session["user_id"]})
        if user is None:
            database.sessions.delete_one({"_id": session["_id"]})
            raise HTTPException(status_code=401, detail="Your session is invalid or has expired.")
        return public_user(user)
    except PyMongoError as error:
        raise database_error(error) from error


def revoke_session(token: str) -> None:
    database: Database = get_database()
    database.sessions.delete_many({"token_hash": hash_token(token)})


def database_error(error: PyMongoError) -> HTTPException:
    return HTTPException(status_code=503, detail=str(error) or "Database unavailable.")

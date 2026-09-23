"""Persistent user memory, stored per user_id (never per chat session).

Two separate things must not be confused:

* chat history -> `messages` collection, scoped to (user_id, session_id).
  It belongs to ONE conversation and disappears when that chat is deleted.
* user memory -> `user_memories` collection, scoped to (user_id, memory_key).
  It belongs to the USER, so it survives New Chat, logout/login and the
  deletion of any conversation.

On every message the flow is: load chat history -> load these memories ->
retrieve document chunks -> ask the LLM -> store any new fact the user just
gave about themselves.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo.database import Database
from pymongo.errors import PyMongoError

from main import DEFAULT_LLM_MODEL

MEMORY_COLLECTION = "user_memories"
LEGACY_COLLECTION = "memories"  # written by the previous scheme; imported once

MAX_MEMORIES = 50
MAX_VALUE_LENGTH = 400

# How memories are ordered in the prompt: the most useful facts come first.
KEY_ORDER = (
    "name",
    "profession",
    "education",
    "skills",
    "preferences",
    "interests",
    "projects",
    "location",
    "facts",
)
ALLOWED_KEYS = frozenset(KEY_ORDER)
# Keys that accumulate values instead of overwriting them.
LIST_KEYS = frozenset({"skills", "preferences", "interests", "projects"})

_LEGACY_KEYS = {"name": "name", "preference": "preferences", "interest": "interests"}

# Credentials must never be persisted.
BLOCKED_KEY = re.compile(
    r"password|passwd|pwd|secret|token|api[\s_-]?key|credential|private[\s_-]?key"
    r"|authorization|bearer|otp|passcode|ssn|credit|card",
    re.IGNORECASE,
)
BLOCKED_VALUE = re.compile(
    r"\b(?:password|passwd|pwd|secret|token|api[_-]?key|credential|bearer|authorization)"
    r"\b\s*(?:is|are|:|=)\s*\S"
    r"|sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{8,}\.eyJ",
    re.IGNORECASE,
)

# Facts that refer to the UI/documents rather than to the user.
_ANAPHORIC = re.compile(
    r"^(?:it|its|this|that|these|those|the (?:document|file|pdf|upload|summary|summaries"
    r"|answer|response|chat|message|question|previous))\b",
    re.IGNORECASE,
)

# (key, pattern) pairs. "auto" is classified from the matched value.
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("name", r"\bmy name is ([A-Za-z][A-Za-z .'\-]{0,60})"),
    ("name", r"\bcall me ([A-Za-z][A-Za-z .'\-]{0,60})"),
    ("skills", r"\bi (?:am|'m|im) (?:currently )?(?:learning|studying|practicing) ([^.;!?\n]{2,120})"),
    ("skills", r"\bmy (?:skills|stack|tech stack|technology stack) (?:are|is|:) ([^.;!?\n]{2,120})"),
    ("skills", r"\bi (?:know|can (?:code|program|develop) in|have experience (?:in|with)) ([^.;!?\n]{2,120})"),
    ("interests", r"\bi (?:am|'m|im) interested in ([^.;!?\n]{2,120})"),
    ("projects", r"\bi (?:am|'m|im) (?:currently )?working on ([^.;!?\n]{2,120})"),
    ("projects", r"\bmy (?:current |latest )?project (?:is|:) ([^.;!?\n]{2,120})"),
    ("location", r"\bi (?:live|am living) in ([^.;!?\n]{2,80})"),
    ("location", r"\bi (?:am|'m|im) from ([^.;!?\n]{2,80})"),
    ("profession", r"\bi work (?:as|for) ([^.;!?\n]{2,70})"),
    ("profession", r"\bmy (?:job|profession|occupation|role|title) (?:is|:) ([^.;!?\n]{2,70})"),
    ("education", r"\bmy (?:degree|major|qualification|field of study) (?:is|:) ([^.;!?\n]{2,70})"),
    ("education", r"\bi (?:study|am studying|am pursuing) ([^.;!?\n]{2,70})"),
    ("preferences", r"\bi (?:prefer|love|enjoy) ([^.;!?\n]{2,120})"),
    ("auto", r"\bi (?:am|'m|im) (?:a |an )([^.;!?\n]{2,70})"),
)

_EDUCATION_MARKERS = (
    "graduate",
    "student",
    "studying",
    "pursuing",
    "degree",
    "bachelor",
    "b.tech",
    "btech",
    "m.tech",
    "master",
    "diploma",
    "phd",
    "university",
    "college",
    "school",
    "alumni",
    "semester",
)
_PROFESSION_MARKERS = (
    "engineer",
    "developer",
    "designer",
    "manager",
    "analyst",
    "architect",
    "consultant",
    "scientist",
    "doctor",
    "teacher",
    "nurse",
    "lawyer",
    "writer",
    "editor",
    "artist",
    "chef",
    "officer",
    "specialist",
    "intern",
    "founder",
    "entrepreneur",
    "programmer",
    "administrator",
    "recruiter",
    "accountant",
    "tester",
    "devops",
    "researcher",
)

_FIRST_PERSON = re.compile(r"^\s*(?:i|i'm|im|my|me)\b", re.IGNORECASE)
_NOT_A_STATEMENT = re.compile(
    r"^\s*i (?:want|need|would like|was wondering|am wondering|hope|guess|suppose|was thinking)\b",
    re.IGNORECASE,
)

_EXTRACTION_SYSTEM = (
    "You turn one chat message into durable facts about the user. "
    'Reply with ONLY valid JSON: {"memories":[{"memory_key":"name","memory_value":"Suraj"}]}. '
    "Allowed memory_key values: name, profession, education, skills, preferences, "
    "interests, projects, location, facts. "
    "Include only facts the user explicitly stated about themselves. Never infer or "
    "guess, and never store questions, tasks, document or file contents, code, or "
    "facts about other people. Never store passwords, API keys, tokens or any "
    'credential. Return at most 5 items, or {"memories":[]} when there is nothing '
    "to remember."
)


def normalize_key(memory_key: Any) -> str:
    """Force a key into a safe lowercase slug."""
    cleaned = re.sub(r"[^a-z0-9 _-]", " ", str(memory_key).lower())
    return re.sub(r"[\s_]+", "_", cleaned).strip("_-")[:64]


def clean_value(memory_value: Any) -> str:
    """Collapse whitespace and cap the stored value."""
    return re.sub(r"\s+", " ", str(memory_value)).strip()[:MAX_VALUE_LENGTH]


def is_sensitive(memory_key: str, memory_value: str) -> bool:
    """True when the pair looks like a credential that must not be stored."""
    return bool(
        BLOCKED_KEY.search(memory_key or "")
        or BLOCKED_VALUE.search(memory_value or "")
    )


def _clean(matched: str) -> str:
    return re.sub(r"\s+", " ", matched).strip(" \t\r\n.,;:!?'\"()[]{}")[:MAX_VALUE_LENGTH]


def _classify(value: str) -> str | None:
    """Decide whether a generic "I am a ..." value is education or profession."""
    lowered = value.lower()
    if any(marker in lowered for marker in _EDUCATION_MARKERS):
        return "education"
    if any(marker in lowered for marker in _PROFESSION_MARKERS):
        return "profession"
    return None


def extract_facts(message: str) -> dict[str, str]:
    """Fast, free extraction of explicit personal facts from one message."""
    facts: dict[str, str] = {}
    for key, pattern in _PATTERNS:
        match = re.search(pattern, message, re.IGNORECASE)
        if not match:
            continue
        value = _clean(match.group(1))
        if key == "auto":
            value = value.split(" and ")[0].strip()
        if not value or _ANAPHORIC.match(value):
            continue
        target = _classify(value) if key == "auto" else key
        if target and target in ALLOWED_KEYS and target not in facts:
            facts[target] = value
    return facts


def _looks_like_personal_statement(message: str) -> bool:
    """Gate the LLM fallback so questions and instructions never cost a call."""
    if "?" in message:
        return False
    if not _FIRST_PERSON.match(message):
        return False
    return not _NOT_A_STATEMENT.match(message)


def _parse_extraction(raw: str) -> list[tuple[str, str]]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        payload = json.loads(raw[start : end + 1])
    except ValueError:
        return []
    items = payload.get("memories")
    if not isinstance(items, list):
        return []
    found: list[tuple[str, str]] = []
    for item in items[:MAX_MEMORIES]:
        if not isinstance(item, dict):
            continue
        key = normalize_key(item.get("memory_key") or item.get("key") or "facts")
        value = clean_value(item.get("memory_value") or item.get("value") or "")
        if key not in ALLOWED_KEYS:
            key = "facts"
        if value:
            found.append((key, value))
    return found


def _llm_extract(message: str) -> list[tuple[str, str]]:
    """Optional fallback for phrasings the patterns do not cover. Never raises."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or len(message.strip()) < 8:
        return []
    try:
        from groq import Groq

        response = Groq(api_key=api_key).chat.completions.create(
            model=DEFAULT_LLM_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": message},
            ],
        )
        raw = response.choices[0].message.content or ""
    except Exception:
        return []
    return [item for item in _parse_extraction(raw) if not is_sensitive(*item)]


def store_facts(database: Database, user_id: str, facts: dict[str, str]) -> int:
    """Insert or update memories for one user. Returns how many rows changed."""
    collection = database[MEMORY_COLLECTION]
    now = datetime.now(timezone.utc)
    changed = 0
    for raw_key, raw_value in facts.items():
        key = normalize_key(raw_key)
        value = clean_value(raw_value)
        if not key or key not in ALLOWED_KEYS or not value:
            continue
        if is_sensitive(key, value):
            continue
        existing = collection.find_one({"user_id": user_id, "memory_key": key})
        if existing is None:
            collection.insert_one(
                {
                    "id": f"mem_{uuid4().hex}",
                    "user_id": user_id,
                    "memory_key": key,
                    "memory_value": value,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            changed += 1
            continue
        current = clean_value(existing.get("memory_value", ""))
        if key in LIST_KEYS and value.lower() not in current.lower():
            merged = clean_value(f"{current}; {value}")
            if merged == current:
                continue
            value = merged
        elif value == current:
            continue
        collection.update_one(
            {"id": existing["id"], "user_id": user_id},
            {"$set": {"memory_value": value, "updated_at": now}},
        )
        changed += 1
    return changed


def save_memories_from_message(database: Database, user_id: str, message: str) -> int:
    """Detect important personal information in one user message and persist it.

    Regular patterns handle the common cases for free; the LLM is only asked
    when nothing matched and the message really looks like a statement about
    the user, so ordinary questions never store anything.
    """
    facts = extract_facts(message)
    if not facts and _looks_like_personal_statement(message):
        facts = dict(_llm_extract(message))
    if not facts:
        return 0
    return store_facts(database, user_id, facts)


def _ordered(entries: dict[str, str]) -> list[str]:
    order = {key: index for index, key in enumerate(KEY_ORDER)}
    keys = sorted(entries, key=lambda key: (order.get(key, len(KEY_ORDER)), key))
    return [f"{key}: {entries[key]}" for key in keys]


def load_memories(database: Database, user_id: str) -> list[str]:
    """Memories to inject into the prompt for this user (never another user's)."""
    documents = list(
        database[MEMORY_COLLECTION]
        .find({"user_id": user_id}, {"_id": 0, "memory_key": 1, "memory_value": 1})
        .sort("updated_at", -1)
        .limit(MAX_MEMORIES)
    )
    entries: dict[str, str] = {}
    for document in documents:
        key = str(document.get("memory_key") or "")
        value = clean_value(document.get("memory_value") or "")
        if key in ALLOWED_KEYS and value:
            entries[key] = value
    return _ordered(entries)


_MEMORY_PROJECTION = {
    "_id": 0,
    "id": 1,
    "memory_key": 1,
    "memory_value": 1,
    "created_at": 1,
    "updated_at": 1,
}


def list_memories(database: Database, user_id: str) -> list[dict[str, Any]]:
    """Full records for GET /memory, including the fields the prompt skips."""
    records = list(
        database[MEMORY_COLLECTION].find({"user_id": user_id}, _MEMORY_PROJECTION)
    )
    order = {key: index for index, key in enumerate(KEY_ORDER)}
    records.sort(
        key=lambda item: (
            order.get(str(item.get("memory_key")), len(KEY_ORDER)),
            str(item.get("memory_key")),
        )
    )
    return records


def upsert_memory(
    database: Database, user_id: str, memory_key: str, memory_value: str
) -> dict[str, Any]:
    """Create or update one memory for POST /memory. Raises ValueError when invalid."""
    key = normalize_key(memory_key)
    value = clean_value(memory_value)
    if not key:
        raise ValueError("memory_key must contain letters or numbers.")
    if not value:
        raise ValueError("memory_value cannot be empty.")
    if key not in ALLOWED_KEYS:
        raise ValueError(f"memory_key must be one of: {', '.join(KEY_ORDER)}.")
    if is_sensitive(key, value):
        raise ValueError(
            "Passwords, API keys, tokens and other credentials cannot be stored."
        )
    store_facts(database, user_id, {key: value})
    record = database[MEMORY_COLLECTION].find_one(
        {"user_id": user_id, "memory_key": key}, _MEMORY_PROJECTION
    )
    if record is None:  # pragma: no cover - store_facts always writes
        raise ValueError("The memory could not be saved.")
    return record


def delete_memory(database: Database, user_id: str, memory_id: str) -> bool:
    """Delete one of THIS user's memories. Another user's id simply won't match."""
    result = database[MEMORY_COLLECTION].delete_one(
        {"id": memory_id, "user_id": user_id}
    )
    return bool(result.deleted_count)


def _legacy_value(content: Any) -> str:
    text = clean_value(content)
    match = re.fullmatch(r"The user'?s \w+ is (.+?)\.?", text)
    return clean_value(match.group(1)) if match else text


def _import_legacy_memories(database: Database) -> None:
    """One-time, idempotent import of facts saved by the previous scheme."""
    collection = database[MEMORY_COLLECTION]
    legacy = database[LEGACY_COLLECTION]
    now = datetime.now(timezone.utc)
    cursor = legacy.find(
        {"migrated_at": {"$exists": False}},
        {"user_id": 1, "kind": 1, "content": 1},
    ).sort("updated_at", 1)
    for document in cursor:
        user_id = document.get("user_id")
        value = _legacy_value(document.get("content"))
        if not user_id or not value:
            continue
        key = _LEGACY_KEYS.get(str(document.get("kind") or ""), "facts")
        if key in LIST_KEYS:
            store_facts(database, str(user_id), {key: value})
        else:
            collection.update_one(
                {"user_id": user_id, "memory_key": key},
                {
                    "$set": {"memory_value": value, "updated_at": now},
                    "$setOnInsert": {"id": f"mem_{uuid4().hex}", "created_at": now},
                },
                upsert=True,
            )
        legacy.update_one({"_id": document["_id"]}, {"$set": {"migrated_at": now}})


def ensure_memory_schema(database: Database) -> None:
    """Create the memory index and import older facts. Called from ensure_indexes."""
    database[MEMORY_COLLECTION].create_index(
        [("user_id", 1), ("memory_key", 1)],
        unique=True,
        name="user_memory_owner_key_unique",
    )
    try:
        _import_legacy_memories(database)
    except PyMongoError:
        pass  # never let a legacy import break login or chat

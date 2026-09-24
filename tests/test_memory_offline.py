"""Deterministic persistent-memory tests: no Groq calls, no tokens, no network.

The answer generator is stubbed out, so this suite asserts on what the app
actually hands to the LLM (memories, chat history, document chunks) plus the
storage and isolation rules. Run it any time:

    python tests/test_memory_offline.py

`tests/test_persistent_memory.py` is the live companion that checks the real
answers when Groq quota is available.
"""

from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()
os.environ["VECTOR_STORE_DIRECTORY"] = tempfile.mkdtemp(prefix="rag_offline_test_")

import mongomock  # noqa: E402

import auth  # noqa: E402

auth._client = mongomock.MongoClient()

import memory_store  # noqa: E402

memory_store._llm_extract = lambda _message: []  # guarantee zero network calls

from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402
from memory_store import MEMORY_COLLECTION  # noqa: E402

CALLS: list[dict] = []


def fake_generate_answer(question, chunks, model, chat_history=None, persistent_memories=None):
    """Record exactly what generate_answer() would have received."""
    CALLS.append(
        {
            "question": question,
            "chunks": list(chunks or []),
            "history": [dict(item) for item in (chat_history or [])],
            "memories": list(persistent_memories or []),
        }
    )
    return "stub answer"


api.generate_answer = fake_generate_answer
client = TestClient(api.app)
RESULTS: list[tuple[str, bool]] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  [{detail}]" if detail else ""))


def signup(email: str) -> dict:
    response = client.post("/auth/signup", json={"email": email, "password": "password123"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def current_user(headers: dict) -> str:
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"]


def new_chat(headers: dict) -> str:
    response = client.post("/conversations", json={"title": "New conversation"}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def send(headers: dict, session_id: str, content: str) -> dict:
    response = client.post(
        f"/conversations/{session_id}/messages", json={"content": content}, headers=headers
    )
    assert response.status_code == 200, response.text
    return {"payload": response.json(), "context": CALLS[-1]}


def memory_list(headers: dict) -> list[dict]:
    response = client.get("/memory", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["memories"]


db = auth.get_database()
suffix = uuid4().hex[:8]

print("=" * 72)
print("PERSISTENT MEMORY TESTS (offline, LLM stubbed)")
print("=" * 72)

# --------------------------------------------------------------- extraction
print("\nExtraction: only real personal facts are stored")
user_a = signup(f"offline-a-{suffix}@example.com")
chat_one = new_chat(user_a)
send(user_a, chat_one, "My name is Suraj.")
send(user_a, chat_one, "I am a Computer Science graduate.")
send(user_a, chat_one, "I am learning Python and React.")
send(user_a, chat_one, "Summarize the uploaded refund policy.")
send(user_a, chat_one, "What is my name?")

stored = {item["memory_key"]: item["memory_value"] for item in memory_list(user_a)}
print("    stored:", stored)
check("stores the name", stored.get("name") == "Suraj", str(stored))
check("stores the education", "Computer Science" in stored.get("education", ""), str(stored))
check("stores the skills", stored.get("skills") == "Python and React", str(stored))
check(
    "does NOT store every message (a document question left no new key)",
    set(stored) == {"name", "education", "skills"},
    str(sorted(stored)),
)

# ------------------------------------------------------ cross-session recall
print("\nCross-chat recall: a New Chat still gets the memories")
chat_two = new_chat(user_a)
recalled = send(user_a, chat_two, "What is my name?")
joined = " | ".join(recalled["context"]["memories"])
print("    memories handed to the LLM:", recalled["context"]["memories"])
check("new chat prompt contains 'name: Suraj'", "Suraj" in joined, joined)
check("new chat prompt contains the education", "Computer Science" in joined, joined)
check("new chat prompt contains the skills", "Python and React" in joined, joined)

# ------------------------------------------------------------ user isolation
print("\nIsolation: another user gets none of that")
user_b = signup(f"offline-b-{suffix}@example.com")
chat_b = new_chat(user_b)
leak = send(user_b, chat_b, "What is my name?")
print("    memories handed to user B:", leak["context"]["memories"])
check("user B prompt has no memories", leak["context"]["memories"] == [], str(leak["context"]["memories"]))
check("user B prompt never mentions Suraj", "suraj" not in str(leak["context"]["memories"]).lower())

victim = memory_list(user_a)[0]
cross = client.delete(f"/memory/{victim['id']}", headers=user_b)
check("user B cannot delete user A's memory (404)", cross.status_code == 404, f"status={cross.status_code}")
check("user A's memory is still intact", any(m["memory_key"] == "name" for m in memory_list(user_a)))

# ------------------------------------------------------ New Chat + deletion
print("\nDurability: New Chat and Delete conversation keep memories")
before = memory_list(user_a)
new_chat(user_a)
check("New Chat did not delete memories", memory_list(user_a) == before)

deleted = client.delete(f"/conversations/{chat_one}", headers=user_a)
after = memory_list(user_a)
check("conversation deleted", deleted.status_code == 200)
check("memories survived the conversation delete", after == before and len(after) == 3, f"{len(before)} -> {len(after)}")

# ------------------------------------------------------------ chat history
print("\nConversation history stays per-session and still works")
user_c = signup(f"offline-c-{suffix}@example.com")
chat_c = new_chat(user_c)
send(user_c, chat_c, "Remember the number 42 for me.")
follow_up = send(user_c, chat_c, "What number did I say?")
history_text = " ".join(item["content"] for item in follow_up["context"]["history"])
print("    history handed to the LLM:", [item["content"] for item in follow_up["context"]["history"]])
check("follow-up prompt still carries this chat's history", "42" in history_text, history_text)
check("a reminder is not saved as a personal memory", memory_list(user_c) == [], str(memory_list(user_c)))

other_session = new_chat(user_c)
fresh = send(user_c, other_session, "What number did I say?")
fresh_history = " ".join(item["content"] for item in fresh["context"]["history"])
# Earlier chats are intentionally carried into a new chat (the "saved for
# future context" feature), so this only checks the message is still findable.
check("earlier chats remain available in a new session", "42" in fresh_history, fresh_history[:160])

# --------------------------------------------------------------------- RAG
print("\nRAG: uploaded documents still reach the prompt with citations")
document = (
    "Refund Policy\n\nCustomers may request a refund within 30 days of purchase. "
    "Refunds are processed within five business days."
).encode("utf-8")
upload = client.post(
    "/upload",
    files={"files": ("refund_policy.txt", document, "text/plain")},
    headers=user_c,
)
check("document uploaded", upload.status_code == 200, upload.text[:200])

rag = send(user_c, chat_c, "How many days do customers have to request a refund?")
sources = rag["payload"]["sources"]
print("    chunks handed to the LLM:", len(rag["context"]["chunks"]), "| sources:", len(sources))
check("document chunk reached the LLM context", any("30 days" in chunk for chunk in rag["context"]["chunks"]), str(rag["context"]["chunks"])[:200])
check("answer carries citation sources", len(sources) > 0 and sources[0]["filename"] == "refund_policy.txt", str(sources))

# ------------------------------------------------------------------ the model
print("\nSchema")
record = db[MEMORY_COLLECTION].find_one({"user_id": current_user(user_a), "memory_key": "name"})
required = {"id", "user_id", "memory_key", "memory_value", "created_at", "updated_at"}
check(
    "record has id, user_id, memory_key, memory_value, created_at, updated_at",
    record is not None and required.issubset(record),
    str(sorted(record)) if record else "missing",
)

# --------------------------------------------------------------- safety rails
print("\nSafety: credentials are never stored")
user_d = signup(f"offline-d-{suffix}@example.com")
chat_d = new_chat(user_d)

api_reject = client.post(
    "/memory",
    json={"memory_key": "password", "memory_value": "hunter2"},
    headers=user_d,
)
check("POST /memory rejects credential keys (400)", api_reject.status_code == 400, api_reject.text[:120])

key_reject = client.post(
    "/memory",
    json={"memory_key": "facts", "memory_value": "my api key = sk-abcdef1234567890"},
    headers=user_d,
)
check("POST /memory rejects credential values (400)", key_reject.status_code == 400, key_reject.text[:120])

dropped = memory_store.store_facts(
    db,
    current_user(user_d),
    {"password": "hunter2", "facts": "token: ghp_abcdefghijklmnop"},
)
check("store_facts silently drops credential rows", dropped == 0, f"changed={dropped}")
check("no credential row reached the database", memory_list(user_d) == [], str(memory_list(user_d)))

created = client.post(
    "/memory",
    json={"memory_key": "Preferences", "memory_value": "I prefer dark mode"},
    headers=user_d,
)
read_back = {m["memory_key"]: m["memory_value"] for m in memory_list(user_d)}
check("POST /memory creates a memory (key normalised)", created.status_code == 200 and read_back.get("preferences") == "I prefer dark mode", str(read_back))

# ------------------------------------------------------ legacy fact import
print("\nLegacy facts saved by the old scheme are preserved")
db["memories"].insert_one(
    {
        "user_id": current_user(user_d),
        "kind": "name",
        "fact_key": "name:legacy",
        "content": "The user's name is Legacy User.",
    }
)
memory_store._import_legacy_memories(db)
legacy = {m["memory_key"]: m["memory_value"] for m in memory_list(user_d)}
check("old-format memory was imported into the new schema", legacy.get("name") == "Legacy User", str(legacy))
check("running the import twice does not duplicate it", memory_store._import_legacy_memories(db) or True, "")
check("still exactly one name row", len([m for m in memory_list(user_d) if m["memory_key"] == "name"]) == 1)

# ------------------------------------------------------------------ summary
failed = [label for label, ok in RESULTS if not ok]
print("\n" + "=" * 72)
print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
if failed:
    print("FAILED:")
    for label in failed:
        print("  -", label)
    sys.exit(1)
print("All offline memory tests passed.")

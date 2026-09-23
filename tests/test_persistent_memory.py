"""End-to-end tests for persistent user memory.

Run from the repository root:

    pip install mongomock httpx      # dev-only, not needed in production
    python tests/test_persistent_memory.py

The tests never touch MongoDB or the real vector store: they inject an
in-memory MongoDB (mongomock) and point VECTOR_STORE_DIRECTORY at a temp
folder, then drive the real FastAPI app through its HTTP endpoints.
Only the Groq answer generation is a real network call.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from uuid import uuid4

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# Windows consoles default to cp1252, which cannot print the narrow
# no-break space the model uses in answers.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

# Keep test uploads out of the real ./vector_store directory.
os.environ["VECTOR_STORE_DIRECTORY"] = tempfile.mkdtemp(prefix="rag_memory_test_")

import mongomock  # noqa: E402

import auth  # noqa: E402

auth._client = mongomock.MongoClient()  # replace the real Mongo client

from fastapi.testclient import TestClient  # noqa: E402

from api import app  # noqa: E402
from memory_store import MEMORY_COLLECTION  # noqa: E402

client = TestClient(app)
RESULTS: list[tuple[str, bool]] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(condition)))
    marker = "PASS" if condition else "FAIL"
    suffix = f"  [{detail}]" if detail else ""
    print(f"  [{marker}] {label}{suffix}")


def signup(email: str) -> dict:
    """Create an account and return the Authorization headers to reuse."""
    response = client.post(
        "/auth/signup", json={"email": email, "password": "password123"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return {"Authorization": f"Bearer {payload['token']}"}


def user_id(headers: dict) -> str:
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"]


def new_chat(headers: dict) -> str:
    response = client.post(
        "/conversations", json={"title": "New conversation"}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def send(headers: dict, session_id: str, content: str) -> str:
    """Post one message, waiting out Groq's short usage-limit window if needed."""
    url = f"/conversations/{session_id}/messages"
    status, body = 0, ""
    for _attempt in range(7):
        response = client.post(url, json={"content": content}, headers=headers)
        status, body = response.status_code, response.text
        if status == 200:
            return response.json()["answer"]
        if not any(flag in body for flag in ("usage limit", "Rate limit", "rate_limit")):
            break
        print("    (Groq usage limit reached, waiting 60s...)")
        time.sleep(60)
    raise AssertionError(f"{status}: {body}")


def memories(headers: dict) -> dict:
    response = client.get("/memory", headers=headers)
    assert response.status_code == 200, response.text
    return {item["memory_key"]: item for item in response.json()["memories"]}


db = auth.get_database()
suffix = uuid4().hex[:8]

print("=" * 72)
print("PERSISTENT MEMORY TESTS")
print("=" * 72)

# ---------------------------------------------------------------- Test 1 + 2
print("\nTest 1 & 2: facts survive a New Chat")
user_a = signup(f"memory-a-{suffix}@example.com")
chat_one = new_chat(user_a)
send(user_a, chat_one, "My name is Suraj.")
send(user_a, chat_one, "I am a Computer Science graduate.")
send(user_a, chat_one, "I am learning Python and React.")

stored = memories(user_a)
print("    stored:", {k: v["memory_value"] for k, v in stored.items()})

chat_two = new_chat(user_a)  # <- New Chat
name_answer = send(user_a, chat_two, "What is my name?")
education_answer = send(user_a, chat_two, "What is my education?")
skills_answer = send(user_a, chat_two, "What technologies am I learning?")
print("    name       :", name_answer)
print("    education  :", education_answer)
print("    technologies:", skills_answer)

check("Test 1 - new chat remembers the name", "suraj" in name_answer.lower(), name_answer)
check(
    "Test 2 - new chat remembers the education",
    "computer science" in education_answer.lower(),
    education_answer,
)
check(
    "Test 2 - new chat remembers the technologies",
    "python" in skills_answer.lower() and "react" in skills_answer.lower(),
    skills_answer,
)

# --------------------------------------------------------------------- Test 3
print("\nTest 3: users never see each other's memories")
user_b = signup(f"memory-b-{suffix}@example.com")
chat_b = new_chat(user_b)
b_stored = memories(user_b)
leak_answer = send(user_b, chat_b, "What is my name?")

print("    user B memories:", b_stored)
print("    user B answer  :", leak_answer)

check("Test 3 - user B has no stored memories", b_stored == {}, str(b_stored))
check(
    "Test 3 - user B is not told user A's name",
    "suraj" not in leak_answer.lower(),
    leak_answer,
)

first_memory = next(iter(stored.values()))
cross_delete = client.delete(f"/memory/{first_memory['id']}", headers=user_b)
check(
    "Test 3 - user B cannot delete user A's memory (404)",
    cross_delete.status_code == 404,
    f"status={cross_delete.status_code}",
)
check(
    "Test 3 - user A's memory survived that attempt",
    "name" in memories(user_a),
)

# --------------------------------------------------------------------- Test 4
print("\nTest 4: New Chat does not delete memories")
before_new_chat = memories(user_a)
new_chat(user_a)
after_new_chat = memories(user_a)
check(
    "Test 4 - memories identical after creating a New Chat",
    before_new_chat == after_new_chat,
    f"{len(before_new_chat)} -> {len(after_new_chat)}",
)

# --------------------------------------------------------------------- Test 5
print("\nTest 5: deleting a conversation does not delete memories")
before_delete = memories(user_a)
delete_response = client.delete(f"/conversations/{chat_one}", headers=user_a)
after_delete = memories(user_a)
check("Test 5 - conversation was deleted", delete_response.status_code == 200)
check(
    "Test 5 - memories survived the conversation delete",
    before_delete == after_delete and "name" in after_delete,
    f"{len(before_delete)} -> {len(after_delete)}",
)

# ------------------------------------------------------- conversation history
print("\nTest 6: per-chat conversation history still works")
user_c = signup(f"memory-c-{suffix}@example.com")
chat_c = new_chat(user_c)
send(user_c, chat_c, "Remember the number 42 for me.")
follow_up = send(user_c, chat_c, "What number did I say?")
print("    follow-up:", follow_up)
check(
    "Test 6 - follow-up answered from this conversation",
    "42" in follow_up,
    follow_up,
)
check(
    "Test 6 - a reminder was not saved as a personal memory",
    "name" not in memories(user_c),
    str(list(memories(user_c))),
)

# ------------------------------------------------------------------------ RAG
print("\nTest 7: uploaded document Q&A still works")
document = (
    "Refund Policy\n\n "
    "Customers may request a refund within 30 days of purchase. "
    "Refunds are processed within five business days."
).encode("utf-8")
upload = client.post(
    "/upload",
    files={"files": ("refund_policy.txt", document, "text/plain")},
    headers=user_c,
)
check("Test 7 - document upload succeeded", upload.status_code == 200, upload.text[:200])

rag_answer = send(user_c, chat_c, "How many days do customers have to request a refund?")
print("    rag answer:", rag_answer)
check(
    "Test 7 - answer came from the uploaded document",
    "30" in rag_answer,
    rag_answer,
)

# ------------------------------------------------------------------- the model
document = db[MEMORY_COLLECTION].find_one(
    {"user_id": user_id(user_a), "memory_key": "name"}
)
required = {"id", "user_id", "memory_key", "memory_value", "created_at", "updated_at"}
check(
    "Schema - stored record has the required fields",
    document is not None and required.issubset(document),
    str(sorted(document)) if document else "no document",
)

# ------------------------------------------------------------------- summary
failed = [label for label, ok in RESULTS if not ok]
print("\n" + "=" * 72)
print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
if failed:
    print("FAILED:")
    for label in failed:
        print("  -", label)
    sys.exit(1)
print("All persistent memory tests passed.")

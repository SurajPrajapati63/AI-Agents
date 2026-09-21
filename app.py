from __future__ import annotations

from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv

from main import (
    DEFAULT_LLM_MODEL,
    TOP_K,
    generate_answer,
)
from rag_store import ChromaDocumentStore


# =========================================================
# CONFIGURATION
# =========================================================

load_dotenv()

st.set_page_config(
    page_title="Document Q&A",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =========================================================
# CUSTOM CSS
# =========================================================

st.markdown(
    """
    <style>

    /* =========================================
       MAIN PAGE
       ========================================= */

    .block-container {
        max-width: 1000px;
        padding-top: 2rem;
        padding-bottom: 7rem;
    }


    /* =========================================
       HEADER
       ========================================= */

    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }

    .subtitle {
        color: #808080;
        font-size: 1rem;
        margin-bottom: 1.5rem;
    }


    /* =========================================
       SIDEBAR
       ========================================= */

    section[data-testid="stSidebar"] {
        border-right: 1px solid rgba(128, 128, 128, 0.2);
    }


    /* =========================================
       CHAT MESSAGES
       ========================================= */

    [data-testid="stChatMessage"] {
        padding-top: 0.8rem;
        padding-bottom: 0.8rem;
        margin-bottom: 0.7rem;
        max-width: 78%;
        border-radius: 18px;
    }

    [data-testid="stChatMessageContent"] {
        line-height: 1.6;
    }

    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        margin-left: auto;
        background: #e8eefc;
        padding: 0.8rem 1rem;
    }

    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
        margin-right: auto;
        background: rgba(128, 128, 128, 0.10);
        padding: 0.8rem 1rem;
    }

    [data-testid="stChatMessage"] pre {
        border-radius: 10px;
    }

    .chat-heading {
        margin: 0 0 1rem;
        font-size: 1.15rem;
        font-weight: 650;
    }


    /* =========================================
       CHAT INPUT
       ========================================= */

    [data-testid="stChatInput"] {
        max-width: 1000px;
        margin: auto;
    }

    [data-testid="stChatInput"] textarea {
        border-radius: 24px !important;
        min-height: 50px !important;
        max-height: 150px !important;
        padding: 0.8rem 1rem !important;
    }

    [data-testid="stChatInput"] textarea:focus {
        border-color: rgba(100, 100, 255, 0.6) !important;
        box-shadow: 0 0 0 1px rgba(100, 100, 255, 0.2) !important;
    }


    /* =========================================
       SUCCESS / INFO BOX
       ========================================= */

    div[data-testid="stAlert"] {
        border-radius: 10px;
    }


    /* =========================================
       MOBILE
       ========================================= */

    @media (max-width: 640px) {

        .block-container {
            padding-top: 1rem;
            padding-left: 0.75rem;
            padding-right: 0.75rem;
            padding-bottom: 7rem;
        }

        .main-title {
            font-size: 1.7rem;
        }

        .subtitle {
            font-size: 0.9rem;
        }

        [data-testid="stChatInput"] {
            width: 100%;
        }

        [data-testid="stChatMessage"] {
            max-width: 92%;
        }
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# HEADER
# =========================================================

st.markdown(
    '<div class="main-title">📄 Document Q&A</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="subtitle">'
    "Upload a document and ask questions grounded only in its contents."
    "</div>",
    unsafe_allow_html=True,
)


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.header("📁 Document")

    uploaded_files = st.file_uploader(
        "Select PDF or TXT files",
        type=["pdf", "txt"],
        accept_multiple_files=True,
        help="Upload a PDF or plain text document.",
    )

    st.caption("Select one or more files, then click Upload and process.")

    if uploaded_files:
        for file in uploaded_files:
            st.caption(f"Selected: {file.name}")

    upload_clicked = st.button(
        "Upload and process",
        type="primary",
        disabled=not uploaded_files,
        use_container_width=True,
    )

    st.divider()



# =========================================================
# BUILD RAG
# =========================================================

@st.cache_resource()
def build_rag() -> ChromaDocumentStore:
    return ChromaDocumentStore()

rag = build_rag()


# =========================================================
# CHECK DOCUMENT
# =========================================================

if not uploaded_files:

    st.info(
        "👈 Upload a PDF or TXT document from the sidebar to start."
    )

    st.stop()


# =========================================================
# PROCESS DOCUMENTS
# =========================================================

file_signature = tuple(
    (file.name, len(file.getvalue()))
    for file in uploaded_files
)

if upload_clicked or st.session_state.get("processed_signature") == file_signature:
    if st.session_state.get("processed_signature") != file_signature:
        progress = st.progress(0, text="Uploading documents...")
        try:
            processed = []
            for index, file in enumerate(uploaded_files, 1):
                progress.progress(
                    (index - 1) / len(uploaded_files),
                    text=f"Processing {file.name}... Generating embeddings...",
                )
                processed.append(rag.add_document(file.name, file.getvalue()))
            st.session_state.processed_signature = file_signature
            progress.progress(1.0, text="Stored successfully.")
            st.success(f"Processed {len(processed)} document(s) successfully.")
        except Exception as error:
            st.error(f"Upload failed: {error}")
            st.stop()
else:
    st.info("Select your files, then click Upload and process.")
    st.stop()

st.success(f"ChromaDB contains {len(rag.documents())} processed document(s).")


# =========================================================
# CHAT STATE AND HISTORY
# =========================================================

if "conversations" not in st.session_state:
    conversation_id = uuid4().hex
    st.session_state.conversations = {
        conversation_id: {"title": "New chat", "messages": []}
    }
    st.session_state.active_conversation_id = conversation_id

if "active_conversation_id" not in st.session_state:
    st.session_state.active_conversation_id = next(iter(st.session_state.conversations))


def active_conversation() -> dict:
    return st.session_state.conversations[st.session_state.active_conversation_id]


def start_new_chat() -> None:
    conversation_id = uuid4().hex
    st.session_state.conversations[conversation_id] = {
        "title": "New chat",
        "messages": [],
    }
    st.session_state.active_conversation_id = conversation_id


with st.sidebar:
    st.divider()
    if st.button("＋ New Chat", use_container_width=True):
        start_new_chat()
        st.rerun()

    st.subheader("Chat History")
    for conversation_id, conversation in reversed(list(st.session_state.conversations.items())):
        label = conversation["title"]
        if st.button(
            label,
            key=f"conversation_{conversation_id}",
            use_container_width=True,
            type="primary" if conversation_id == st.session_state.active_conversation_id else "secondary",
        ):
            st.session_state.active_conversation_id = conversation_id
            st.rerun()

with st.sidebar:
    st.divider()
    st.subheader("Stored documents")
    for document in rag.documents():
        st.caption(f"{document['filename']} ({document['chunks']} chunks)")
        confirm_delete = st.checkbox(
            "Confirm delete",
            key=f"confirm_delete_{document['id']}",
        )
        if st.button(
            "Delete document",
            key=f"delete_{document['id']}",
            disabled=not confirm_delete,
            use_container_width=True,
        ):
            rag.delete_document(document["id"])
            st.session_state.pop("processed_signature", None)
            st.rerun()


# =========================================================
# CHAT HISTORY
# =========================================================

conversation = active_conversation()
messages = conversation["messages"]

st.markdown('<div class="chat-heading">AI Chat</div>', unsafe_allow_html=True)

if not messages:

    st.info(
        "💬 Ask a question about the uploaded document to begin."
    )

else:

    for message in messages:

        with st.chat_message(
            message["role"]
        ):

            st.markdown(
                message["content"]
            )

            if message.get("sources"):
                with st.expander("Sources"):
                    for source in message["sources"]:
                        page = source.get("page")
                        location = f" — Page {page}" if page else ""
                        st.caption(f"{source['filename']}{location}")


# =========================================================
# CHAT INPUT
# =========================================================
#
# Streamlit keeps this composer fixed at the bottom and supports Enter to send
# and Shift+Enter for a new line.
# =========================================================

question = st.chat_input(
    "Ask something about your document..."
)


# =========================================================
# HANDLE QUESTION
# =========================================================

if question:

    question = question.strip()


    if not question:

        st.warning(
            "Please enter a question."
        )

        st.stop()


    # -----------------------------------------------------
    # SHOW USER QUESTION
    # -----------------------------------------------------

    if conversation["title"] == "New chat":
        title_limit = 40
        conversation["title"] = question[:title_limit].rstrip()
        if len(question) > title_limit:
            conversation["title"] += "..."

    conversation["messages"].append(
        {
            "id": uuid4().hex,
            "role": "user",
            "content": question,
        }
    )


    # -----------------------------------------------------
    # GENERATE ANSWER
    # -----------------------------------------------------

    with st.spinner(
        "🔎 Searching the document and generating an answer..."
    ):

        retrieved = []
        try:

            # Retrieve relevant chunks
            retrieved = rag.retrieve(
                question,
                TOP_K,
            )


            # Extract only the retrieved chunk text for grounded generation.
            retrieved_chunks = [item["text"] for item in retrieved]


            # Generate answer using LLM
            answer = generate_answer(
                question,
                retrieved_chunks,
                DEFAULT_LLM_MODEL,
                conversation["messages"][:-1],
            )


        except Exception as error:

            answer = (
                "Sorry, I couldn't process your question.\n\n"
                f"Error: {error}"
            )


    # -----------------------------------------------------
    # SAVE ASSISTANT ANSWER
    # -----------------------------------------------------

    conversation["messages"].append(
        {
            "id": uuid4().hex,
            "role": "assistant",
            "content": answer,
            "sources": [
                {
                    "filename": item["metadata"]["filename"],
                    "page": item["metadata"].get("page"),
                    "chunk_id": item["metadata"]["chunk_id"],
                }
                for item in retrieved
            ] if "retrieved" in locals() else [],
        }
    )


    # -----------------------------------------------------
    # REFRESH CHAT
    # -----------------------------------------------------

    st.rerun()
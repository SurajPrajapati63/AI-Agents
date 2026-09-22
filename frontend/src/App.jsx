import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Bot, Check, ChevronDown, FileText, FolderOpen, LoaderCircle, Menu,
  MessageSquarePlus, Paperclip, Send, ShieldCheck, Sparkles, Trash2, UploadCloud, X,
} from 'lucide-react'
import { askQuestion, deleteDocument, getDocuments, uploadDocuments } from './services/api'
import './App.css'

const CHAT_STORAGE_KEY = 'document_qna_conversations'

function newConversation() {
  return { id: crypto.randomUUID(), title: 'New conversation', messages: [] }
}

function readConversations() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(CHAT_STORAGE_KEY))
    return Array.isArray(saved) && saved.length ? saved : [newConversation()]
  } catch {
    return [newConversation()]
  }
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(value)
}

function App() {
  const [conversations, setConversations] = useState(readConversations)
  const [activeId, setActiveId] = useState(null)
  const [documents, setDocuments] = useState([])
  const [selectedFiles, setSelectedFiles] = useState([])
  const [question, setQuestion] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [status, setStatus] = useState(null)
  const [mobileOpen, setMobileOpen] = useState(false)
  const composerFileInputRef = useRef(null)
  const chatScrollRef = useRef(null)
  const messagesEndRef = useRef(null)

  const selectedId = activeId || conversations[0].id
  const activeConversation = conversations.find((conversation) => conversation.id === selectedId) || conversations[0]

  useEffect(() => {
    sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(conversations))
  }, [conversations])

  useEffect(() => {
    getDocuments()
      .then((payload) => setDocuments(payload.documents || []))
      .catch(() => setStatus({ type: 'error', text: 'Backend unavailable. Start the API to manage documents.' }))
  }, [])

  useEffect(() => {
    chatScrollRef.current?.scrollTo({
      top: chatScrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [activeConversation?.messages, isSending])

  function updateActiveConversation(update) {
    setConversations((current) => current.map((conversation) => (
      conversation.id === selectedId ? update(conversation) : conversation
    )))
  }

  function startNewChat() {
    const conversation = newConversation()
    setConversations((current) => [conversation, ...current])
    setActiveId(conversation.id)
    setQuestion('')
    setMobileOpen(false)
  }

  function deleteConversation(conversationId) {
    const conversation = conversations.find((item) => item.id === conversationId)
    if (!conversation || !window.confirm(`Delete "${conversation.title}"?`)) return

    const remaining = conversations.filter((item) => item.id !== conversationId)
    const nextConversations = remaining.length ? remaining : [newConversation()]
    setConversations(nextConversations)
    if (conversationId === selectedId) setActiveId(nextConversations[0].id)
  }

  function selectFiles(event) {
    const files = Array.from(event.target.files || [])
    const supported = files.filter((file) => /\.(pdf|txt)$/i.test(file.name))
    setSelectedFiles(supported)
    if (files.length !== supported.length) setStatus({ type: 'error', text: 'Only PDF and TXT files can be uploaded.' })
    event.target.value = ''
  }

  async function handleUpload() {
    if (!selectedFiles.length) return
    setIsUploading(true)
    setStatus({ type: 'loading', text: 'Uploading and generating embeddings...' })
    try {
      const payload = await uploadDocuments(selectedFiles)
      setDocuments((current) => [...(payload.documents || []), ...current])
      setSelectedFiles([])
      setStatus({ type: 'success', text: 'Documents stored successfully.' })
    } catch (error) {
      setStatus({ type: 'error', text: error.message })
    } finally {
      setIsUploading(false)
    }
  }

  async function handleDelete(document) {
    if (!window.confirm(`Delete ${document.filename} and its embeddings?`)) return
    try {
      await deleteDocument(document.id)
      setDocuments((current) => current.filter((item) => item.id !== document.id))
      setStatus({ type: 'success', text: `${document.filename} deleted.` })
    } catch (error) {
      setStatus({ type: 'error', text: error.message })
    }
  }

  async function sendQuestion(event) {
    event?.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || isSending) return

    const userMessage = { id: crypto.randomUUID(), role: 'user', content: trimmed }
    const history = activeConversation.messages.map(({ role, content }) => ({ role, content }))
    updateActiveConversation((conversation) => ({
      ...conversation,
      title: conversation.messages.length ? conversation.title : trimmed.slice(0, 38) + (trimmed.length > 38 ? '...' : ''),
      messages: [...conversation.messages, userMessage],
    }))
    setQuestion('')
    setIsSending(true)
    setStatus(null)

    try {
      const payload = await askQuestion(trimmed, history)
      updateActiveConversation((conversation) => ({
        ...conversation,
        messages: [...conversation.messages, { id: crypto.randomUUID(), role: 'assistant', content: payload.answer }],
      }))
    } catch (error) {
      updateActiveConversation((conversation) => ({
        ...conversation,
        messages: [...conversation.messages, { id: crypto.randomUUID(), role: 'assistant', content: `I couldn't complete that request.\n\n${error.message}`, error: true }],
      }))
    } finally {
      setIsSending(false)
    }
  }

  function handleComposerKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      sendQuestion(event)
    }
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? 'sidebar-open' : ''}`}>
        <div className="sidebar-header">
          <div className="brand-mark"><Sparkles size={16} /></div>
          <div><strong>Sourcewise</strong><span>Document intelligence</span></div>
          <button className="icon-button mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close sidebar"><X size={18} /></button>
        </div>
        <button className="new-chat-button" onClick={startNewChat}><MessageSquarePlus size={17} /> New chat</button>

        <section className="sidebar-section">
          <div className="section-label"><MessageSquarePlus size={14} /> Conversations</div>
          <div className="conversation-list">
            {conversations.filter((conversation) => conversation.messages.length > 0).map((conversation) => (
              <div className={`conversation-row ${conversation.id === selectedId ? 'active' : ''}`} key={conversation.id}>
                <button className="conversation-item" onClick={() => { setActiveId(conversation.id); setMobileOpen(false) }}>
                  <MessageSquarePlus size={15} /><span>{conversation.title}</span>
                </button>
                <button className="icon-button conversation-delete" onClick={() => deleteConversation(conversation.id)} aria-label={`Delete ${conversation.title}`}><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
        </section>

        <section className="sidebar-section documents-section">
          <div className="section-label"><FolderOpen size={14} /> Knowledge base</div>
          <div className="document-list">
            {documents.map((document) => <div className="document-row" key={document.id}><FileText size={16} /><div><strong>{document.filename}</strong><span>{document.chunks} chunks</span></div><button className="icon-button danger" onClick={() => handleDelete(document)} aria-label={`Delete ${document.filename}`}><Trash2 size={15} /></button></div>)}
            {!documents.length && <span className="empty-note">No documents stored yet.</span>}
          </div>
        </section>
        <div className="sidebar-footer"><ShieldCheck size={14} /> Answers stay grounded in your documents</div>
      </aside>

      {mobileOpen && <button className="sidebar-scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation" />}
      <main className="chat-workspace">
        {documents.length > 0 && <header className="topbar">
          <button className="icon-button menu-button" onClick={() => setMobileOpen(true)} aria-label="Open sidebar"><Menu size={20} /></button>
          <div><span className="eyebrow">Private workspace</span><h1>{activeConversation.title}</h1></div>
          <div className="connection-status"><span /> Vector store connected</div>
        </header>}

        <section ref={chatScrollRef} className="chat-scroll" aria-live="polite">
          <div className="chat-column">
            {!activeConversation.messages.length ? <div className="welcome-panel">
              <div className="welcome-icon"><Bot size={24} /></div><p className="eyebrow">Document Q&A</p><h2>Ask better questions of your files.</h2><p>Upload a policy, resume, or project note, then ask for a clear answer grounded in the text.</p>
              <div className="prompt-suggestions">{['Summarize the key points', 'What projects are mentioned?', 'What should I know first?'].map((prompt) => <button key={prompt} onClick={() => setQuestion(prompt)}>{prompt}<ChevronDown size={14} /></button>)}</div>
            </div> : activeConversation.messages.map((message) => (
              <article className={`message-row ${message.role}`} key={message.id}>
                <div className={`avatar ${message.role}`}>{message.role === 'assistant' ? <Bot size={16} /> : 'You'}</div>
                <div className="message-body"><div className="message-meta">{message.role === 'user' ? 'You' : 'Sourcewise'} <span>·</span> {formatDate(new Date())}</div>
                  <div className={`message-content ${message.error ? 'message-error' : ''}`}>{message.role === 'assistant' ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown> : <p>{message.content}</p>}</div>
                </div>
              </article>
            ))}
            {isSending && <article className="message-row assistant"><div className="avatar assistant"><Bot size={16} /></div><div className="message-body"><div className="message-meta">Sourcewise <span>·</span> thinking</div><div className="typing-indicator"><i /><i /><i /></div></div></article>}
            <div ref={messagesEndRef} />
          </div>
        </section>

        <footer className="composer-area">
          {status && <div className={`status-banner ${status.type}`}>{status.type === 'loading' && <LoaderCircle className="spin" size={14} />}{status.type === 'success' && <Check size={14} />}{status.text}</div>}
          {selectedFiles.length > 0 && <div className="composer-files">{selectedFiles.map((file) => <span key={`${file.name}-${file.size}`}><FileText size={13} /> {file.name}</span>)}<button type="button" onClick={handleUpload} disabled={isUploading}>{isUploading ? <LoaderCircle className="spin" size={13} /> : <UploadCloud size={13} />} {isUploading ? 'Processing' : 'Upload'}</button></div>}
          <form className="composer" onSubmit={sendQuestion}>
            <button type="button" className="composer-action" onClick={() => composerFileInputRef.current?.click()} aria-label="Attach documents"><Paperclip size={18} /></button>
            <input ref={composerFileInputRef} className="hidden-file-input" type="file" accept=".pdf,.txt" multiple onChange={selectFiles} />
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder="Ask anything about your documents..." rows="1" disabled={isSending} />
            <button className="send-button" type="submit" disabled={!question.trim() || isSending} aria-label="Send question"><Send size={17} /></button>
          </form>
          <p className="composer-hint">Enter to send · Shift + Enter for a new line</p>
        </footer>
      </main>
    </div>
  )
}

export default App

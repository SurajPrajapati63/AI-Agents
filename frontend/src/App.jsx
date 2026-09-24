import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Bot, Brain, Check, ChevronDown, FileText, FolderOpen, LoaderCircle, LogOut, Menu,
  MessageCircle, MessageSquarePlus, Paperclip, Send, ShieldCheck, Sparkles, Trash2, UserRound, X,
} from 'lucide-react'
import AuthPage from './Auth.jsx'
import {
  askQuestion, clearAuthToken, createConversation, deleteConversation as deleteConversationRequest,
  deleteDocument, deleteMemory, getConversationMessages, getCurrentUser, getDocuments, getAuthToken,
  getMemories, listConversations, logout as logoutRequest, sendConversationMessage, uploadDocuments,
} from './services/api'
import './App.css'

function newConversation() {
  return { id: crypto.randomUUID(), title: 'New conversation', messages: [] }
}

function App() {
  const [conversations, setConversations] = useState(() => [newConversation()])
  const [activeId, setActiveId] = useState(null)
  const [documents, setDocuments] = useState([])
  const [question, setQuestion] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [status, setStatus] = useState(null)
  const [toast, setToast] = useState(null)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [user, setUser] = useState(null)
  const [authLoading, setAuthLoading] = useState(() => Boolean(getAuthToken()))
  const composerFileInputRef = useRef(null)
  const chatScrollRef = useRef(null)
  const messagesEndRef = useRef(null)
  const toastTimerRef = useRef(null)
  const statusTimerRef = useRef(null)

  const selectedId = activeId || conversations[0].id
  const activeConversation = conversations.find((conversation) => conversation.id === selectedId) || conversations[0]

  function showToast(nextToast) {
    setToast((current) => current?.type === nextToast.type && current.text === nextToast.text ? current : nextToast)
    window.clearTimeout(toastTimerRef.current)
    toastTimerRef.current = window.setTimeout(() => setToast(null), 3200)
  }

  async function loadConversations() {
    const payload = await listConversations()
    const sessionSummaries = (payload.conversations || []).filter((summary) => (
      typeof summary?.session_id === 'string' && summary.session_id.length > 0
    ))
    const loaded = await Promise.all(sessionSummaries.map(async (summary) => {
      try {
        const history = await getConversationMessages(summary.session_id)
        const messages = (history.messages || []).map((message) => ({
          id: message.message_id || crypto.randomUUID(),
          role: message.role,
          content: message.content,
        }))
        return {
          id: summary.session_id,
          title: summary.title || 'New conversation',
          messages,
        }
      } catch (error) {
        // A conversation can be deleted in another tab/device after the
        // summary list loads. Drop the stale entry instead of retrying it on
        // every page load.
        if (error?.status === 404) return null
        throw error
      }
    }))
    const validConversations = loaded.filter(Boolean)
    const nextConversations = validConversations.length ? validConversations : [newConversation()]
    setConversations(nextConversations)
    setActiveId(nextConversations[0].id)
  }

  useEffect(() => {
    let active = true
    const token = getAuthToken()
    if (!token) {
      return undefined
    }

    getCurrentUser()
      .then((payload) => {
        if (!active) return undefined
        setUser(payload.user)
        return loadConversations()
      })
      .catch((error) => {
        if (!active) return undefined
        if (error?.status === 401) {
          clearAuthToken()
          setConversations([newConversation()])
        } else {
          showToast({ type: 'error', text: error.message || 'Unable to load conversations.' })
        }
      })
      .finally(() => {
        if (active) setAuthLoading(false)
      })

    function handleAuthExpired() {
      clearAuthToken()
      setUser(null)
      setConversations([newConversation()])
      setAuthLoading(false)
    }
    window.addEventListener('sourcewise-auth-expired', handleAuthExpired)
    return () => {
      active = false
      window.removeEventListener('sourcewise-auth-expired', handleAuthExpired)
    }
  }, [])

  useEffect(() => () => {
    window.clearTimeout(toastTimerRef.current)
    window.clearTimeout(statusTimerRef.current)
  }, [])

  useEffect(() => {
    if (!user) return undefined
    let active = true
    getDocuments()
      .then((payload) => {
        if (active) setDocuments(payload.documents || [])
      })
      .catch(() => {
        if (active) setStatus({ type: 'error', text: 'Backend unavailable. Start the API to manage documents.' })
      })
    return () => {
      active = false
    }
  }, [user])

  useEffect(() => {
    chatScrollRef.current?.scrollTo({
      top: chatScrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [activeConversation?.messages, isSending])

  function isServerConversation(conversationId) {
    return typeof conversationId === 'string' && conversationId.startsWith('chat_')
  }

  function updateConversation(conversationId, update) {
    setConversations((current) => current.map((conversation) => (
      conversation.id === conversationId ? update(conversation) : conversation
    )))
  }

  async function ensureServerConversation(conversationId, title) {
    if (isServerConversation(conversationId)) return conversationId
    const created = await createConversation(title)
    const serverId = created.session_id
    setConversations((current) => current.map((conversation) => (
      conversation.id === conversationId
        ? { ...conversation, id: serverId, title: created.title || conversation.title }
        : conversation
    )))
    setActiveId(serverId)
    return serverId
  }

  async function startNewChat() {
    try {
      const created = await createConversation()
      const conversation = { ...newConversation(), id: created.session_id, title: created.title }
      setConversations((current) => [conversation, ...current])
      setActiveId(conversation.id)
      setQuestion('')
      setMobileOpen(false)
    } catch (error) {
      if (error?.status === 404 || error?.status === 405) {
        const conversation = newConversation()
        setConversations((current) => [conversation, ...current])
        setActiveId(conversation.id)
        setQuestion('')
        setMobileOpen(false)
      } else {
        showToast({ type: 'error', text: error.message || 'Unable to create a new chat.' })
      }
    }
  }

  async function deleteConversation(conversationId) {
    const conversation = conversations.find((item) => item.id === conversationId)
    if (!conversation || !window.confirm(`Delete "${conversation.title}"?`)) return

    try {
      if (isServerConversation(conversationId)) await deleteConversationRequest(conversationId)
      const remaining = conversations.filter((item) => item.id !== conversationId)
      let replacement = null
      if (!remaining.length) {
        try {
          replacement = await createConversation()
        } catch (error) {
          if (error?.status !== 404 && error?.status !== 405) throw error
          replacement = { session_id: crypto.randomUUID(), title: 'New conversation' }
        }
      }
      const nextConversations = remaining.length
        ? remaining
        : [{ ...newConversation(), id: replacement.session_id, title: replacement.title }]
      setConversations(nextConversations)
      if (conversationId === selectedId) setActiveId(nextConversations[0].id)
    } catch (error) {
      showToast({ type: 'error', text: error.message || 'Unable to delete that chat.' })
    }
  }

  function handleAuthenticated(authUser) {
    setUser(authUser)
    setAuthLoading(false)
    loadConversations().catch((error) => showToast({ type: 'error', text: error.message || 'Unable to load conversations.' }))
  }

  async function handleLogout() {
    setUser(null)
    try {
      await logoutRequest()
    } catch {
    } finally {
      clearAuthToken()
      setAuthLoading(false)
      setConversations([newConversation()])
      setActiveId(null)
      setDocuments([])
      setQuestion('')
      setStatus(null)
      setMobileOpen(false)
      showToast({ type: 'success', text: 'Logged out successfully!' })
    }
  }

  async function selectFiles(event) {
    const files = Array.from(event.target.files || [])
    const supported = files.filter((file) => /\.(pdf|txt)$/i.test(file.name))
    event.target.value = ''
    if (files.length !== supported.length) setStatus({ type: 'error', text: 'Only PDF and TXT files can be uploaded.' })
    if (!supported.length) return

    setStatus({ type: 'loading', text: 'Uploading and generating embeddings...' })
    try {
      const payload = await uploadDocuments(supported)
      setDocuments((current) => [...(payload.documents || []), ...current])
      setStatus({ type: 'success', text: 'Documents stored successfully.' })
    } catch (error) {
      setStatus({ type: 'error', text: error.message })
    }
  }

  async function handleDelete(document) {
    if (!window.confirm(`Delete ${document.filename} and its embeddings?`)) return
    try {
      await deleteDocument(document.id)
      setDocuments((current) => current.filter((item) => item.id !== document.id))
      const message = `${document.filename} deleted.`
      setStatus({ type: 'success', text: message })
      window.clearTimeout(statusTimerRef.current)
      statusTimerRef.current = window.setTimeout(() => {
        setStatus((current) => current?.type === 'success' && current.text === message ? null : current)
      }, 3000)
    } catch (error) {
      setStatus({ type: 'error', text: error.message })
    }
  }

  async function sendQuestion(event) {
    event?.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || isSending) return

    setIsSending(true)
    setStatus(null)

    let conversationId = selectedId
    let legacyFallback = false
    try {
      conversationId = await ensureServerConversation(conversationId, activeConversation.title)
    } catch (error) {
      if (error?.status === 404 || error?.status === 405) {
        legacyFallback = true
      } else {
        setIsSending(false)
        showToast({ type: 'error', text: error.message || 'Unable to start this conversation.' })
        return
      }
    }

    const userMessage = { id: crypto.randomUUID(), role: 'user', content: trimmed }
    updateConversation(conversationId, (conversation) => ({
      ...conversation,
      title: conversation.messages.length ? conversation.title : trimmed.slice(0, 38) + (trimmed.length > 38 ? '...' : ''),
      messages: [...conversation.messages, userMessage],
    }))
    setQuestion('')

    try {
      let payload
      if (legacyFallback) {
        payload = await askQuestion(trimmed, activeConversation.messages.map(({ role, content }) => ({ role, content })))
      } else {
        try {
          payload = await sendConversationMessage(conversationId, trimmed)
        } catch (error) {
          // Repair stale conversation IDs transparently, then send the
          // message once against the newly created server conversation.
          if (error?.status !== 404) throw error
          const created = await createConversation(activeConversation.title)
          const replacementId = created.session_id
          setConversations((current) => current.map((conversation) => (
            conversation.id === conversationId
              ? { ...conversation, id: replacementId, title: created.title || conversation.title }
              : conversation
          )))
          setActiveId(replacementId)
          conversationId = replacementId
          payload = await sendConversationMessage(replacementId, trimmed)
        }
      }
      updateConversation(conversationId, (conversation) => ({
        ...conversation,
        messages: [...conversation.messages, { id: crypto.randomUUID(), role: 'assistant', content: payload.answer }],
      }))
    } catch (error) {
      updateConversation(conversationId, (conversation) => ({
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

  if (authLoading) {
    return <div className="auth-loading-screen"><div className="auth-loading-mark"><Sparkles size={22} /></div><LoaderCircle className="spin" size={20} /><p>Opening your workspace...</p></div>
  }
  if (!user) return <><AuthPage onAuthenticated={handleAuthenticated} onToast={showToast} />{toast && <div className={`toast ${toast.type}`} role="status">{toast.text}</div>}</>

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? 'sidebar-open' : ''}`}>
        <div className="sidebar-header">
          <div className="brand-mark"><Sparkles size={16} /></div>
          <div><strong>Sourcewise</strong><span>Document intelligence</span></div>
          <button className="icon-button mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close sidebar"><X size={18} /></button>
        </div>
        <button className="new-chat-button" onClick={startNewChat}><MessageSquarePlus size={17} /> New chat</button>

        <section className="sidebar-section conversations-section">
          <div className="section-label"><MessageSquarePlus size={14} /> Conversations</div>
          <div className="conversation-list">
            {conversations.filter((conversation) => conversation.messages.length > 0).map((conversation) => (
              <div className={`conversation-row ${conversation.id === selectedId ? 'active' : ''}`} key={conversation.id}>
                <button type="button" className="conversation-item" onClick={() => { setActiveId(conversation.id); setMobileOpen(false) }}>
                  <MessageCircle size={22} strokeWidth={1.7} /><span>{conversation.title}</span>
                </button>
                <button type="button" className="icon-button conversation-delete" onClick={(event) => { event.stopPropagation(); deleteConversation(conversation.id) }} aria-label={`Delete ${conversation.title}`}><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
        </section>

        <section className="sidebar-section documents-section">
          <div className="section-label"><FolderOpen size={14} /> My documents</div>
          <div className="document-list">
            {documents.map((document) => <div className="document-row" key={document.id}><FileText size={16} /><div><strong>{document.filename}</strong><span>{document.chunks} chunks</span></div><button className="icon-button danger" onClick={() => handleDelete(document)} aria-label={`Delete ${document.filename}`}><Trash2 size={15} /></button></div>)}
            {!documents.length && <span className="empty-note">No documents stored yet.</span>}
          </div>
        </section>
        <div className="sidebar-footer"><ShieldCheck size={14} /> Chats are saved for future context</div>
      </aside>

      {mobileOpen && <button className="sidebar-scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation" />}
      <main className="chat-workspace">
        <header className="topbar">
          <button className="icon-button menu-button" onClick={() => setMobileOpen(true)} aria-label="Open sidebar"><Menu size={20} /></button>
          <div><span className="eyebrow">Private workspace</span></div>
          <div className="connection-status"></div>
          <div className="user-controls">
            <div className="user-identity"><UserRound size={16} /><span>{user.email}</span></div>
            <button type="button" className="logout-button" onClick={handleLogout} aria-label="Sign out"><LogOut size={15} /><span>Sign out</span></button>
          </div>
        </header>

        <section ref={chatScrollRef} className="chat-scroll" aria-live="polite">
          <div className="chat-column">
            {!activeConversation.messages.length ? <div className="welcome-panel">
              <div className="welcome-icon"><Bot size={24} /></div><p className="eyebrow">Chat with your files</p><h2>Ask anything.</h2><p>General questions get a direct answer, questions about your uploaded files are answered from the file, and every chat is saved for future context.</p>
              <div className="prompt-suggestions">{['Summarize the key points', 'What projects are mentioned?', 'Explain this in simple words'].map((prompt) => <button key={prompt} onClick={() => setQuestion(prompt)}>{prompt}<ChevronDown size={14} /></button>)}</div>
            </div> : activeConversation.messages.map((message) => (
              <article className={`message-row ${message.role}`} key={message.id}>
                <div className={`avatar ${message.role}`}>{message.role === 'assistant' ? <Bot size={16} /> : 'You'} </div>
                <div className="message-body"><div className="message-meta">{message.role === 'user' ? "" : 'Sourcewise'}</div>
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
          <form className="composer" onSubmit={sendQuestion}>
            <button type="button" className="composer-action" onClick={() => composerFileInputRef.current?.click()} aria-label="Attach documents"><Paperclip size={18} /></button>
            <input ref={composerFileInputRef} className="hidden-file-input" type="file" accept=".pdf,.txt" multiple onChange={selectFiles} />
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder="Ask anything..." rows="1" disabled={isSending} />
            <button className="send-button" type="submit" disabled={!question.trim() || isSending} aria-label="Send question"><Send size={17} /></button>
          </form>
          <p className="composer-hint">Enter to send · Shift + Enter for a new line</p>
        </footer>
      </main>
      {toast && <div className={`toast ${toast.type}`} role="status">{toast.text}</div>}
    </div>
  )
}

export default App

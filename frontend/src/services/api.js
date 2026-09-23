const configuredApiUrl = import.meta.env.API_URL?.trim().replace(/\/+$/, '')
const API_URL = configuredApiUrl
const TOKEN_STORAGE_KEY = 'sourcewise_auth_token'
const REQUEST_TIMEOUT_MS = 30000
const RETRY_DELAY_MS = 2000

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function getAuthToken() {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
}

export function setAuthToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
  }
}

export function clearAuthToken() {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
  }
}

async function request(path, options = {}, authenticate = true, isRetry = false) {
  if (!API_URL) {
    throw new Error('The API is not configured. Set API_URL in the Vercel project settings.')
  }

  const headers = new Headers(options.headers)
  if (authenticate) {
    const token = getAuthToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
  }

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(`${API_URL}${path}`, { ...options, headers, signal: controller.signal })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      if (response.status === 401 && authenticate) {
        clearAuthToken()
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('sourcewise-auth-expired'))
        }
      }
      const detail = payload.detail
      const message = typeof detail === 'string'
        ? detail
        : payload.message || 'The backend request failed.'
      const error = new Error(message)
      error.status = response.status
      throw error
    }
    return payload
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Please try again.')
    if (error.status) throw error
    // Network error, CORS rejection, or a sleeping host (Render cold start) -> retry once.
    if (!isRetry) {
      await wait(RETRY_DELAY_MS)
      return request(path, options, authenticate, true)
    }
    if (error instanceof TypeError) {
      throw new Error('Cannot reach the server. It may be waking up - please try again in a moment.')
    }
    throw error
  } finally {
    clearTimeout(timeout)
  }
}

export function signup(credentials) {
  return request('/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(credentials),
  }, false)
}

export function login(credentials) {
  return request('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(credentials),
  }, false)
}

export function getCurrentUser() {
  return request('/auth/me')
}

export async function logout() {
  try {
    return await request('/auth/logout', { method: 'POST' })
  } finally {
    clearAuthToken()
  }
}

export function listConversations() {
  return request('/conversations')
}

export function createConversation(title = 'New conversation') {
  return request('/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

export function getConversationMessages(sessionId) {
  return request(`/conversations/${sessionId}/messages`)
}

export function sendConversationMessage(sessionId, content) {
  return request(`/conversations/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export function deleteConversation(sessionId) {
  return request(`/conversations/${sessionId}`, { method: 'DELETE' })
}

export function saveConversations(conversations) {
  return request('/conversations', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversations }),
  })
}

export function uploadDocuments(files) {
  const formData = new FormData()
  files.forEach((file) => formData.append('files', file))
  return request('/upload', { method: 'POST', body: formData })
}

export function askQuestion(question, chatHistory) {
  return request('/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, chat_history: chatHistory }),
  })
}

export function getDocuments() {
  return request('/documents')
}

export function deleteDocument(id) {
  return request(`/documents/${id}`, { method: 'DELETE' })
}

export function getMemories() {
  return request('/memory')
}

export function deleteMemory(id) {
  return request(`/memory/${id}`, { method: 'DELETE' })
}

export function checkHealth() {
  return request('/health', {}, false)
}

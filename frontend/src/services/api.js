const configuredApiUrl = import.meta.env.API_URL?.trim().replace(/\/+$/, '')
const API_URL = configuredApiUrl || (import.meta.env.DEV ? ' https://ai-agents-qigt.onrender.com' : '')
const REQUEST_TIMEOUT_MS = 30000

async function request(path, options = {}) {
  if (!API_URL) {
    throw new Error('The API is not configured. Set API_URL in the Vercel project settings.')
  }

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(`${API_URL}${path}`, { ...options, signal: controller.signal })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload.detail || payload.message || 'The backend request failed.')
    }
    return payload
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Please try again.')
    if (error instanceof TypeError) throw new Error('Unable to reach the backend service.')
    throw error
  } finally {
    clearTimeout(timeout)
  }
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

export function checkHealth() {
  return request('/health')
}

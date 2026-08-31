import type { ConversationSummary, PersistedMessage } from '../types'
import { csrfHeaders } from './auth'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(method === 'GET' ? {} : csrfHeaders()),
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) throw new Error(`Conversation request failed with ${response.status}.`)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function conversationHistoryEnabled(): Promise<boolean> {
  const response = await api<{ conversation_history?: { enabled?: boolean } }>('/api/health')
  return response.conversation_history?.enabled === true
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await api<{ conversations: ConversationSummary[] }>('/api/conversations?limit=100')
  return response.conversations
}

export function createConversation(memoryEnabled = false): Promise<ConversationSummary> {
  return api('/api/conversations', {
    method: 'POST',
    body: JSON.stringify({ title: 'New conversation', memory_enabled: memoryEnabled }),
  })
}

export async function listMessages(conversationId: string): Promise<PersistedMessage[]> {
  const response = await api<{ messages: PersistedMessage[] }>(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages?limit=200`,
  )
  return response.messages
}

export function updateConversation(
  conversationId: string,
  update: { title?: string; memory_enabled?: boolean },
): Promise<ConversationSummary> {
  return api(`/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'PATCH',
    body: JSON.stringify(update),
  })
}

export function deleteConversation(conversationId: string): Promise<void> {
  return api(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })
}

export function deleteAllConversations(): Promise<void> {
  return api('/api/conversations', { method: 'DELETE' })
}

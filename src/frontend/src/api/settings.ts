import type { MemoryItem, UserPreferences } from '../types'
import { csrfHeaders } from './auth'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.method && init.method !== 'GET' ? csrfHeaders() : {}),
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) throw new Error(`Settings request failed with ${response.status}.`)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function getPreferences(): Promise<UserPreferences> {
  return request('/api/preferences')
}

export function updatePreferences(values: Partial<UserPreferences>): Promise<UserPreferences> {
  return request('/api/preferences', { method: 'PATCH', body: JSON.stringify(values) })
}

export async function listMemory(): Promise<MemoryItem[]> {
  return (await request<{ memory: MemoryItem[] }>('/api/memory')).memory
}

export function createMemory(content: string): Promise<MemoryItem> {
  return request('/api/memory', { method: 'POST', body: JSON.stringify({ content }) })
}

export function updateMemory(id: string, content: string): Promise<MemoryItem> {
  return request(`/api/memory/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ content }) })
}

export function deleteMemory(id: string): Promise<void> {
  return request(`/api/memory/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

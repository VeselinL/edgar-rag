import type { ChatDocument } from '../types'
import { csrfHeaders } from './auth'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

export class DocumentRequestError extends Error {}

function mediaType(file: File): ChatDocument['media_type'] {
  const extension = file.name.toLocaleLowerCase().split('.').pop()
  if (file.type === 'application/pdf' || (!file.type && extension === 'pdf')) return 'application/pdf'
  if (file.type === 'text/plain' || (!file.type && extension === 'txt')) return 'text/plain'
  throw new DocumentRequestError('Choose a PDF or plain-text (.txt) file.')
}

async function responseError(response: Response): Promise<DocumentRequestError> {
  try {
    const body = await response.json() as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail.trim()) {
      return new DocumentRequestError(body.detail)
    }
  } catch {
    // Keep server internals out of the UI when the response is not JSON.
  }
  return new DocumentRequestError('The source could not be updated. Please try again.')
}

export async function uploadDocument(conversationId: string, file: File): Promise<ChatDocument> {
  if (!file.name.trim() || file.name.length > 255) {
    throw new DocumentRequestError('The filename must be between 1 and 255 characters.')
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new DocumentRequestError('Files must be 20 MiB or smaller.')
  }
  const contentType = mediaType(file)
  const response = await fetch(
    `${API_BASE_URL}/api/conversations/${encodeURIComponent(conversationId)}/documents?filename=${encodeURIComponent(file.name)}`,
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': contentType, ...csrfHeaders() },
      body: file,
    },
  )
  if (!response.ok) throw await responseError(response)
  return response.json() as Promise<ChatDocument>
}

export async function listDocuments(conversationId: string): Promise<ChatDocument[]> {
  const response = await fetch(
    `${API_BASE_URL}/api/conversations/${encodeURIComponent(conversationId)}/documents`,
    { credentials: 'include', headers: { Accept: 'application/json' } },
  )
  if (!response.ok) throw await responseError(response)
  const body = await response.json() as { documents: ChatDocument[] }
  return body.documents
}

export async function deleteDocument(conversationId: string, documentId: string): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/conversations/${encodeURIComponent(conversationId)}/documents/${encodeURIComponent(documentId)}`,
    { method: 'DELETE', credentials: 'include', headers: csrfHeaders() },
  )
  if (!response.ok) throw await responseError(response)
}

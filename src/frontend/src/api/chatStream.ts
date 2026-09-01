import type { Source, SourceStatus } from '../types'
import { csrfHeaders } from './auth'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

interface StreamHandlers {
  signal: AbortSignal
  onOpen: () => void
  onDelta: (text: string) => void
  onSources: (sources: Source[], sourceStatus: SourceStatus, malformedSourceCount: number) => void
  onDone: () => void
}

interface RawEvent {
  event: string
  data: unknown
}

export class ChatStreamError extends Error {}

function parseFrame(frame: string): RawEvent | null {
  let event = ''
  const dataLines: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(':')) continue
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (!event || dataLines.length === 0) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    throw new ChatStreamError('AVA returned a malformed stream event.')
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isSourceStatus(value: unknown): value is SourceStatus {
  return value === 'cited' || value === 'none_cited' || value === 'cited_with_unrenderable_items'
}

export interface ConversationTurn {
  conversationId: string
  clientTurnId: string
}

export async function streamChat(
  query: string,
  handlers: StreamHandlers,
  conversation?: ConversationTurn,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...csrfHeaders(),
      },
      body: JSON.stringify({
        query,
        ...(conversation ? {
          conversation_id: conversation.conversationId,
          client_turn_id: conversation.clientTurnId,
        } : {}),
      }),
      signal: handlers.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ChatStreamError('The AVA service could not be reached. Check the connection and retry.')
  }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: 'Your AVA session expired. Sign in again to continue.',
      404: 'This conversation was deleted or is no longer available. Start a new chat.',
      409: 'This conversation turn is already in progress. Wait a moment, then retry.',
      429: 'AVA is receiving too many requests. Wait briefly, then retry.',
      503: 'AVA is still preparing its filing services. Please retry shortly.',
    }
    throw new ChatStreamError(
      messages[response.status] ?? 'AVA could not start this analysis. Review the question and retry.',
    )
  }
  if (!response.body) throw new ChatStreamError('AVA returned an empty response stream.')
  handlers.onOpen()

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false

  const handleEvent = (raw: RawEvent) => {
    if (!isRecord(raw.data)) throw new ChatStreamError('AVA returned an invalid stream event.')
    if (raw.event === 'delta') {
      if (typeof raw.data.text !== 'string') throw new ChatStreamError('AVA returned an invalid text fragment.')
      if (raw.data.text.length > 0) handlers.onDelta(raw.data.text)
      return
    }
    if (raw.event === 'sources') {
      const sources = Array.isArray(raw.data.sources) ? (raw.data.sources as Source[]) : []
      if (!isSourceStatus(raw.data.source_status)) {
        throw new ChatStreamError('AVA returned an invalid source status.')
      }
      handlers.onSources(
        sources,
        raw.data.source_status,
        typeof raw.data.malformed_source_count === 'number' ? raw.data.malformed_source_count : 0,
      )
      return
    }
    if (raw.event === 'done') {
      terminal = true
      handlers.onDone()
      return
    }
    if (raw.event === 'error') {
      terminal = true
      throw new ChatStreamError(
        typeof raw.data.message === 'string'
          ? raw.data.message
          : 'The filing-analysis service returned an unusable error response.',
      )
    }
    throw new ChatStreamError('AVA returned an unknown stream event.')
  }

  while (!terminal) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const event = parseFrame(frame)
      if (event) handleEvent(event)
      if (terminal) break
    }
    if (done) break
  }
  if (!terminal) throw new ChatStreamError('The response was interrupted. Please try again.')
}

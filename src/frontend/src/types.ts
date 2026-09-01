export type RequestState =
  | 'idle'
  | 'submitting'
  | 'waiting_for_first_token'
  | 'streaming'
  | 'completed'
  | 'error'

interface SourceBase {
  company: string
  ticker: string
  filing_year: number
  section: string
  source_url?: string
}

export interface NarrativeSource extends SourceBase {
  content_type: 'text'
  text: string
}

export interface TableSource extends SourceBase {
  content_type: 'table'
  title?: string
  units?: string
  headers: string[]
  rows: string[][]
  column_units?: string[]
}

export interface WebSource {
  content_type: 'web'
  title: string
  publisher: string
  retrieved_at: string
  source_url: string
  excerpt: string
}

export interface UploadedSource {
  content_type: 'upload'
  document_id: string
  filename: string
  media_type: string
  page_number: number | null
  excerpt: string
}

export type Source = NarrativeSource | TableSource | WebSource | UploadedSource
export type SourceStatus = 'cited' | 'none_cited' | 'cited_with_unrenderable_items'

export interface UserMessage {
  id: string
  role: 'user'
  text: string
}

export interface AssistantMessage {
  id: string
  role: 'assistant'
  text: string
  state: Exclude<RequestState, 'idle' | 'submitting'>
  sources: Source[] | null
  sourceStatus: SourceStatus
  malformedSourceCount: number
  error?: string
  feedbackEligible?: boolean
  feedback?: 'helpful' | 'not_helpful' | 'submitting' | 'error'
}

export type ChatMessage = UserMessage | AssistantMessage

export interface ConversationSummary {
  id: string
  title: string
  memory_enabled: boolean
  pinned: boolean
  pinned_at: string | null
  created_at: string
  updated_at: string
}

export interface ChatDocument {
  id: string
  conversation_id: string
  filename: string
  media_type: 'application/pdf' | 'text/plain'
  size_bytes: number
  status: 'processing' | 'ready' | 'failed'
  page_count: number | null
  token_count: number
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface PersistedMessage {
  id: string
  client_turn_id: string
  role: 'user' | 'assistant'
  text: string
  status: 'in_progress' | 'completed' | 'failed'
  ordinal: number
  created_at: string
  source_event?: {
    sources: Source[]
    source_status: SourceStatus
    malformed_source_count: number
  }
}

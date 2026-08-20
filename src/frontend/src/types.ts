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

export type Source = NarrativeSource | TableSource

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
  citationFallback: boolean
  malformedSourceCount: number
  error?: string
}

export type ChatMessage = UserMessage | AssistantMessage

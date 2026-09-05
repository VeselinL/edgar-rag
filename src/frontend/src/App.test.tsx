import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { getAuthSession, signOut } from './api/auth'
import { streamChat } from './api/chatStream'
import { createMemory, deleteMemory, getPreferences, listMemory, updateMemory, updatePreferences } from './api/settings'
import { deleteDocument, listDocuments, uploadDocument } from './api/documents'
import {
  conversationHistoryEnabled,
  createConversation,
  exportConversations,
  listConversations,
  listMessages,
  submitFeedback,
  updateConversation,
} from './api/conversations'
import type { Source } from './types'

vi.mock('./api/chatStream', () => ({
  ChatStreamError: class ChatStreamError extends Error {},
  streamChat: vi.fn(),
}))

vi.mock('./api/auth', () => ({
  getAuthSession: vi.fn(async () => ({ mode: 'none', authenticated: true })),
  signInUrl: vi.fn(() => 'http://localhost:8000/api/auth/login?return_to=%2F'),
  signOut: vi.fn(async () => undefined),
}))

vi.mock('./api/documents', () => ({
  deleteDocument: vi.fn(),
  listDocuments: vi.fn(),
  uploadDocument: vi.fn(),
}))

vi.mock('./api/conversations', () => ({
  conversationHistoryEnabled: vi.fn(async () => false),
  createConversation: vi.fn(),
  deleteAllConversations: vi.fn(),
  deleteConversation: vi.fn(),
  exportConversations: vi.fn(),
  listConversations: vi.fn(),
  listMessages: vi.fn(),
  submitFeedback: vi.fn(),
  updateConversation: vi.fn(),
}))

vi.mock('./api/settings', () => ({
  createMemory: vi.fn(), deleteMemory: vi.fn(), getPreferences: vi.fn(),
  listMemory: vi.fn(), updateMemory: vi.fn(), updatePreferences: vi.fn(),
}))

const mockedStream = vi.mocked(streamChat)
const mockedDeleteDocument = vi.mocked(deleteDocument)
const mockedListDocuments = vi.mocked(listDocuments)
const mockedUploadDocument = vi.mocked(uploadDocument)
const mockedAuthSession = vi.mocked(getAuthSession)
const mockedSignOut = vi.mocked(signOut)
const mockedHistoryEnabled = vi.mocked(conversationHistoryEnabled)
const mockedCreateConversation = vi.mocked(createConversation)
const mockedExportConversations = vi.mocked(exportConversations)
const mockedListConversations = vi.mocked(listConversations)
const mockedListMessages = vi.mocked(listMessages)
const mockedSubmitFeedback = vi.mocked(submitFeedback)
const mockedUpdateConversation = vi.mocked(updateConversation)
const mockedCreateMemory = vi.mocked(createMemory)
const mockedDeleteMemory = vi.mocked(deleteMemory)
const mockedGetPreferences = vi.mocked(getPreferences)
const mockedListMemory = vi.mocked(listMemory)
const mockedUpdateMemory = vi.mocked(updateMemory)
const mockedUpdatePreferences = vi.mocked(updatePreferences)
const defaultPreferences = {
  nickname: '', warmth: 'balanced' as const, enthusiasm: 'balanced' as const, emoji_use: 'off' as const,
  custom_instructions: '', language: 'en' as const, model: 'AZURE_GPT_4o_2024_1120', theme: 'system' as const,
}
type Handlers = Parameters<typeof streamChat>[1]

describe('App', () => {
  beforeEach(() => {
    mockedStream.mockReset()
    mockedDeleteDocument.mockReset()
    mockedDeleteDocument.mockResolvedValue(undefined)
    mockedListDocuments.mockReset()
    mockedListDocuments.mockResolvedValue([])
    mockedUploadDocument.mockReset()
    mockedAuthSession.mockReset()
    mockedAuthSession.mockResolvedValue({ mode: 'none', authenticated: true })
    mockedSignOut.mockReset()
    mockedSignOut.mockResolvedValue(undefined)
    mockedHistoryEnabled.mockReset()
    mockedHistoryEnabled.mockResolvedValue(false)
    mockedCreateConversation.mockReset()
    mockedExportConversations.mockReset()
    mockedExportConversations.mockResolvedValue(new Blob(['{}'], { type: 'application/json' }))
    mockedListConversations.mockReset()
    mockedListMessages.mockReset()
    mockedSubmitFeedback.mockReset()
    mockedSubmitFeedback.mockResolvedValue(undefined)
    mockedUpdateConversation.mockReset()
    mockedCreateMemory.mockReset()
    mockedDeleteMemory.mockReset()
    mockedGetPreferences.mockReset()
    mockedGetPreferences.mockResolvedValue(defaultPreferences)
    mockedListMemory.mockReset()
    mockedListMemory.mockResolvedValue([])
    mockedUpdateMemory.mockReset()
    mockedUpdatePreferences.mockReset()
    mockedUpdatePreferences.mockResolvedValue(defaultPreferences)
    localStorage.clear()
    document.documentElement.dataset.theme = 'light'
  })

  it('describes the active eleven-company filing corpus', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Ask AVA about the SEC filings')).not.toBeDisabled())
    expect(screen.getByText(/SEC 10-K filings from eleven companies/)).toBeInTheDocument()
    expect(screen.getByAltText('AVA').getAttribute('src')).toContain('ava-light.png')
  })

  it('requires explicit sign-in without exposing the composer', async () => {
    mockedAuthSession.mockResolvedValue({ mode: 'oidc', authenticated: false })

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Sign in to AVA' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue to sign in' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Ask AVA about the SEC filings')).not.toBeInTheDocument()
    expect(mockedHistoryEnabled).not.toHaveBeenCalled()
  })

  it('shows an actionable startup failure instead of silently entering stateless mode', async () => {
    mockedAuthSession.mockRejectedValue(new Error('network details'))
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'AVA is temporarily unavailable' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Ask AVA about the SEC filings')).not.toBeInTheDocument()
    expect(screen.queryByText('network details')).not.toBeInTheDocument()
  })

  it('signs out without retaining the saved transcript in browser state', async () => {
    mockedAuthSession.mockResolvedValue({ mode: 'oidc', authenticated: true })
    mockedHistoryEnabled.mockResolvedValue(false)
    render(<App />)

    const button = await screen.findByRole('button', { name: 'Sign out' })
    await userEvent.click(button)

    expect(mockedSignOut).toHaveBeenCalledOnce()
    expect(await screen.findByRole('heading', { name: 'Sign in to AVA' })).toBeInTheDocument()
  })

  it('submits with Enter, shows waiting, and removes it on first token', async () => {
    let handlers: Handlers | undefined
    let finish: (() => void) | undefined
    mockedStream.mockImplementation((_query, receivedHandlers) => {
      handlers = receivedHandlers
      receivedHandlers.onOpen()
      return new Promise<void>((resolve) => { finish = resolve })
    })
    render(<App />)
    const input = screen.getByLabelText('Ask AVA about the SEC filings')
    await waitFor(() => expect(input).not.toBeDisabled())
    await userEvent.type(input, 'What does Tesla do?{enter}')
    expect(mockedStream).toHaveBeenCalledWith('What does Tesla do?', expect.any(Object), undefined, 'AZURE_GPT_4o_2024_1120')
    expect(screen.getByRole('status', { name: /^(Thinking|Reasoning|Cogitating|Cerebrating|Contemplating|Pondering|Ruminating|Sleuthing) \(in progress\)$/ })).toBeInTheDocument()

    act(() => handlers?.onDelta('Tesla builds electric vehicles.'))
    expect(screen.queryByRole('status', { name: /in progress/ })).not.toBeInTheDocument()
    expect(screen.getByText('Tesla builds electric vehicles.')).toBeInTheDocument()
    act(() => {
      handlers?.onDone()
      finish?.()
    })
    await waitFor(() => expect(input).not.toBeDisabled())
  })

  it('uses Shift+Enter for a newline without submitting', async () => {
    render(<App />)
    const input = screen.getByLabelText('Ask AVA about the SEC filings')
    await waitFor(() => expect(input).not.toBeDisabled())
    fireEvent.change(input, { target: { value: 'First line' } })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(mockedStream).not.toHaveBeenCalled()
  })

  it('renders filing, table, and web sources without raw IDs', async () => {
    const sources: Source[] = [
      {
        company: 'Tesla, Inc.', ticker: 'TSLA', filing_year: 2025,
        section: 'Item 1 — Business', content_type: 'text', text: 'Complete narrative evidence.',
      },
      {
        company: 'Mobileye Global Inc.', ticker: 'MBLY', filing_year: 2025,
        section: 'Item 8 — Financial Statements', content_type: 'table', title: 'Revenue',
        units: 'USD millions', headers: ['Category', '2025', '2024'],
        rows: [['Product', '10', '']], column_units: ['text', 'USD millions', 'USD millions'],
      },
      {
        content_type: 'web', title: 'Current AV report', publisher: 'example.com',
        retrieved_at: '2026-09-01T00:00:00+00:00', source_url: 'https://example.com/report',
        excerpt: 'Bounded web search evidence.',
      },
      {
        content_type: 'upload', document_id: 'document-1', filename: 'architecture.txt',
        media_type: 'text/plain', page_number: null, excerpt: 'Uploaded source evidence.',
      },
    ]
    mockedStream.mockImplementation(async (_query, handlers) => {
      handlers.onOpen()
      handlers.onDelta('Grounded answer.')
      handlers.onSources(sources, 'cited', 0)
      handlers.onDone()
    })
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Ask AVA about the SEC filings')).not.toBeDisabled())
    await userEvent.type(screen.getByLabelText('Ask AVA about the SEC filings'), 'Show evidence{enter}')
    const button = await screen.findByRole('button', { name: 'View sources (4)' })
    await userEvent.click(button)
    expect(screen.getByText('Complete narrative evidence.')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '2025' })).toBeInTheDocument()
    expect(screen.getByText('Bounded web search evidence.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open web source' })).toHaveAttribute('href', 'https://example.com/report')
    expect(screen.getByText('Uploaded source evidence.')).toBeInTheDocument()
    expect(screen.queryByText(/CHUNK-/)).not.toBeInTheDocument()
  })

  it('shows the no-reference state when no citation resolves', async () => {
    mockedStream.mockImplementation(async (_query, handlers) => {
      handlers.onOpen()
      handlers.onDelta('Answer without a citation.')
      handlers.onSources([], 'none_cited', 0)
      handlers.onDone()
    })
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('Ask AVA about the SEC filings')).not.toBeDisabled())
    await userEvent.type(screen.getByLabelText('Ask AVA about the SEC filings'), 'Question{enter}')
    expect(await screen.findByText('No source references were available for this answer.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /View sources/ })).not.toBeInTheDocument()
  })

  it('prevents duplicate submissions while active', async () => {
    mockedStream.mockImplementation(() => new Promise<void>(() => undefined))
    render(<App />)
    const input = screen.getByLabelText('Ask AVA about the SEC filings')
    await waitFor(() => expect(input).not.toBeDisabled())
    await userEvent.type(input, 'Question{enter}')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(mockedStream).toHaveBeenCalledTimes(1)
  })

  it('persists appearance selection through Settings', async () => {
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([])
    mockedListMessages.mockResolvedValue([])
    mockedCreateConversation.mockResolvedValue({
      id: 'conversation-1', title: 'New conversation', memory_enabled: true, pinned: false,
      pinned_at: null, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', company_scope: [],
    })
    mockedUpdatePreferences.mockResolvedValue({ ...defaultPreferences, theme: 'dark' })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Settings' }))
    await userEvent.selectOptions(screen.getByLabelText('Appearance'), 'dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(mockedUpdatePreferences).toHaveBeenCalledWith({ theme: 'dark' })
    expect(screen.getByAltText('AVA').getAttribute('src')).toContain('ava-dark.png')
  })

  it('does not persist an empty new chat before the first question', async () => {
    const created = {
      id: 'conversation-1', title: 'New conversation', memory_enabled: true, pinned: false,
      pinned_at: null, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', company_scope: [],
    }
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([])
    mockedListMessages.mockResolvedValue([])
    mockedCreateConversation.mockResolvedValue(created)
    mockedStream.mockImplementation(async (_query, handlers) => {
      handlers.onOpen()
      handlers.onDelta('Saved answer.')
      handlers.onSources([], 'none_cited', 0)
      handlers.onDone()
    })
    render(<App />)

    const input = await screen.findByLabelText('Ask AVA about the SEC filings')
    expect(mockedCreateConversation).not.toHaveBeenCalled()
    await userEvent.click(await screen.findByRole('button', { name: 'Open conversation sidebar' }))
    await userEvent.click(screen.getByRole('button', { name: '+ New chat' }))
    expect(mockedCreateConversation).not.toHaveBeenCalled()

    await userEvent.type(input, 'What does Rivian make?{enter}')

    await waitFor(() => expect(mockedCreateConversation).toHaveBeenCalledOnce())
    expect(mockedStream).toHaveBeenCalledWith(
      'What does Rivian make?', expect.any(Object),
      { conversationId: 'conversation-1', clientTurnId: expect.any(String) },
      'AZURE_GPT_4o_2024_1120',
    )
  })

  it('keeps personalization edits local until explicitly saved', async () => {
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([])
    mockedListMessages.mockResolvedValue([])
    mockedUpdatePreferences.mockResolvedValue({ ...defaultPreferences, nickname: 'Veselin' })
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Settings' }))
    await userEvent.click(screen.getByRole('button', { name: 'Personalization' }))
    await userEvent.type(screen.getByLabelText('Nickname'), 'Veselin')
    expect(mockedUpdatePreferences).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Nickname')).toHaveValue('Veselin')

    await userEvent.click(screen.getByRole('button', { name: 'Save personalization' }))
    expect(mockedUpdatePreferences).toHaveBeenCalledWith(expect.objectContaining({ nickname: 'Veselin' }))
  })

  it('uses a lower-right gear Settings trigger that shifts for the open sidebar', async () => {
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([])
    mockedListMessages.mockResolvedValue([])
    render(<App />)

    const settings = await screen.findByRole('button', { name: 'Settings' })
    expect(settings.querySelector('svg')).toBeInTheDocument()
    expect(settings).not.toHaveClass('settings-trigger--sidebar-open')

    await userEvent.click(screen.getByRole('button', { name: 'Open conversation sidebar' }))
    expect(settings).toHaveClass('settings-trigger--sidebar-open')
  })

  it('localizes AVA controls from the saved Serbian preference', async () => {
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([])
    mockedListMessages.mockResolvedValue([])
    mockedCreateConversation.mockResolvedValue({
      id: 'conversation-1', title: 'New conversation', memory_enabled: true, pinned: false,
      pinned_at: null, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', company_scope: [],
    })
    mockedGetPreferences.mockResolvedValue({ ...defaultPreferences, language: 'sr' })

    render(<App />)

    expect(await screen.findByLabelText('Pitajte AVA o SEC izveštajima')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Podešavanja' })).toBeInTheDocument()
  })

  it('uses Serbian generation activity before the first streamed token', async () => {
    let finish: (() => void) | undefined
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([])
    mockedGetPreferences.mockResolvedValue({ ...defaultPreferences, language: 'sr' })
    mockedStream.mockImplementation((_query, handlers) => {
      handlers.onOpen()
      return new Promise<void>((resolve) => { finish = resolve })
    })
    render(<App />)

    const input = await screen.findByLabelText('Pitajte AVA o SEC izveštajima')
    await userEvent.type(input, 'Zdravo{enter}')
    expect(screen.getByRole('status', { name: /^(Razmišljam|Rezonujem|Promišljam|Tumačim|Razmatram|Analiziram|Mozgam) \(u toku\)$/ })).toBeInTheDocument()
    await act(async () => { finish?.() })
  })

  it('opens a blank workspace on refresh and loads a saved chat only when selected', async () => {
    const conversation = {
      id: 'conversation-1',
      title: 'Tesla risks',
      memory_enabled: false,
      pinned: false,
      pinned_at: null,
      created_at: '2026-08-31T00:00:00Z',
      updated_at: '2026-08-31T00:00:00Z',
    }
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([conversation])
    mockedListMessages.mockResolvedValue([
      {
        id: 'message-1', client_turn_id: 'turn-1', role: 'user', text: 'Tell me about Tesla.',
        status: 'completed', ordinal: 1, created_at: '2026-08-31T00:00:00Z',
      },
      {
        id: 'message-2', client_turn_id: 'turn-1', role: 'assistant', text: 'Saved answer.',
        status: 'completed', ordinal: 2, created_at: '2026-08-31T00:00:01Z',
        source_event: { sources: [], source_status: 'none_cited', malformed_source_count: 0 },
      },
    ])
    mockedStream.mockImplementation(async (_query, handlers) => {
      handlers.onOpen()
      handlers.onDelta('Follow-up answer.')
      handlers.onSources([], 'none_cited', 0)
      handlers.onDone()
    })
    render(<App />)

    expect(await screen.findByLabelText('Ask AVA about the SEC filings')).toBeInTheDocument()
    expect(screen.queryByText('Saved answer.')).not.toBeInTheDocument()
    expect(mockedListMessages).not.toHaveBeenCalled()
    await userEvent.click(await screen.findByRole('button', { name: 'Open conversation sidebar' }))
    await userEvent.click(screen.getByText('Tesla risks'))
    expect(await screen.findByText('Saved answer.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mark answer as helpful' }))
    expect(mockedSubmitFeedback).toHaveBeenCalledWith('conversation-1', 'message-2', 'helpful')
    expect(await screen.findByText('Feedback saved.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Ask AVA about the SEC filings'), 'What about its risks?{enter}')

    expect(mockedStream).toHaveBeenCalledWith(
      'What about its risks?',
      expect.any(Object),
      expect.objectContaining({ conversationId: 'conversation-1', clientTurnId: expect.any(String) }),
      'AZURE_GPT_4o_2024_1120',
    )
    expect(screen.getByRole('button', { name: 'Close conversation sidebar' })).toBeInTheDocument()
  })

  it('edits long-term memory from Settings instead of a per-chat toggle', async () => {
    const conversation = {
      id: 'conversation-1', title: 'New conversation', memory_enabled: false,
      pinned: false, pinned_at: null,
      created_at: '2026-08-31T00:00:00Z', updated_at: '2026-08-31T00:00:00Z',
    }
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([conversation])
    mockedListMessages.mockResolvedValue([])
    mockedCreateMemory.mockResolvedValue({
      id: 'memory-1', content: 'Use concise answers.', type: 'explicit', source_conversation_id: null,
      source_message_id: null, version: 1, created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    })
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Settings' }))
    await userEvent.click(screen.getByRole('button', { name: 'Memory' }))
    await userEvent.type(screen.getByRole('textbox', { name: /add memory/i }), 'Use concise answers.')
    await userEvent.click(screen.getByRole('button', { name: 'Add memory' }))

    expect(mockedCreateMemory).toHaveBeenCalledWith('Use concise answers.')
    expect(await screen.findByText('Saved by you')).toBeInTheDocument()
  })

  it('opens memory editing in a separate dialog', async () => {
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([])
    mockedListMessages.mockResolvedValue([])
    mockedListMemory.mockResolvedValue([{
      id: 'memory-1', content: 'Compare future goals and plans.', type: 'explicit',
      source_conversation_id: null, source_message_id: null, version: 1,
      created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    }])
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Settings' }))
    await userEvent.click(screen.getByRole('button', { name: 'Memory' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }))

    const editor = screen.getByRole('dialog', { name: 'Edit memory' })
    expect(editor).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Edit memory' })).toHaveValue('Compare future goals and plans.')
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('downloads the authenticated conversation export from history', async () => {
    const conversation = {
      id: 'conversation-1', title: 'Saved research', memory_enabled: false,
      pinned: false, pinned_at: null,
      created_at: '2026-08-31T00:00:00Z', updated_at: '2026-08-31T00:00:00Z',
    }
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([conversation])
    mockedListMessages.mockResolvedValue([])
    const createObjectURL = vi.fn(() => 'blob:ava-export')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Open conversation sidebar' }))
    await userEvent.click(screen.getByRole('button', { name: 'Export my data' }))

    await waitFor(() => expect(mockedExportConversations).toHaveBeenCalledOnce())
    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:ava-export')
  })

  it('uploads a source into the active chat and lists it in chat Sources', async () => {
    const conversation = {
      id: 'conversation-1', title: 'Architecture', memory_enabled: false,
      pinned: false, pinned_at: null,
      created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    }
    const uploaded = {
      id: 'document-1', conversation_id: conversation.id, filename: 'architecture.txt',
      media_type: 'text/plain' as const, size_bytes: 18, status: 'ready' as const,
      page_count: null, token_count: 3, chunk_count: 1,
      created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    }
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([conversation])
    mockedListMessages.mockResolvedValue([])
    mockedUploadDocument.mockResolvedValue(uploaded)
    mockedListDocuments.mockResolvedValue([uploaded])
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Open conversation sidebar' }))
    await userEvent.click(screen.getByRole('button', { name: 'Architecture' }))
    const fileInput = await screen.findByLabelText('Choose a PDF or text source')
    const file = new File(['architecture notes'], 'architecture.txt', { type: 'text/plain' })
    await userEvent.upload(fileInput, file)

    await waitFor(() => expect(mockedUploadDocument).toHaveBeenCalledWith('conversation-1', file))
    expect(await screen.findByText('architecture.txt is ready in this chat.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sources (1)' }))
    expect(await screen.findByRole('dialog', { name: 'Sources' })).toBeInTheDocument()
    expect(screen.getByText('architecture.txt')).toBeInTheDocument()
    expect(mockedListDocuments).toHaveBeenCalledWith('conversation-1')
  })

  it('opens the same chat action menu by button or right-click and persists pinning', async () => {
    const conversation = {
      id: 'conversation-1', title: 'Tesla research', memory_enabled: false,
      pinned: false, pinned_at: null,
      created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    }
    const pinned = { ...conversation, pinned: true, pinned_at: '2026-09-01T00:01:00Z' }
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValueOnce([conversation]).mockResolvedValueOnce([pinned])
    mockedListMessages.mockResolvedValue([])
    mockedUpdateConversation.mockResolvedValue(pinned)
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: 'Open conversation sidebar' }))
    const row = screen.getByRole('button', { name: 'Tesla research' })
    fireEvent.contextMenu(row.closest('li') as HTMLElement)
    expect(screen.getByRole('menu', { name: 'Actions for Tesla research' })).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    await waitFor(() => expect(row).toHaveFocus())

    await userEvent.click(screen.getByRole('button', { name: 'Actions for Tesla research' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Pin' }))
    expect(mockedUpdateConversation).toHaveBeenCalledWith('conversation-1', { pinned: true })
    expect(await screen.findByRole('heading', { name: 'Pinned' })).toBeInTheDocument()
  })
})

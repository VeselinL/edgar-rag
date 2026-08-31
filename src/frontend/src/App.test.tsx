import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { getAuthSession, signOut } from './api/auth'
import { streamChat } from './api/chatStream'
import {
  conversationHistoryEnabled,
  createConversation,
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

vi.mock('./api/conversations', () => ({
  conversationHistoryEnabled: vi.fn(async () => false),
  createConversation: vi.fn(),
  deleteAllConversations: vi.fn(),
  deleteConversation: vi.fn(),
  listConversations: vi.fn(),
  listMessages: vi.fn(),
  submitFeedback: vi.fn(),
  updateConversation: vi.fn(),
}))

const mockedStream = vi.mocked(streamChat)
const mockedAuthSession = vi.mocked(getAuthSession)
const mockedSignOut = vi.mocked(signOut)
const mockedHistoryEnabled = vi.mocked(conversationHistoryEnabled)
const mockedCreateConversation = vi.mocked(createConversation)
const mockedListConversations = vi.mocked(listConversations)
const mockedListMessages = vi.mocked(listMessages)
const mockedSubmitFeedback = vi.mocked(submitFeedback)
const mockedUpdateConversation = vi.mocked(updateConversation)
type Handlers = Parameters<typeof streamChat>[1]

describe('App', () => {
  beforeEach(() => {
    mockedStream.mockReset()
    mockedAuthSession.mockReset()
    mockedAuthSession.mockResolvedValue({ mode: 'none', authenticated: true })
    mockedSignOut.mockReset()
    mockedSignOut.mockResolvedValue(undefined)
    mockedHistoryEnabled.mockReset()
    mockedHistoryEnabled.mockResolvedValue(false)
    mockedCreateConversation.mockReset()
    mockedListConversations.mockReset()
    mockedListMessages.mockReset()
    mockedSubmitFeedback.mockReset()
    mockedSubmitFeedback.mockResolvedValue(undefined)
    mockedUpdateConversation.mockReset()
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
    expect(mockedStream).toHaveBeenCalledWith('What does Tesla do?', expect.any(Object))
    expect(screen.getByLabelText('AVA is finding evidence and preparing an answer.')).toBeInTheDocument()

    act(() => handlers?.onDelta('Tesla builds electric vehicles.'))
    expect(screen.queryByLabelText('AVA is finding evidence and preparing an answer.')).not.toBeInTheDocument()
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

  it('renders narrative and structured table sources without raw IDs', async () => {
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
    const button = await screen.findByRole('button', { name: 'View sources (2)' })
    await userEvent.click(button)
    expect(screen.getByText('Complete narrative evidence.')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '2025' })).toBeInTheDocument()
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

  it('persists explicit theme selection', async () => {
    render(<App />)
    const toggle = screen.getByRole('button', { name: 'Switch to dark theme' })
    await userEvent.click(toggle)
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('ava-theme')).toBe('dark')
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
    expect(screen.getByAltText('AVA').getAttribute('src')).toContain('ava-dark.png')
  })

  it('resumes saved messages and sends idempotent conversation turn IDs', async () => {
    const conversation = {
      id: 'conversation-1',
      title: 'Tesla risks',
      memory_enabled: false,
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

    expect(await screen.findByText('Saved answer.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mark answer as helpful' }))
    expect(mockedSubmitFeedback).toHaveBeenCalledWith('conversation-1', 'message-2', 'helpful')
    expect(await screen.findByText('Feedback saved.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Ask AVA about the SEC filings'), 'What about its risks?{enter}')

    expect(mockedStream).toHaveBeenCalledWith(
      'What about its risks?',
      expect.any(Object),
      expect.objectContaining({ conversationId: 'conversation-1', clientTurnId: expect.any(String) }),
    )
    expect(screen.getByRole('button', { name: 'History' })).toBeInTheDocument()
  })

  it('keeps long-term memory opt-in and exposes an explicit toggle', async () => {
    const conversation = {
      id: 'conversation-1', title: 'New conversation', memory_enabled: false,
      created_at: '2026-08-31T00:00:00Z', updated_at: '2026-08-31T00:00:00Z',
    }
    mockedHistoryEnabled.mockResolvedValue(true)
    mockedListConversations.mockResolvedValue([conversation])
    mockedListMessages.mockResolvedValue([])
    mockedUpdateConversation.mockResolvedValue({ ...conversation, memory_enabled: true })
    render(<App />)

    const toggle = await screen.findByRole('button', { name: 'Memory off' })
    await userEvent.click(toggle)

    expect(mockedUpdateConversation).toHaveBeenCalledWith('conversation-1', { memory_enabled: true })
    expect(await screen.findByRole('button', { name: 'Memory on' })).toHaveAttribute('aria-pressed', 'true')
  })
})

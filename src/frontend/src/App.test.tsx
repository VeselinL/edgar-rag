import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { streamChat } from './api/chatStream'
import type { Source } from './types'

vi.mock('./api/chatStream', () => ({
  ChatStreamError: class ChatStreamError extends Error {},
  streamChat: vi.fn(),
}))

const mockedStream = vi.mocked(streamChat)
type Handlers = Parameters<typeof streamChat>[1]

describe('App', () => {
  beforeEach(() => {
    mockedStream.mockReset()
    localStorage.clear()
    document.documentElement.dataset.theme = 'light'
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
      handlers.onSources(sources, false, 0)
      handlers.onDone()
    })
    render(<App />)
    await userEvent.type(screen.getByLabelText('Ask AVA about the SEC filings'), 'Show evidence{enter}')
    const button = await screen.findByRole('button', { name: 'View sources (2)' })
    await userEvent.click(button)
    expect(screen.getByText('Complete narrative evidence.')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '2025' })).toBeInTheDocument()
    expect(screen.queryByText(/CHUNK-/)).not.toBeInTheDocument()
  })

  it('prevents duplicate submissions while active', async () => {
    mockedStream.mockImplementation(() => new Promise<void>(() => undefined))
    render(<App />)
    const input = screen.getByLabelText('Ask AVA about the SEC filings')
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
  })
})

import { useEffect, useRef, useState } from 'react'
import { ChatStreamError, streamChat } from './api/chatStream'
import { Composer } from './components/Composer'
import { Conversation } from './components/Conversation'
import { EmptyState } from './components/EmptyState'
import { Header } from './components/Header'
import { useTheme } from './hooks/useTheme'
import type { AssistantMessage, ChatMessage } from './types'

const PRE_TOKEN_ERROR = 'The filing-analysis service is temporarily unavailable. Please retry shortly.'
const MID_STREAM_ERROR = 'The response was interrupted. Please try again.'

export default function App() {
  const { theme, toggleTheme } = useTheme()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [active, setActive] = useState(false)
  const [validation, setValidation] = useState('')
  const controller = useRef<AbortController | null>(null)
  const idSequence = useRef(0)

  useEffect(() => () => controller.current?.abort(), [])

  const updateAssistant = (id: string, update: (message: AssistantMessage) => AssistantMessage) => {
    setMessages((current) => current.map((message) =>
      message.role === 'assistant' && message.id === id ? update(message) : message,
    ))
  }

  const submit = async () => {
    const query = draft
    if (!query.trim()) {
      setValidation('Enter a question to continue.')
      return
    }
    if (active) return
    setValidation('')
    setActive(true)
    idSequence.current += 1
    const requestId = idSequence.current
    const userId = `user-${requestId}`
    const assistantId = `assistant-${requestId}`
    const newAssistant: AssistantMessage = {
      id: assistantId,
      role: 'assistant',
      text: '',
      state: 'waiting_for_first_token',
      sources: null,
      sourceStatus: 'none_cited',
      malformedSourceCount: 0,
    }
    setMessages((current) => [
      ...current,
      { id: userId, role: 'user', text: query },
      newAssistant,
    ])

    const abortController = new AbortController()
    controller.current = abortController
    let opened = false
    let receivedText = false
    try {
      await streamChat(query, {
        signal: abortController.signal,
        onOpen: () => {
          opened = true
          setDraft('')
        },
        onDelta: (text) => {
          receivedText = true
          updateAssistant(assistantId, (message) => ({
            ...message,
            state: 'streaming',
            text: message.text + text,
          }))
        },
        onSources: (sources, sourceStatus, malformedSourceCount) => {
          updateAssistant(assistantId, (message) => ({
            ...message,
            sources,
            sourceStatus,
            malformedSourceCount,
          }))
        },
        onDone: () => {
          updateAssistant(assistantId, (message) => ({ ...message, state: 'completed' }))
        },
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      const message = error instanceof ChatStreamError ? error.message : PRE_TOKEN_ERROR
      updateAssistant(assistantId, (assistant) => ({
        ...assistant,
        state: 'error',
        error: receivedText ? MID_STREAM_ERROR : message,
      }))
      if (!opened) setDraft(query)
    } finally {
      if (controller.current === abortController) controller.current = null
      setActive(false)
    }
  }

  const isEmpty = messages.length === 0
  return (
    <div className="app-shell">
      <Header theme={theme} onToggleTheme={toggleTheme} />
      <main className={`main ${isEmpty ? 'main--empty' : ''}`}>
        {isEmpty ? <EmptyState /> : <Conversation messages={messages} />}
        <Composer
          value={draft}
          active={active}
          validationMessage={validation}
          onChange={(value) => {
            setDraft(value)
            if (validation) setValidation('')
          }}
          onSubmit={() => void submit()}
        />
      </main>
    </div>
  )
}

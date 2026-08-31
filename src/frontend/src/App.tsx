import { useEffect, useRef, useState } from 'react'
import { ChatStreamError, streamChat } from './api/chatStream'
import { getAuthSession, signInUrl, signOut } from './api/auth'
import {
  conversationHistoryEnabled,
  createConversation,
  deleteAllConversations,
  deleteConversation,
  listConversations,
  listMessages,
  updateConversation,
} from './api/conversations'
import { Composer } from './components/Composer'
import { Conversation } from './components/Conversation'
import { EmptyState } from './components/EmptyState'
import { Header } from './components/Header'
import { HistoryPanel } from './components/HistoryPanel'
import { useTheme } from './hooks/useTheme'
import type { AssistantMessage, ChatMessage, ConversationSummary, PersistedMessage } from './types'

const PRE_TOKEN_ERROR = 'The filing-analysis service is temporarily unavailable. Please retry shortly.'
const MID_STREAM_ERROR = 'The response was interrupted. Please try again.'

export default function App() {
  const { theme, toggleTheme } = useTheme()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [active, setActive] = useState(false)
  const [validation, setValidation] = useState('')
  const [historyEnabled, setHistoryEnabled] = useState(false)
  const [historyInitializing, setHistoryInitializing] = useState(true)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [authenticationRequired, setAuthenticationRequired] = useState(false)
  const [authenticated, setAuthenticated] = useState(true)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [currentConversation, setCurrentConversation] = useState<ConversationSummary | null>(null)
  const controller = useRef<AbortController | null>(null)
  const idSequence = useRef(0)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const auth = await getAuthSession()
        if (cancelled) return
        setAuthenticationRequired(auth.mode === 'oidc')
        setAuthenticated(auth.authenticated)
        if (!auth.authenticated) return
        if (!await conversationHistoryEnabled() || cancelled) return
        setHistoryEnabled(true)
        const saved = await listConversations()
        if (cancelled) return
        const selected = saved[0] ?? await createConversation(false)
        const next = saved.length ? saved : [selected]
        const stored = await listMessages(selected.id)
        if (cancelled) return
        setConversations(next)
        setCurrentConversation(selected)
        setMessages(storedMessages(stored))
      } catch {
        // The explicit stateless path remains usable when history is disabled
        // or its separate persistence service is unavailable.
      } finally {
        if (!cancelled) setHistoryInitializing(false)
      }
    })()
    return () => {
      cancelled = true
      controller.current?.abort()
    }
  }, [])

  const storedMessages = (stored: PersistedMessage[]): ChatMessage[] => stored
    .filter((message) => message.role === 'user' || message.status !== 'in_progress')
    .map((message) => {
      if (message.role === 'user') return { id: message.id, role: 'user', text: message.text }
      const sourceEvent = message.source_event
      return {
        id: message.id,
        role: 'assistant',
        text: message.text,
        state: message.status === 'failed' ? 'error' : 'completed',
        sources: sourceEvent?.sources ?? [],
        sourceStatus: sourceEvent?.source_status ?? 'none_cited',
        malformedSourceCount: sourceEvent?.malformed_source_count ?? 0,
        ...(message.status === 'failed' ? { error: MID_STREAM_ERROR } : {}),
      }
    })

  const refreshConversations = async () => {
    if (!historyEnabled) return
    setConversations(await listConversations())
  }

  const selectConversation = async (conversation: ConversationSummary) => {
    if (active) return
    setCurrentConversation(conversation)
    setMessages(storedMessages(await listMessages(conversation.id)))
    setHistoryOpen(false)
  }

  const newConversation = async () => {
    if (!historyEnabled || active) return
    const created = await createConversation(false)
    setCurrentConversation(created)
    setConversations((current) => [created, ...current])
    setMessages([])
    setHistoryOpen(false)
  }

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
    if (historyInitializing) {
      setValidation('AVA is preparing conversation history. Please try again shortly.')
      return
    }
    setValidation('')
    setActive(true)
    idSequence.current += 1
    const requestId = idSequence.current
    const clientTurnId = crypto.randomUUID()
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
      const handlers: Parameters<typeof streamChat>[1] = {
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
      }
      if (currentConversation) {
        await streamChat(query, handlers, {
          conversationId: currentConversation.id,
          clientTurnId,
        })
      } else {
        await streamChat(query, handlers)
      }
      await refreshConversations()
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
  const needsSignIn = authenticationRequired && !authenticated
  return (
    <div className="app-shell">
      <Header
        theme={theme}
        onToggleTheme={toggleTheme}
        historyEnabled={historyEnabled}
        memoryEnabled={currentConversation?.memory_enabled}
        onToggleHistory={() => setHistoryOpen((value) => !value)}
        onNewConversation={() => void newConversation()}
        onToggleMemory={() => {
          if (!currentConversation) return
          void updateConversation(currentConversation.id, { memory_enabled: !currentConversation.memory_enabled })
            .then((updated) => {
              setCurrentConversation(updated)
              setConversations((items) => items.map((item) => item.id === updated.id ? updated : item))
            })
        }}
        authenticationRequired={authenticationRequired}
        authenticated={authenticated}
        onSignIn={() => window.location.assign(signInUrl())}
        onSignOut={() => {
          void signOut().then(() => {
            setAuthenticated(false)
            setHistoryEnabled(false)
            setCurrentConversation(null)
            setConversations([])
            setMessages([])
          })
        }}
      />
      <div className="workspace">
        {historyEnabled && historyOpen && (
          <HistoryPanel
            conversations={conversations}
            activeId={currentConversation?.id}
            onSelect={(conversation) => void selectConversation(conversation)}
            onRename={(conversation) => {
              const title = window.prompt('Conversation name', conversation.title)
              if (!title?.trim()) return
              void updateConversation(conversation.id, { title }).then((updated) => {
                setConversations((items) => items.map((item) => item.id === updated.id ? updated : item))
                if (currentConversation?.id === updated.id) setCurrentConversation(updated)
              })
            }}
            onDelete={(conversation) => {
              void deleteConversation(conversation.id).then(async () => {
                const remaining = conversations.filter((item) => item.id !== conversation.id)
                setConversations(remaining)
                if (currentConversation?.id === conversation.id) {
                  if (remaining[0]) await selectConversation(remaining[0])
                  else await newConversation()
                }
              })
            }}
            onDeleteAll={() => {
              if (!window.confirm('Delete all saved conversations? This cannot be undone.')) return
              void deleteAllConversations().then(async () => {
                const created = await createConversation(false)
                setConversations([created])
                setCurrentConversation(created)
                setMessages([])
                setHistoryOpen(false)
              })
            }}
            onClose={() => setHistoryOpen(false)}
          />
        )}
        <main className={`main ${isEmpty ? 'main--empty' : ''}`}>
          {needsSignIn ? (
            <section className="auth-prompt" aria-labelledby="auth-heading">
              <h1 id="auth-heading">Sign in to AVA</h1>
              <p>Your filing research and saved conversations stay isolated to your verified account.</p>
              <button type="button" className="auth-prompt__button" onClick={() => window.location.assign(signInUrl())}>
                Continue to sign in
              </button>
            </section>
          ) : (
            <>
              {isEmpty ? <EmptyState theme={theme} /> : <Conversation messages={messages} theme={theme} />}
              <Composer
                value={draft}
                active={active || historyInitializing}
                validationMessage={validation}
                onChange={(value) => {
                  setDraft(value)
                  if (validation) setValidation('')
                }}
                onSubmit={() => void submit()}
              />
            </>
          )}
        </main>
      </div>
    </div>
  )
}

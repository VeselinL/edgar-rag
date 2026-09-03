import { useEffect, useRef, useState } from 'react'
import { ChatStreamError, streamChat } from './api/chatStream'
import { getAuthSession, signInUrl, signOut } from './api/auth'
import { deleteDocument, listDocuments, uploadDocument } from './api/documents'
import {
  conversationHistoryEnabled,
  createConversation,
  deleteAllConversations,
  deleteConversation,
  exportConversations,
  listConversations,
  listMessages,
  submitFeedback,
  updateConversation,
} from './api/conversations'
import { Composer } from './components/Composer'
import { ChatSourcesPanel } from './components/ChatSourcesPanel'
import { ConversationSidebar } from './components/ConversationSidebar'
import { Conversation } from './components/Conversation'
import { EmptyState } from './components/EmptyState'
import { Header } from './components/Header'
import { useTheme } from './hooks/useTheme'
import type { AssistantMessage, ChatDocument, ChatMessage, ConversationSummary, PersistedMessage } from './types'

const PRE_TOKEN_ERROR = 'The filing-analysis service is temporarily unavailable. Please retry shortly.'
const MID_STREAM_ERROR = 'The response was interrupted. Please try again.'
const DEFAULT_MODEL = 'AZURE_GPT_4o_2024_1120'
const GENERATION_ACTIVITIES = ['Thinking', 'Reasoning', 'Cogitating', 'Cerebrating', 'Contemplating', 'Pondering', 'Ruminating', 'Sleuthing'] as const

export default function App() {
  const { theme, toggleTheme } = useTheme()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [active, setActive] = useState(false)
  const [validation, setValidation] = useState('')
  const [historyEnabled, setHistoryEnabled] = useState(false)
  const [historyInitializing, setHistoryInitializing] = useState(true)
  const [startupError, setStartupError] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [authenticationRequired, setAuthenticationRequired] = useState(false)
  const [authenticated, setAuthenticated] = useState(true)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [currentConversation, setCurrentConversation] = useState<ConversationSummary | null>(null)
  const [documents, setDocuments] = useState<ChatDocument[]>([])
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [sourcesLoading, setSourcesLoading] = useState(false)
  const [sourcesError, setSourcesError] = useState('')
  const [uploadStatus, setUploadStatus] = useState('')
  const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null)
  const [selectedModel, setSelectedModel] = useState(() => window.localStorage.getItem('ava-model') ?? DEFAULT_MODEL)
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
        if (!cancelled) {
          setStartupError('AVA could not initialize its account and conversation services.')
        }
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
        feedbackEligible: message.status === 'completed',
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
    setDocuments([])
    setSourcesOpen(false)
    setUploadStatus('')
  }

  const newConversation = async () => {
    if (!historyEnabled || active) return
    const created = await createConversation(false)
    setCurrentConversation(created)
    setConversations((current) => [created, ...current])
    setMessages([])
    setDocuments([])
    setSourcesOpen(false)
    setUploadStatus('')
  }

  const openSources = async () => {
    if (!currentConversation) return
    setSourcesOpen(true)
    setSourcesLoading(true)
    setSourcesError('')
    try {
      setDocuments(await listDocuments(currentConversation.id))
    } catch (error) {
      setSourcesError(error instanceof Error ? error.message : 'Sources could not be loaded.')
    } finally {
      setSourcesLoading(false)
    }
  }

  const addDocument = async (file: File) => {
    if (!currentConversation || active) return
    setUploadStatus(`Adding ${file.name}…`)
    setSourcesError('')
    try {
      const uploaded = await uploadDocument(currentConversation.id, file)
      setDocuments((items) => [uploaded, ...items.filter((item) => item.id !== uploaded.id)])
      setUploadStatus(`${file.name} is ready in this chat.`)
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : 'The source could not be uploaded.')
    }
  }

  const removeDocument = async (document: ChatDocument) => {
    if (!currentConversation || deletingDocumentId) return
    if (!window.confirm(`Delete ${document.filename} from this chat?`)) return
    setDeletingDocumentId(document.id)
    setSourcesError('')
    try {
      await deleteDocument(currentConversation.id, document.id)
      setDocuments((items) => items.filter((item) => item.id !== document.id))
      setUploadStatus(`${document.filename} was removed.`)
    } catch (error) {
      setSourcesError(error instanceof Error ? error.message : 'The source could not be deleted.')
    } finally {
      setDeletingDocumentId(null)
    }
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
      activity: GENERATION_ACTIVITIES[Math.floor(Math.random() * GENERATION_ACTIVITIES.length)],
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
        onStatus: (activity) => {
          updateAssistant(assistantId, (message) => ({ ...message, activity }))
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
        const conversation = {
          conversationId: currentConversation.id,
          clientTurnId,
        }
        await streamChat(query, handlers, conversation, selectedModel)
      } else {
        await streamChat(query, handlers, undefined, selectedModel)
      }
      if (currentConversation) {
        setMessages(storedMessages(await listMessages(currentConversation.id)))
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
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((value) => !value)}
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
        {historyEnabled && sidebarOpen && (
          <ConversationSidebar
            conversations={conversations}
            activeId={currentConversation?.id}
            memoryEnabled={currentConversation?.memory_enabled ?? false}
            companyScope={currentConversation?.company_scope ?? []}
            onToggleCompany={(ticker) => {
              if (!currentConversation) return
              const currentScope = currentConversation.company_scope ?? []
              const nextScope = ticker === 'ALL'
                ? []
                : currentScope.includes(ticker)
                  ? currentScope.filter((value) => value !== ticker)
                  : [...currentScope, ticker]
              void updateConversation(currentConversation.id, { company_scope: nextScope }).then((updated) => {
                setCurrentConversation(updated)
                setConversations((items) => items.map((item) => item.id === updated.id ? updated : item))
              })
            }}
            model={selectedModel}
            onModelChange={(model) => {
              setSelectedModel(model)
              window.localStorage.setItem('ava-model', model)
            }}
            onNew={() => void newConversation()}
            onToggleMemory={() => {
              if (!currentConversation) return
              void updateConversation(currentConversation.id, { memory_enabled: !currentConversation.memory_enabled })
                .then((updated) => {
                  setCurrentConversation(updated)
                  setConversations((items) => items.map((item) => item.id === updated.id ? updated : item))
                })
            }}
            onSelect={(conversation) => void selectConversation(conversation)}
            onPin={(conversation) => {
              void updateConversation(conversation.id, { pinned: !conversation.pinned }).then(async (updated) => {
                if (currentConversation?.id === updated.id) setCurrentConversation(updated)
                setConversations(await listConversations())
              })
            }}
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
              })
            }}
            onExport={() => {
              void exportConversations()
                .then((blob) => {
                  const url = URL.createObjectURL(blob)
                  const link = document.createElement('a')
                  link.href = url
                  link.download = 'ava-conversations.json'
                  link.click()
                  URL.revokeObjectURL(url)
                })
                .catch(() => window.alert('Your data could not be exported. Please try again.'))
            }}
            onClose={() => setSidebarOpen(false)}
          />
        )}
        {historyEnabled && sourcesOpen && (
          <ChatSourcesPanel
            documents={documents}
            loading={sourcesLoading}
            error={sourcesError}
            deletingId={deletingDocumentId}
            onDelete={(document) => void removeDocument(document)}
            onClose={() => setSourcesOpen(false)}
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
          ) : startupError ? (
            <section className="auth-prompt" aria-labelledby="startup-error-heading">
              <h1 id="startup-error-heading">AVA is temporarily unavailable</h1>
              <p>{startupError} Check the connection, then try again.</p>
              <button type="button" className="auth-prompt__button" onClick={() => window.location.reload()}>
                Retry
              </button>
            </section>
          ) : (
            <>
              {isEmpty ? <EmptyState theme={theme} /> : (
                <Conversation
                  messages={messages}
                  theme={theme}
                  onFeedback={(messageId, value) => {
                    if (!currentConversation) return
                    updateAssistant(messageId, (message) => ({ ...message, feedback: 'submitting' }))
                    void submitFeedback(currentConversation.id, messageId, value)
                      .then(() => updateAssistant(messageId, (message) => ({ ...message, feedback: value })))
                      .catch(() => updateAssistant(messageId, (message) => ({ ...message, feedback: 'error' })))
                  }}
                />
              )}
              <Composer
                value={draft}
                active={active || historyInitializing}
                validationMessage={validation}
                onChange={(value) => {
                  setDraft(value)
                  if (validation) setValidation('')
                }}
                onSubmit={() => void submit()}
                uploadsEnabled={historyEnabled && Boolean(currentConversation)}
                uploadStatus={uploadStatus}
                sourceCount={documents.length}
                onUpload={(file) => void addDocument(file)}
                onOpenSources={() => void openSources()}
              />
            </>
          )}
        </main>
      </div>
    </div>
  )
}

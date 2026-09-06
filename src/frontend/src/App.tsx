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
import { SettingsModal } from './components/SettingsModal'
import { useTheme } from './hooks/useTheme'
import { createMemory, deleteMemory, getPreferences, listMemory, updateMemory, updatePreferences } from './api/settings'
import type { AssistantMessage, ChatDocument, ChatMessage, ConversationSummary, MemoryItem, PersistedMessage, UserPreferences } from './types'

const UI_COPY = {
  en: {
    preTokenError: 'The filing-analysis service is temporarily unavailable. Please retry shortly.',
    interrupted: 'The response was interrupted. Please try again.',
    initialization: 'AVA could not initialize its account and conversation services.',
    sourcesUnavailable: 'Sources could not be loaded.', sourceUploadFailed: 'The source could not be uploaded.',
    sourceDeleteFailed: 'The source could not be deleted.', adding: 'Adding', ready: 'is ready in this chat.', removed: 'was removed.',
    enterQuestion: 'Enter a question to continue.', historyPreparing: 'AVA is preparing conversation history. Please try again shortly.',
    deleteSource: 'Delete {name} from this chat?', deleteChats: 'Delete all saved conversations? This cannot be undone.',
    exportFailed: 'Your data could not be exported. Please try again.', conversationName: 'Conversation name',
    signInTitle: 'Sign in to AVA', signInBody: 'Your filing research and saved conversations stay isolated to your verified account.',
    signIn: 'Continue to sign in', unavailable: 'AVA is temporarily unavailable', retry: 'Retry', checkConnection: 'Check the connection, then try again.',
  },
  sr: {
    preTokenError: 'Usluga za analizu SEC prijava je privremeno nedostupna. Pokušajte ponovo uskoro.',
    interrupted: 'Odgovor je prekinut. Pokušajte ponovo.',
    initialization: 'AVA nije mogao da pokrene usluge naloga i istorije razgovora.',
    sourcesUnavailable: 'Izvori nisu mogli da se učitaju.', sourceUploadFailed: 'Izvor nije mogao da se otpremi.',
    sourceDeleteFailed: 'Izvor nije mogao da se obriše.', adding: 'Dodajem', ready: 'je spreman u ovom razgovoru.', removed: 'je uklonjen.',
    enterQuestion: 'Unesite pitanje da biste nastavili.', historyPreparing: 'AVA priprema istoriju razgovora. Pokušajte ponovo uskoro.',
    deleteSource: 'Obrisati {name} iz ovog razgovora?', deleteChats: 'Obrisati sve sačuvane razgovore? Ova radnja se ne može poništiti.',
    exportFailed: 'Vaši podaci nisu mogli da se izvezu. Pokušajte ponovo.', conversationName: 'Naziv razgovora',
    signInTitle: 'Prijavite se u AVA', signInBody: 'Vaša istraživanja prijava i sačuvani razgovori ostaju izdvojeni u potvrđenom nalogu.',
    signIn: 'Nastavite sa prijavom', unavailable: 'AVA je privremeno nedostupan', retry: 'Pokušajte ponovo', checkConnection: 'Proverite vezu, a zatim pokušajte ponovo.',
  },
} as const
const GENERATION_ACTIVITIES = {
  en: ['Thinking', 'Reasoning', 'Cogitating', 'Cerebrating', 'Contemplating', 'Pondering', 'Ruminating', 'Sleuthing'],
  sr: ['Razmišljam', 'Rezonujem', 'Promišljam', 'Tumačim', 'Razmatram', 'Analiziram', 'Mozgam'],
} as const
const DEFAULT_PREFERENCES: UserPreferences = {
  nickname: '', warmth: 'balanced', enthusiasm: 'balanced', emoji_use: 'off',
  custom_instructions: '', language: 'en', model: 'AZURE_GPT_4o_2024_1120', theme: 'system',
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z" />
      <path d="M19.4 13.5a7.8 7.8 0 0 0 .1-1.5 7.8 7.8 0 0 0-.1-1.5l2-1.5-2-3.4-2.4 1a8.3 8.3 0 0 0-2.5-1.5L14.2 2h-4l-.4 3.1a8.3 8.3 0 0 0-2.5 1.5l-2.4-1-2 3.4 2 1.5a7.8 7.8 0 0 0-.1 1.5c0 .5 0 1 .1 1.5l-2 1.5 2 3.4 2.4-1a8.3 8.3 0 0 0 2.5 1.5l.4 3.1h4l.4-3.1a8.3 8.3 0 0 0 2.5-1.5l2.4 1 2-3.4-2-1.5Z" />
    </svg>
  )
}

function SidebarIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  )
}

export default function App() {
  const { theme, setThemePreference } = useTheme()
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
  const [pendingCompanyScope, setPendingCompanyScope] = useState<string[]>([])
  const [documents, setDocuments] = useState<ChatDocument[]>([])
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [sourcesLoading, setSourcesLoading] = useState(false)
  const [sourcesError, setSourcesError] = useState('')
  const [uploadStatus, setUploadStatus] = useState('')
  const [uploading, setUploading] = useState(false)
  const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null)
  const [preferences, setPreferences] = useState<UserPreferences>(DEFAULT_PREFERENCES)
  const [memory, setMemory] = useState<MemoryItem[]>([])
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsButton = useRef<HTMLButtonElement>(null)
  const controller = useRef<AbortController | null>(null)
  const idSequence = useRef(0)
  const copy = UI_COPY[preferences.language]

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
        const savedPreferences = await getPreferences()
        if (cancelled) return
        setPreferences(savedPreferences)
        setThemePreference(savedPreferences.theme)
        const saved = await listConversations()
        if (cancelled) return
        if (cancelled) return
        setConversations(saved)
        setCurrentConversation(null)
        setPendingCompanyScope([])
        setMessages([])
      } catch {
        if (!cancelled) {
          setStartupError(UI_COPY.en.initialization)
        }
      } finally {
        if (!cancelled) setHistoryInitializing(false)
      }
    })()
    return () => {
      cancelled = true
      controller.current?.abort()
    }
  }, [setThemePreference])

  const savePreferences = async (values: Partial<UserPreferences>) => {
    const saved = await updatePreferences(values)
    setPreferences(saved)
    if (values.theme) setThemePreference(saved.theme)
  }

  const openSettings = async () => {
    setSettingsOpen(true)
    setMemoryLoading(true)
    try { setMemory(await listMemory()) } finally { setMemoryLoading(false) }
  }

  const closeSettings = () => {
    setSettingsOpen(false)
    window.setTimeout(() => settingsButton.current?.focus(), 0)
  }

  const addMemory = async (content: string) => {
    const created = await createMemory(content)
    setMemory((items) => [created, ...items])
  }

  const saveMemory = async (id: string, content: string) => {
    const updated = await updateMemory(id, content)
    setMemory((items) => items.map((item) => item.id === id ? updated : item))
  }

  const removeMemory = async (id: string) => {
    await deleteMemory(id)
    setMemory((items) => items.filter((item) => item.id !== id))
  }

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
        ...(message.status === 'failed' ? { error: copy.interrupted } : {}),
      }
    })

  const refreshConversations = async () => {
    if (!historyEnabled) return
    setConversations(await listConversations())
  }

  const selectConversation = async (conversation: ConversationSummary) => {
    if (active) return
    setCurrentConversation(conversation)
    setPendingCompanyScope([])
    setMessages(storedMessages(await listMessages(conversation.id)))
    setDocuments([])
    setSourcesOpen(false)
    setUploadStatus('')
  }

  const newConversation = async () => {
    if (!historyEnabled || active) return
    setCurrentConversation(null)
    setPendingCompanyScope([])
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
    } catch {
      setSourcesError(copy.sourcesUnavailable)
    } finally {
      setSourcesLoading(false)
    }
  }

  const addDocument = async (file: File) => {
    if (!historyEnabled || active || uploading) return
    setUploadStatus(`${copy.adding} ${file.name}…`)
    setSourcesError('')
    setUploading(true)
    let conversation = currentConversation
    let pendingConversation = false
    try {
      if (!conversation) {
        conversation = await createConversation(pendingCompanyScope)
        pendingConversation = true
      }
      const uploaded = await uploadDocument(conversation.id, file)
      if (pendingConversation) {
        setCurrentConversation(conversation)
        setPendingCompanyScope([])
        setConversations((items) => [conversation!, ...items])
      }
      setDocuments((items) => [uploaded, ...items.filter((item) => item.id !== uploaded.id)])
      setUploadStatus(`${file.name} ${copy.ready}`)
    } catch {
      if (pendingConversation && conversation) {
        try { await deleteConversation(conversation.id) } catch { /* Best-effort cleanup of an empty pending chat. */ }
      }
      setUploadStatus(copy.sourceUploadFailed)
    } finally {
      setUploading(false)
    }
  }

  const removeDocument = async (document: ChatDocument) => {
    if (!currentConversation || deletingDocumentId) return
    if (!window.confirm(copy.deleteSource.replace('{name}', document.filename))) return
    setDeletingDocumentId(document.id)
    setSourcesError('')
    try {
      await deleteDocument(currentConversation.id, document.id)
      setDocuments((items) => items.filter((item) => item.id !== document.id))
      setUploadStatus(`${document.filename} ${copy.removed}`)
    } catch {
      setSourcesError(copy.sourceDeleteFailed)
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
      setValidation(copy.enterQuestion)
      return
    }
    if (active) return
    if (historyInitializing) {
      setValidation(copy.historyPreparing)
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
      activity: GENERATION_ACTIVITIES[preferences.language][Math.floor(
        Math.random() * GENERATION_ACTIVITIES[preferences.language].length,
      )],
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
    let conversationForTurn = currentConversation
    try {
      if (historyEnabled && !conversationForTurn) {
        conversationForTurn = await createConversation(pendingCompanyScope)
        setCurrentConversation(conversationForTurn)
        setPendingCompanyScope([])
        setConversations((current) => [conversationForTurn!, ...current])
      }
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
      if (conversationForTurn) {
        const conversation = {
          conversationId: conversationForTurn.id,
          clientTurnId,
        }
        await streamChat(query, handlers, conversation, preferences.model)
      } else {
        await streamChat(query, handlers, undefined, preferences.model)
      }
      if (conversationForTurn) {
        setMessages(storedMessages(await listMessages(conversationForTurn.id)))
      }
      await refreshConversations()
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      const message = preferences.language === 'en' && error instanceof ChatStreamError
        ? error.message
        : copy.preTokenError
      updateAssistant(assistantId, (assistant) => ({
        ...assistant,
        state: 'error',
        error: receivedText ? copy.interrupted : message,
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
        language={preferences.language}
        authenticationRequired={authenticationRequired}
        authenticated={authenticated}
        onSignIn={() => window.location.assign(signInUrl())}
        onSignOut={() => {
          void signOut().then(() => {
            setAuthenticated(false)
            setHistoryEnabled(false)
            setCurrentConversation(null)
            setPendingCompanyScope([])
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
            language={preferences.language}
            companyScope={currentConversation?.company_scope ?? pendingCompanyScope}
            onToggleCompany={(ticker) => {
              const currentScope = currentConversation?.company_scope ?? pendingCompanyScope
              const nextScope = ticker === 'ALL'
                ? []
                : currentScope.includes(ticker)
                  ? currentScope.filter((value) => value !== ticker)
                  : [...currentScope, ticker]
              if (!currentConversation) {
                setPendingCompanyScope(nextScope)
                return
              }
              void updateConversation(currentConversation.id, { company_scope: nextScope }).then((updated) => {
                setCurrentConversation(updated)
                setConversations((items) => items.map((item) => item.id === updated.id ? updated : item))
              })
            }}
            onNew={() => void newConversation()}
            onSelect={(conversation) => void selectConversation(conversation)}
            onPin={(conversation) => {
              void updateConversation(conversation.id, { pinned: !conversation.pinned }).then(async (updated) => {
                if (currentConversation?.id === updated.id) setCurrentConversation(updated)
                setConversations(await listConversations())
              })
            }}
            onRename={(conversation) => {
              const title = window.prompt(copy.conversationName, conversation.title)
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
              if (!window.confirm(copy.deleteChats)) return
              void deleteAllConversations().then(async () => {
                setConversations([])
                setCurrentConversation(null)
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
                .catch(() => window.alert(copy.exportFailed))
            }}
          />
        )}
        {historyEnabled && sourcesOpen && (
          <ChatSourcesPanel
            documents={documents}
            language={preferences.language}
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
              <h1 id="auth-heading">{copy.signInTitle}</h1>
              <p>{copy.signInBody}</p>
              <button type="button" className="auth-prompt__button" onClick={() => window.location.assign(signInUrl())}>
                {copy.signIn}
              </button>
            </section>
          ) : startupError ? (
            <section className="auth-prompt" aria-labelledby="startup-error-heading">
              <h1 id="startup-error-heading">{copy.unavailable}</h1>
              <p>{startupError} {copy.checkConnection}</p>
              <button type="button" className="auth-prompt__button" onClick={() => window.location.reload()}>
                {copy.retry}
              </button>
            </section>
          ) : (
            <>
              {isEmpty ? <EmptyState theme={theme} language={preferences.language} /> : (
                <Conversation
                  messages={messages}
                  theme={theme}
                  language={preferences.language}
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
                language={preferences.language}
                active={active || historyInitializing || uploading}
                validationMessage={validation}
                onChange={(value) => {
                  setDraft(value)
                  if (validation) setValidation('')
                }}
                onSubmit={() => void submit()}
                uploadsEnabled={historyEnabled}
                uploadStatus={uploadStatus}
                sourceCount={documents.length}
                onUpload={(file) => void addDocument(file)}
                onOpenSources={currentConversation ? () => void openSources() : undefined}
              />
            </>
          )}
        </main>
      </div>
      {historyEnabled && authenticated && (
        <button
          type="button"
          className={`sidebar-toggle ${sidebarOpen ? 'sidebar-toggle--open' : ''}`}
          onClick={() => setSidebarOpen((value) => !value)}
          aria-label={preferences.language === 'sr'
            ? (sidebarOpen ? 'Zatvori bočnu traku razgovora' : 'Otvori bočnu traku razgovora')
            : (sidebarOpen ? 'Close conversation sidebar' : 'Open conversation sidebar')}
          aria-expanded={sidebarOpen}
        >
          <SidebarIcon />
        </button>
      )}
      {historyEnabled && authenticated && (
        <button
          ref={settingsButton}
          type="button"
          className={`settings-trigger ${sidebarOpen ? 'settings-trigger--sidebar-open' : ''}`}
          onClick={() => void openSettings()}
          aria-label={preferences.language === 'sr' ? 'Podešavanja' : 'Settings'}
        >
          <SettingsIcon />
        </button>
      )}
      {settingsOpen && (
        <SettingsModal
          preferences={preferences}
          memory={memory}
          loadingMemory={memoryLoading}
          onClose={closeSettings}
          onPreferences={savePreferences}
          onCreateMemory={addMemory}
          onUpdateMemory={saveMemory}
          onDeleteMemory={removeMemory}
        />
      )}
    </div>
  )
}

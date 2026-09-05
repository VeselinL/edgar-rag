import { useEffect, useRef, useState } from 'react'
import type { MemoryItem, PreferenceTheme, UserPreferences } from '../types'

const MODELS = [
  ['AZURE_GPT_4o_2024_1120', 'GPT-4o (2024-11-20)'],
  ['AZURE_GPT_41_2025_0414', 'GPT-4.1 (2025-04-14)'],
  ['AZURE_GPT_5_2025_0807', 'GPT-5 (2025-08-07)'],
  ['AZURE_GPT_51_2025_1113', 'GPT-5.1 (2025-11-13)'],
  ['AZURE_GPT_54_2026_0305', 'GPT-5.4 (2026-03-05)'],
  ['AZURE_GPT_55_2026_0424', 'GPT-5.5 (2026-04-24)'],
  ['AZURE_GPT_56_SOL_2026_0709', 'GPT-5.6 SOL (2026-07-09)'],
] as const

type Page = 'general' | 'memory' | 'personalization'

interface Props {
  preferences: UserPreferences
  memory: MemoryItem[]
  loadingMemory: boolean
  onClose: () => void
  onPreferences: (values: Partial<UserPreferences>) => Promise<void>
  onCreateMemory: (content: string) => Promise<void>
  onUpdateMemory: (id: string, content: string) => Promise<void>
  onDeleteMemory: (id: string) => Promise<void>
}

export function SettingsModal({
  preferences, memory, loadingMemory, onClose, onPreferences, onCreateMemory, onUpdateMemory, onDeleteMemory,
}: Props) {
  const dialog = useRef<HTMLDivElement>(null)
  const [page, setPage] = useState<Page>('general')
  const [error, setError] = useState('')
  const [draftMemory, setDraftMemory] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingContent, setEditingContent] = useState('')

  useEffect(() => {
    dialog.current?.querySelector<HTMLElement>('button, select, textarea, input')?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab' || !dialog.current) return
      const elements = [...dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), textarea:not(:disabled), input:not(:disabled)')]
      if (!elements.length) return
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const save = async (values: Partial<UserPreferences>) => {
    setError('')
    try { await onPreferences(values) } catch { setError('Settings could not be saved. Please retry.') }
  }
  const addMemory = async () => {
    if (!draftMemory.trim()) return
    setError('')
    try { await onCreateMemory(draftMemory); setDraftMemory('') } catch { setError('Memory could not be saved. Please retry.') }
  }
  const saveMemory = async () => {
    if (!editingId || !editingContent.trim()) return
    setError('')
    try { await onUpdateMemory(editingId, editingContent); setEditingId(null); setEditingContent('') } catch { setError('Memory could not be updated. Please retry.') }
  }

  return (
    <div className="settings-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div ref={dialog} className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-heading">
        <header className="settings-modal__header">
          <h1 id="settings-heading">Settings</h1>
          <button type="button" className="header-button" onClick={onClose}>Close</button>
        </header>
        <div className="settings-modal__body">
          <nav className="settings-nav" aria-label="Settings sections">
            {([['general', 'General'], ['memory', 'Memory'], ['personalization', 'Personalization']] as const).map(([value, label]) => (
              <button key={value} type="button" className={page === value ? 'settings-nav__active' : ''} onClick={() => setPage(value)}>{label}</button>
            ))}
          </nav>
          <section className="settings-content" aria-live="polite">
            {error && <p className="settings-error" role="alert">{error}</p>}
            {page === 'general' && <>
              <h2>General</h2>
              <label>Appearance
                <select value={preferences.theme} onChange={(event) => void save({ theme: event.target.value as PreferenceTheme })}>
                  <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
                </select>
              </label>
              <label>Language
                <select value={preferences.language} onChange={(event) => void save({ language: event.target.value as 'en' | 'sr' })}>
                  <option value="en">English</option><option value="sr">Serbian</option>
                </select>
              </label>
              <label>Answer model
                <select value={preferences.model} onChange={(event) => void save({ model: event.target.value })}>
                  {MODELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
            </>}
            {page === 'memory' && <>
              <h2>Memory</h2>
              <p>AVA uses saved preferences and learned chat summaries only as untrusted context. Filing and web evidence remain authoritative.</p>
              <label>Add a memory
                <textarea value={draftMemory} maxLength={1500} onChange={(event) => setDraftMemory(event.target.value)} />
              </label>
              <button type="button" className="settings-primary" onClick={() => void addMemory()}>Add memory</button>
              {loadingMemory ? <p>Loading memory…</p> : <ul className="memory-list">
                {memory.map((item) => <li key={item.id}>
                  {editingId === item.id ? <>
                    <textarea value={editingContent} maxLength={1500} onChange={(event) => setEditingContent(event.target.value)} />
                    <button type="button" onClick={() => void saveMemory()}>Save</button>
                    <button type="button" onClick={() => setEditingId(null)}>Cancel</button>
                  </> : <>
                    <p>{item.content}</p><small>{item.type === 'explicit' ? 'Saved by you' : 'Learned from chats'}</small>
                    <div><button type="button" onClick={() => { setEditingId(item.id); setEditingContent(item.content) }}>Edit</button><button type="button" onClick={() => void onDeleteMemory(item.id)}>Delete</button></div>
                  </>}
                </li>)}
              </ul>}
            </>}
            {page === 'personalization' && <>
              <h2>Personalization</h2>
              <label>Nickname <input value={preferences.nickname} maxLength={50} onChange={(event) => void save({ nickname: event.target.value })} /></label>
              <label>Warmth <select value={preferences.warmth} onChange={(event) => void save({ warmth: event.target.value as UserPreferences['warmth'] })}><option value="cold">Cold</option><option value="balanced">Balanced</option><option value="warm">Warm</option></select></label>
              <label>Enthusiasm <select value={preferences.enthusiasm} onChange={(event) => void save({ enthusiasm: event.target.value as UserPreferences['enthusiasm'] })}><option value="low">Low</option><option value="balanced">Balanced</option><option value="high">High</option></select></label>
              <label>Emoji use <select value={preferences.emoji_use} onChange={(event) => void save({ emoji_use: event.target.value as UserPreferences['emoji_use'] })}><option value="off">Off</option><option value="light">Light</option></select></label>
              <label>Custom instructions <textarea value={preferences.custom_instructions} maxLength={1500} onChange={(event) => void save({ custom_instructions: event.target.value })} /></label>
              <p>These may affect tone and formatting only. They cannot change AVA’s evidence, citation, security, identity, or tool rules.</p>
            </>}
          </section>
        </div>
      </div>
    </div>
  )
}

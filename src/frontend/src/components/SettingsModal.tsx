import { useEffect, useRef, useState } from 'react'
import type { MemoryItem, PreferenceTheme, UserPreferences } from '../types'
import { t } from '../i18n'

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
type PersonalizationDraft = Pick<UserPreferences, 'nickname' | 'warmth' | 'enthusiasm' | 'emoji_use' | 'custom_instructions'>

function toPersonalizationDraft(preferences: UserPreferences): PersonalizationDraft {
  const { nickname, warmth, enthusiasm, emoji_use, custom_instructions } = preferences
  return { nickname, warmth, enthusiasm, emoji_use, custom_instructions }
}

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
  const label = (key: Parameters<typeof t>[1]) => t(preferences.language, key)
  const dialog = useRef<HTMLDivElement>(null)
  const memoryEditor = useRef<HTMLDivElement>(null)
  const [page, setPage] = useState<Page>('general')
  const [error, setError] = useState('')
  const [draftMemory, setDraftMemory] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingContent, setEditingContent] = useState('')
  const [personalization, setPersonalization] = useState<PersonalizationDraft>(() => toPersonalizationDraft(preferences))

  useEffect(() => setPersonalization(toPersonalizationDraft(preferences)), [preferences])

  useEffect(() => {
    dialog.current?.querySelector<HTMLElement>('button, select, textarea, input')?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (editingId) setEditingId(null)
        else onClose()
      }
      const activeDialog = memoryEditor.current ?? dialog.current
      if (event.key !== 'Tab' || !activeDialog) return
      const elements = [...activeDialog.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), textarea:not(:disabled), input:not(:disabled)')]
      if (!elements.length) return
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [editingId, onClose])

  useEffect(() => {
    if (editingId) memoryEditor.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus()
  }, [editingId])

  const save = async (values: Partial<UserPreferences>) => {
    setError('')
    try { await onPreferences(values) } catch { setError(preferences.language === 'sr' ? 'Podešavanja nisu sačuvana. Pokušajte ponovo.' : 'Settings could not be saved. Please retry.') }
  }
  const addMemory = async () => {
    if (!draftMemory.trim()) return
    setError('')
    try { await onCreateMemory(draftMemory); setDraftMemory('') } catch { setError(preferences.language === 'sr' ? 'Memorija nije sačuvana. Pokušajte ponovo.' : 'Memory could not be saved. Please retry.') }
  }
  const saveMemory = async () => {
    if (!editingId || !editingContent.trim()) return
    setError('')
    try { await onUpdateMemory(editingId, editingContent); setEditingId(null); setEditingContent('') } catch { setError(preferences.language === 'sr' ? 'Memorija nije izmenjena. Pokušajte ponovo.' : 'Memory could not be updated. Please retry.') }
  }
  const savePersonalization = async () => {
    await save(personalization)
  }

  return (
    <div className="settings-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div ref={dialog} className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-heading">
        <header className="settings-modal__header">
          <h1 id="settings-heading">{label('settings')}</h1>
          <button type="button" className="settings-close" onClick={onClose} aria-label={label('close')}>
            <span aria-hidden="true">×</span>
          </button>
        </header>
        <div className="settings-modal__body">
          <nav className="settings-nav" aria-label={label('settings')}>
            {([['general', 'general'], ['memory', 'memory'], ['personalization', 'personalization']] as const).map(([value, key]) => (
              <button key={value} type="button" className={page === value ? 'settings-nav__active' : ''} onClick={() => setPage(value)}>{label(key)}</button>
            ))}
          </nav>
          <section className="settings-content" aria-live="polite">
            {error && <p className="settings-error" role="alert">{error}</p>}
            {page === 'general' && <>
              <h2>{label('general')}</h2>
              <label>{label('appearance')}
                <select value={preferences.theme} onChange={(event) => void save({ theme: event.target.value as PreferenceTheme })}>
                  <option value="system">{label('system')}</option><option value="light">{label('light')}</option><option value="dark">{label('dark')}</option>
                </select>
              </label>
              <label>{label('language')}
                <select value={preferences.language} onChange={(event) => void save({ language: event.target.value as 'en' | 'sr' })}>
                  <option value="en">{label('english')}</option><option value="sr">{label('serbian')}</option>
                </select>
              </label>
              <label>{label('model')}
                <select value={preferences.model} onChange={(event) => void save({ model: event.target.value })}>
                  {MODELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
            </>}
            {page === 'memory' && <>
              <h2>{label('memory')}</h2>
              <p>{label('memoryNotice')}</p>
              <label htmlFor="memory-content">{label('addMemory')}
                <textarea id="memory-content" aria-label={label('addMemory')} value={draftMemory} maxLength={1500} onChange={(event) => setDraftMemory(event.target.value)} />
              </label>
              <button type="button" className="settings-primary" onClick={() => void addMemory()}>{label('addMemory')}</button>
              {loadingMemory ? <p>{label('loadingMemory')}</p> : <ul className="memory-list">
                {memory.map((item) => <li key={item.id}>
                  <p>{item.content}</p><small>{item.type === 'explicit' ? label('savedByYou') : label('learnedChats')}</small>
                  <div className="memory-list__actions"><button type="button" onClick={() => { setEditingId(item.id); setEditingContent(item.content) }}>{label('edit')}</button><button type="button" onClick={() => void onDeleteMemory(item.id)}>{label('delete')}</button></div>
                </li>)}
              </ul>}
            </>}
            {page === 'personalization' && <>
              <h2>{label('personalization')}</h2>
              <label>{label('nickname')} <input value={personalization.nickname} maxLength={50} onChange={(event) => setPersonalization((values) => ({ ...values, nickname: event.target.value }))} /></label>
              <label>{label('warmth')} <select value={personalization.warmth} onChange={(event) => setPersonalization((values) => ({ ...values, warmth: event.target.value as UserPreferences['warmth'] }))}><option value="cold">{label('cold')}</option><option value="balanced">{label('balanced')}</option><option value="warm">{label('warm')}</option></select></label>
              <label>{label('enthusiasm')} <select value={personalization.enthusiasm} onChange={(event) => setPersonalization((values) => ({ ...values, enthusiasm: event.target.value as UserPreferences['enthusiasm'] }))}><option value="low">{label('low')}</option><option value="balanced">{label('balanced')}</option><option value="high">{label('high')}</option></select></label>
              <label>{label('emojiUse')} <select value={personalization.emoji_use} onChange={(event) => setPersonalization((values) => ({ ...values, emoji_use: event.target.value as UserPreferences['emoji_use'] }))}><option value="off">{label('off')}</option><option value="light">{label('light')}</option></select></label>
              <label>{label('customInstructions')} <textarea value={personalization.custom_instructions} maxLength={1500} onChange={(event) => setPersonalization((values) => ({ ...values, custom_instructions: event.target.value }))} /></label>
              <button type="button" className="settings-primary" onClick={() => void savePersonalization()}>{label('savePersonalization')}</button>
              <p>{label('preferenceNotice')}</p>
            </>}
          </section>
        </div>
        {editingId && (
          <div className="settings-editor-backdrop">
            <div ref={memoryEditor} className="settings-memory-editor" role="dialog" aria-modal="true" aria-labelledby="memory-editor-heading">
              <h2 id="memory-editor-heading">{label('editMemory')}</h2>
              <label htmlFor="memory-editor-content">{label('editMemory')}
                <textarea id="memory-editor-content" value={editingContent} maxLength={1500} onChange={(event) => setEditingContent(event.target.value)} />
              </label>
              <div className="settings-editor__actions">
                <button type="button" className="settings-primary" onClick={() => void saveMemory()}>{label('save')}</button>
                <button type="button" className="header-button" onClick={() => setEditingId(null)}>{label('cancel')}</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

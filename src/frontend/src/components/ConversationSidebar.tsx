import { useEffect, useRef, useState } from 'react'
import type { ConversationSummary } from '../types'

interface Props {
  conversations: ConversationSummary[]
  activeId?: string
  memoryEnabled: boolean
  onNew: () => void
  onToggleMemory: () => void
  companyScope: string[]
  onToggleCompany: (ticker: string) => void
  model: string
  onModelChange: (model: string) => void
  onSelect: (conversation: ConversationSummary) => void
  onPin: (conversation: ConversationSummary) => void
  onRename: (conversation: ConversationSummary) => void
  onDelete: (conversation: ConversationSummary) => void
  onDeleteAll: () => void
  onExport: () => void
  onClose: () => void
}

function DotsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="5" cy="12" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="19" cy="12" r="1.4" />
    </svg>
  )
}

const AVAILABLE_MODELS = [
  ['AZURE_GPT_4o_2024_1120', 'GPT-4o (2024-11-20)'],
  ['AZURE_GPT_41_2025_0414', 'GPT-4.1 (2025-04-14)'],
  ['AZURE_GPT_5_2025_0807', 'GPT-5 (2025-08-07)'],
  ['AZURE_GPT_51_2025_1113', 'GPT-5.1 (2025-11-13)'],
  ['AZURE_GPT_54_2026_0305', 'GPT-5.4 (2026-03-05)'],
  ['AZURE_GPT_55_2026_0424', 'GPT-5.5 (2026-04-24)'],
  ['AZURE_GPT_56_SOL_2026_0709', 'GPT-5.6 SOL (2026-07-09)'],
] as const

function ConversationRow({
  conversation,
  active,
  menuOpen,
  onSelect,
  onOpenMenu,
  onCloseMenu,
  onPin,
  onRename,
  onDelete,
}: {
  conversation: ConversationSummary
  active: boolean
  menuOpen: boolean
  onSelect: () => void
  onOpenMenu: () => void
  onCloseMenu: () => void
  onPin: () => void
  onRename: () => void
  onDelete: () => void
}) {
  const rowButton = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const wasMenuOpen = useRef(false)
  useEffect(() => {
    if (menuOpen) menu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    if (wasMenuOpen.current && !menuOpen) rowButton.current?.focus()
    wasMenuOpen.current = menuOpen
  }, [menuOpen])
  const restoreFocus = () => {
    onCloseMenu()
  }
  const action = (callback: () => void) => {
    restoreFocus()
    callback()
  }

  return (
    <li
      className={`sidebar-chat ${active ? 'sidebar-chat--active' : ''} ${menuOpen ? 'sidebar-chat--menu-open' : ''}`}
      onContextMenu={(event) => {
        event.preventDefault()
        onOpenMenu()
      }}
    >
      <button
        ref={rowButton}
        type="button"
        className="sidebar-chat__title"
        onClick={onSelect}
        aria-current={active ? 'page' : undefined}
      >
        {conversation.title}
      </button>
      <button
        type="button"
        className="sidebar-chat__menu-button"
        aria-label={`Actions for ${conversation.title}`}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => menuOpen ? restoreFocus() : onOpenMenu()}
      >
        <DotsIcon />
      </button>
      {menuOpen && (
        <div
          ref={menu}
          className="sidebar-chat__menu"
          role="menu"
          aria-label={`Actions for ${conversation.title}`}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation()
              restoreFocus()
            }
          }}
        >
          <button type="button" role="menuitem" onClick={() => action(onPin)}>
            {conversation.pinned ? 'Unpin' : 'Pin'}
          </button>
          <button type="button" role="menuitem" onClick={() => action(onRename)}>Rename</button>
          <button type="button" role="menuitem" className="sidebar-chat__delete" onClick={() => action(onDelete)}>Delete</button>
        </div>
      )}
    </li>
  )
}

export function ConversationSidebar({
  conversations,
  activeId,
  memoryEnabled,
  onNew,
  onToggleMemory,
  companyScope,
  onToggleCompany,
  model,
  onModelChange,
  onSelect,
  onPin,
  onRename,
  onDelete,
  onDeleteAll,
  onExport,
  onClose,
}: Props) {
  const [menuId, setMenuId] = useState<string | null>(null)
  const pinned = conversations.filter((conversation) => conversation.pinned)
  const recent = conversations.filter((conversation) => !conversation.pinned)

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (menuId) setMenuId(null)
      else onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [menuId, onClose])

  const rows = (items: ConversationSummary[]) => items.map((conversation) => (
    <ConversationRow
      key={conversation.id}
      conversation={conversation}
      active={conversation.id === activeId}
      menuOpen={conversation.id === menuId}
      onSelect={() => onSelect(conversation)}
      onOpenMenu={() => setMenuId(conversation.id)}
      onCloseMenu={() => setMenuId(null)}
      onPin={() => onPin(conversation)}
      onRename={() => onRename(conversation)}
      onDelete={() => onDelete(conversation)}
    />
  ))

  return (
    <aside className="conversation-sidebar" aria-label="Conversation history">
      <div className="conversation-sidebar__heading">
        <strong>AVA</strong>
        <button type="button" className="header-button" onClick={onClose}>Close</button>
      </div>
      <button type="button" className="sidebar-primary-action" onClick={onNew}>+ New chat</button>
      <button
        type="button"
        className={`sidebar-memory ${memoryEnabled ? 'sidebar-memory--active' : ''}`}
        aria-pressed={memoryEnabled}
        onClick={onToggleMemory}
      >
        <span>Long-term memory</span>
        <strong>{memoryEnabled ? 'On' : 'Off'}</strong>
      </button>
      <fieldset className="sidebar-companies">
        <legend>Company scope</legend>
        <label><input type="checkbox" checked={companyScope.length === 0} onChange={() => companyScope.length && onToggleCompany('ALL')} /> All companies</label>
        {[
          ['APTV', 'Aptiv'], ['AUR', 'Aurora'], ['F', 'Ford'], ['GM', 'General Motors'],
          ['GOOGL', 'Alphabet'], ['MBLY', 'Mobileye'], ['NVDA', 'NVIDIA'], ['OUST', 'Ouster'],
          ['QCOM', 'Qualcomm'], ['RIVN', 'Rivian'], ['TSLA', 'Tesla'],
        ].map(([ticker, name]) => (
          <label key={ticker}><input type="checkbox" checked={companyScope.includes(ticker)} onChange={() => onToggleCompany(ticker)} /> {name} ({ticker})</label>
        ))}
      </fieldset>
      <label className="sidebar-model">
        <span>Answer model</span>
        <select value={model} onChange={(event) => onModelChange(event.target.value)}>
          {AVAILABLE_MODELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <nav aria-label="Saved chats" className="conversation-sidebar__history">
        {pinned.length > 0 && (
          <section aria-labelledby="pinned-chats-heading">
            <h2 id="pinned-chats-heading">Pinned</h2>
            <ul>{rows(pinned)}</ul>
          </section>
        )}
        <section aria-labelledby="recent-chats-heading">
          <h2 id="recent-chats-heading">Chats</h2>
          {recent.length > 0 ? <ul>{rows(recent)}</ul> : <p className="history-empty">No other chats.</p>}
        </section>
      </nav>
      <div className="conversation-sidebar__footer">
        <button type="button" onClick={onExport}>Export my data</button>
        {conversations.length > 0 && <button type="button" className="danger" onClick={onDeleteAll}>Delete all chats</button>}
      </div>
    </aside>
  )
}

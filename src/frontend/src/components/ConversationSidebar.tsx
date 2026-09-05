import { useEffect, useRef, useState } from 'react'
import type { ConversationSummary } from '../types'
import type { Language } from '../i18n'
import { t } from '../i18n'

interface Props {
  conversations: ConversationSummary[]
  activeId?: string
  language: Language
  onNew: () => void
  companyScope: string[]
  onToggleCompany: (ticker: string) => void
  onSelect: (conversation: ConversationSummary) => void
  onPin: (conversation: ConversationSummary) => void
  onRename: (conversation: ConversationSummary) => void
  onDelete: (conversation: ConversationSummary) => void
  onDeleteAll: () => void
  onExport: () => void
  onClose: () => void
}

const COMPANIES = [
  ['APTV', 'Aptiv'], ['AUR', 'Aurora'], ['F', 'Ford'], ['GM', 'General Motors'],
  ['GOOGL', 'Alphabet'], ['MBLY', 'Mobileye'], ['NVDA', 'NVIDIA'], ['OUST', 'Ouster'],
  ['QCOM', 'Qualcomm'], ['RIVN', 'Rivian'], ['TSLA', 'Tesla'],
] as const
const companyNameByTicker = new Map<string, string>(COMPANIES)
const RECENT_CHAT_LIMIT = 7

function DotsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="5" cy="12" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="19" cy="12" r="1.4" />
    </svg>
  )
}

function ConversationRow({
  conversation,
  language,
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
  language: Language
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
        aria-label={`${t(language, 'actionsFor')} ${conversation.title}`}
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
          aria-label={`${t(language, 'actionsFor')} ${conversation.title}`}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation()
              restoreFocus()
            }
          }}
        >
          <button type="button" role="menuitem" onClick={() => action(onPin)}>
            {conversation.pinned ? t(language, 'unpin') : t(language, 'pin')}
          </button>
          <button type="button" role="menuitem" onClick={() => action(onRename)}>{t(language, 'rename')}</button>
          <button type="button" role="menuitem" className="sidebar-chat__delete" onClick={() => action(onDelete)}>{t(language, 'delete')}</button>
        </div>
      )}
    </li>
  )
}

export function ConversationSidebar({
  conversations,
  activeId,
  language,
  onNew,
  companyScope,
  onToggleCompany,
  onSelect,
  onPin,
  onRename,
  onDelete,
  onDeleteAll,
  onExport,
  onClose,
}: Props) {
  const [menuId, setMenuId] = useState<string | null>(null)
  const [scopeExpanded, setScopeExpanded] = useState(false)
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const pinned = conversations.filter((conversation) => conversation.pinned)
  const recent = conversations.filter((conversation) => !conversation.pinned)
  const visibleRecent = historyExpanded ? recent : recent.slice(0, RECENT_CHAT_LIMIT)
  const selectedCompanyNames = companyScope.map((ticker) => companyNameByTicker.get(ticker) ?? ticker)
  const scopeSummary = companyScope.length === 0
    ? t(language, 'allCompanies')
    : `${selectedCompanyNames.slice(0, 3).join(', ')}${selectedCompanyNames.length > 3 ? '…' : ''}`

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
      language={language}
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
    <aside className="conversation-sidebar" aria-label={t(language, 'conversationHistory')}>
      <div className="conversation-sidebar__heading">
        <strong>AVA</strong>
        <button type="button" className="header-button" onClick={onClose}>{t(language, 'close')}</button>
      </div>
      <button type="button" className="sidebar-primary-action" onClick={onNew}>{t(language, 'newChat')}</button>
      <nav aria-label={t(language, 'savedChats')} className="conversation-sidebar__history">
        {pinned.length > 0 && (
          <section aria-labelledby="pinned-chats-heading">
            <h2 id="pinned-chats-heading">{t(language, 'pinned')}</h2>
            <ul>{rows(pinned)}</ul>
          </section>
        )}
        <section aria-labelledby="recent-chats-heading">
          <h2 id="recent-chats-heading">{t(language, 'chats')}</h2>
          {recent.length > 0 ? (
            <>
              <ul>{rows(visibleRecent)}</ul>
              {recent.length > RECENT_CHAT_LIMIT && !historyExpanded && (
                <button type="button" className="sidebar-show-more" onClick={() => setHistoryExpanded(true)}>
                  {t(language, 'showMoreChats')}
                </button>
              )}
            </>
          ) : <p className="history-empty">{t(language, 'noOtherChats')}</p>}
        </section>
      </nav>
      <section className="sidebar-companies" aria-labelledby="company-scope-heading">
        <button
          id="company-scope-heading"
          type="button"
          className="sidebar-companies__toggle"
          aria-expanded={scopeExpanded}
          onClick={() => setScopeExpanded((expanded) => !expanded)}
        >
          {t(language, 'companyScope')}
        </button>
        <p className="sidebar-companies__summary">{scopeSummary}</p>
        {scopeExpanded && (
          <div className="sidebar-companies__options">
            <label><input type="checkbox" checked={companyScope.length === 0} onChange={() => companyScope.length && onToggleCompany('ALL')} /> {t(language, 'allCompanies')}</label>
            {COMPANIES.map(([ticker, name]) => (
              <label key={ticker}><input type="checkbox" checked={companyScope.includes(ticker)} onChange={() => onToggleCompany(ticker)} /> {name} ({ticker})</label>
            ))}
          </div>
        )}
      </section>
      <div className="conversation-sidebar__footer">
        <button type="button" onClick={onExport}>{t(language, 'exportData')}</button>
        {conversations.length > 0 && <button type="button" className="danger" onClick={onDeleteAll}>{t(language, 'deleteAll')}</button>}
      </div>
    </aside>
  )
}

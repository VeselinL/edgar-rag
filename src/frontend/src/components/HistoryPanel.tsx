import type { ConversationSummary } from '../types'

interface HistoryPanelProps {
  conversations: ConversationSummary[]
  activeId?: string
  onSelect: (conversation: ConversationSummary) => void
  onRename: (conversation: ConversationSummary) => void
  onDelete: (conversation: ConversationSummary) => void
  onDeleteAll: () => void
  onClose: () => void
}

export function HistoryPanel({
  conversations,
  activeId,
  onSelect,
  onRename,
  onDelete,
  onDeleteAll,
  onClose,
}: HistoryPanelProps) {
  return (
    <aside className="history-panel" aria-label="Conversation history">
      <div className="history-panel__heading">
        <h2>Conversations</h2>
        <button type="button" className="header-button" onClick={onClose}>Close</button>
      </div>
      {conversations.length === 0 ? (
        <p className="history-empty">No saved conversations.</p>
      ) : (
        <ul className="history-list">
          {conversations.map((conversation) => (
            <li key={conversation.id} className={conversation.id === activeId ? 'history-item history-item--active' : 'history-item'}>
              <button type="button" className="history-item__title" onClick={() => onSelect(conversation)}>
                {conversation.title}
              </button>
              <div className="history-item__actions">
                <button type="button" onClick={() => onRename(conversation)}>Rename</button>
                <button type="button" onClick={() => onDelete(conversation)}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {conversations.length > 0 && (
        <button type="button" className="delete-all" onClick={onDeleteAll}>Delete all conversations</button>
      )}
    </aside>
  )
}

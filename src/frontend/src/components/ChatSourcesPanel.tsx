import { useEffect, useRef } from 'react'
import type { ChatDocument } from '../types'

interface Props {
  documents: ChatDocument[]
  loading: boolean
  error: string
  deletingId: string | null
  onDelete: (document: ChatDocument) => void
  onClose: () => void
}

function readableSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

export function ChatSourcesPanel({ documents, loading, error, deletingId, onDelete, onClose }: Props) {
  const closeButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeButton.current?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <aside className="chat-sources-panel" role="dialog" aria-modal="true" aria-labelledby="chat-sources-heading">
      <div className="chat-sources-panel__heading">
        <div>
          <h2 id="chat-sources-heading">Sources</h2>
          <p>Files available only in this chat</p>
        </div>
        <button ref={closeButton} type="button" className="header-button" onClick={onClose}>Close</button>
      </div>
      <div role="status" aria-live="polite">
        {loading && <p className="history-empty">Loading sources…</p>}
        {error && <p className="source-warning">{error}</p>}
      </div>
      {!loading && documents.length === 0 && !error && (
        <p className="history-empty">No files have been added to this chat.</p>
      )}
      {documents.length > 0 && (
        <ul className="chat-document-list">
          {documents.map((document) => (
            <li key={document.id}>
              <div className="chat-document__heading">
                <strong title={document.filename}>{document.filename}</strong>
                <span className={`document-status document-status--${document.status}`}>{document.status}</span>
              </div>
              <p>
                {document.media_type === 'application/pdf' ? 'PDF' : 'Text'} · {readableSize(document.size_bytes)}
                {document.page_count ? ` · ${document.page_count} page${document.page_count === 1 ? '' : 's'}` : ''}
                {document.status === 'ready' ? ` · ${document.chunk_count} excerpt${document.chunk_count === 1 ? '' : 's'}` : ''}
              </p>
              <button
                type="button"
                disabled={deletingId === document.id}
                onClick={() => onDelete(document)}
                aria-label={`Delete ${document.filename}`}
              >
                {deletingId === document.id ? 'Deleting…' : 'Delete'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}

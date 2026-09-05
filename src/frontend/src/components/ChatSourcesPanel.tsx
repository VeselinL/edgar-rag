import { useEffect, useRef } from 'react'
import type { ChatDocument } from '../types'
import type { Language } from '../i18n'
import { t } from '../i18n'

interface Props {
  documents: ChatDocument[]
  language: Language
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

export function ChatSourcesPanel({ documents, language, loading, error, deletingId, onDelete, onClose }: Props) {
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
          <h2 id="chat-sources-heading">{t(language, 'sources')}</h2>
          <p>{t(language, 'filesInChat')}</p>
        </div>
        <button ref={closeButton} type="button" className="header-button" onClick={onClose}>{t(language, 'close')}</button>
      </div>
      <div role="status" aria-live="polite">
        {loading && <p className="history-empty">{t(language, 'loadingSources')}</p>}
        {error && <p className="source-warning">{error}</p>}
      </div>
      {!loading && documents.length === 0 && !error && (
        <p className="history-empty">{t(language, 'noFiles')}</p>
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
                {document.media_type === 'application/pdf' ? 'PDF' : t(language, 'textFile')} · {readableSize(document.size_bytes)}
                {document.page_count ? ` · ${document.page_count} ${t(language, 'page')}${document.page_count === 1 ? '' : language === 'en' ? 's' : ''}` : ''}
                {document.status === 'ready' ? ` · ${document.chunk_count} ${t(language, 'excerpts')}` : ''}
              </p>
              <button
                type="button"
                disabled={deletingId === document.id}
                onClick={() => onDelete(document)}
                aria-label={`${t(language, 'delete')} ${document.filename}`}
              >
                {deletingId === document.id ? t(language, 'deleting') : t(language, 'delete')}
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}

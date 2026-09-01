import { useEffect, useRef } from 'react'

interface Props {
  value: string
  active: boolean
  validationMessage: string
  onChange: (value: string) => void
  onSubmit: () => void
  uploadsEnabled?: boolean
  uploadStatus?: string
  sourceCount?: number
  onUpload?: (file: File) => void
  onOpenSources?: () => void
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m5 12 7-7 7 7M12 5v14" />
    </svg>
  )
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

export function Composer({
  value,
  active,
  validationMessage,
  onChange,
  onSubmit,
  uploadsEnabled = false,
  uploadStatus = '',
  sourceCount = 0,
  onUpload,
  onOpenSources,
}: Props) {
  const textarea = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const element = textarea.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`
  }, [value])

  return (
    <div className="composer-wrap">
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit()
        }}
      >
        <input
          ref={fileInput}
          className="sr-only"
          type="file"
          accept=".pdf,.txt,application/pdf,text/plain"
          disabled={active || !uploadsEnabled}
          aria-label="Choose a PDF or text source"
          onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (file) onUpload?.(file)
          }}
        />
        <button
          className="upload-button"
          type="button"
          disabled={active || !uploadsEnabled}
          aria-label="Upload a source"
          title={uploadsEnabled ? 'Upload a PDF or text source' : 'Uploads require a saved chat'}
          onClick={() => fileInput.current?.click()}
        >
          <PlusIcon />
        </button>
        <label className="sr-only" htmlFor="ava-query">Ask AVA about the SEC filings</label>
        <textarea
          ref={textarea}
          id="ava-query"
          value={value}
          rows={1}
          disabled={active}
          placeholder="Ask about the filings…"
          aria-describedby="composer-help composer-status"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              onSubmit()
            }
          }}
        />
        <button className="send-button" type="submit" disabled={active || !value.trim()} aria-label="Send question">
          <SendIcon />
        </button>
      </form>
      <div className="composer-meta">
        <span id="composer-help">Enter to send · Shift+Enter for a new line · <a href="/privacy.html">Privacy</a></span>
        <span className="composer-meta__right">
          {uploadsEnabled && onOpenSources && (
            <button type="button" className="chat-sources-button" onClick={onOpenSources}>
              Sources{sourceCount > 0 ? ` (${sourceCount})` : ''}
            </button>
          )}
          <span id="composer-status" role="status" aria-live="polite">{uploadStatus || validationMessage}</span>
        </span>
      </div>
    </div>
  )
}

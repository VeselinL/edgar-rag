import { useEffect, useRef } from 'react'
import type { Language } from '../i18n'
import { t } from '../i18n'

interface Props {
  value: string
  language: Language
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
  language,
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
          aria-label={t(language, 'chooseSource')}
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
          aria-label={t(language, 'uploadSource')}
          title={t(language, 'uploadSource')}
          onClick={() => fileInput.current?.click()}
        >
          <PlusIcon />
        </button>
        <label className="sr-only" htmlFor="ava-query">{t(language, 'askFilings')}</label>
        <textarea
          ref={textarea}
          id="ava-query"
          value={value}
          rows={1}
          disabled={active}
          placeholder={t(language, 'askPlaceholder')}
          aria-describedby="composer-help composer-status"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              onSubmit()
            }
          }}
        />
        <button className="send-button" type="submit" disabled={active || !value.trim()} aria-label={t(language, 'send')}>
          <SendIcon />
        </button>
      </form>
      <div className="composer-meta">
        <span id="composer-help">{t(language, 'sendHelp')} <a href="/privacy.html">{t(language, 'privacy')}</a></span>
        <span className="composer-meta__right">
          {uploadsEnabled && onOpenSources && (
            <button type="button" className="chat-sources-button" onClick={onOpenSources}>
              {t(language, 'sources')}{sourceCount > 0 ? ` (${sourceCount})` : ''}
            </button>
          )}
          <span id="composer-status" role="status" aria-live="polite">{uploadStatus || validationMessage}</span>
        </span>
      </div>
    </div>
  )
}

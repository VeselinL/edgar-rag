import { useEffect, useRef, useState } from 'react'
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
  const uploadButton = useRef<HTMLButtonElement>(null)
  const uploadMenu = useRef<HTMLDivElement>(null)
  const [uploadChoiceOpen, setUploadChoiceOpen] = useState(false)
  const [rawTextMode, setRawTextMode] = useState(false)
  const [rawText, setRawText] = useState('')
  const [rawFilename, setRawFilename] = useState('')
  const [rawTextError, setRawTextError] = useState('')
  useEffect(() => {
    const element = textarea.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`
  }, [value])

  useEffect(() => {
    if (!uploadChoiceOpen) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node
      if (!uploadMenu.current?.contains(target) && !uploadButton.current?.contains(target)) closeUploadChoice()
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeUploadChoice()
    }
    window.addEventListener('mousedown', closeOnOutsideClick)
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('mousedown', closeOnOutsideClick)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [uploadChoiceOpen])

  const closeUploadChoice = () => {
    setUploadChoiceOpen(false)
    setRawTextMode(false)
    setRawText('')
    setRawFilename('')
    setRawTextError('')
  }

  const uploadRawText = () => {
    if (!rawText.trim()) {
      setRawTextError(t(language, 'rawTextRequired'))
      return
    }
    const baseName = rawFilename.trim() || 'source.txt'
    const filename = /\.txt$/i.test(baseName) ? baseName : `${baseName}.txt`
    onUpload?.(new File([rawText], filename, { type: 'text/plain' }))
    closeUploadChoice()
  }

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
            if (file) {
              onUpload?.(file)
              closeUploadChoice()
            }
          }}
        />
        <button
          ref={uploadButton}
          className="upload-button"
          type="button"
          disabled={active || !uploadsEnabled}
          aria-label={t(language, 'uploadSource')}
          title={t(language, 'uploadSource')}
          onClick={() => setUploadChoiceOpen((open) => !open)}
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
      {uploadChoiceOpen && (
        <div ref={uploadMenu} className="upload-menu" aria-label={t(language, 'addSource')} role={rawTextMode ? undefined : 'menu'}>
            {!rawTextMode ? (
              <div className="upload-menu__options">
                <button type="button" role="menuitem" className="upload-menu__option" onClick={() => fileInput.current?.click()}>
                  {t(language, 'uploadFromComputer')}
                </button>
                <button type="button" role="menuitem" className="upload-menu__option" onClick={() => setRawTextMode(true)}>
                  {t(language, 'uploadRawText')}
                </button>
              </div>
            ) : (
              <form className="upload-dialog__form" onSubmit={(event) => { event.preventDefault(); uploadRawText() }}>
                <label htmlFor="raw-source-text">{t(language, 'pasteRawText')}
                  <textarea id="raw-source-text" value={rawText} onChange={(event) => {
                    setRawText(event.target.value)
                    if (rawTextError) setRawTextError('')
                  }} />
                </label>
                <label htmlFor="raw-source-filename">{t(language, 'sourceFilename')}
                  <input id="raw-source-filename" value={rawFilename} maxLength={251} onChange={(event) => setRawFilename(event.target.value)} />
                </label>
                <p>{t(language, 'sourceFilenameHint')}</p>
                {rawTextError && <p className="settings-error" role="alert">{rawTextError}</p>}
                <div className="upload-dialog__actions">
                  <button type="submit" className="settings-primary">{t(language, 'uploadText')}</button>
                  <button type="button" className="header-button" onClick={() => setRawTextMode(false)}>{t(language, 'back')}</button>
                  <button type="button" className="header-button" onClick={closeUploadChoice}>{t(language, 'cancel')}</button>
                </div>
              </form>
            )}
        </div>
      )}
    </div>
  )
}

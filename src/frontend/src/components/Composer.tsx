import { useEffect, useRef } from 'react'

interface Props {
  value: string
  active: boolean
  validationMessage: string
  onChange: (value: string) => void
  onSubmit: () => void
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m5 12 7-7 7 7M12 5v14" />
    </svg>
  )
}

export function Composer({ value, active, validationMessage, onChange, onSubmit }: Props) {
  const textarea = useRef<HTMLTextAreaElement>(null)
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
        <span id="composer-status" role="status">{validationMessage}</span>
      </div>
    </div>
  )
}

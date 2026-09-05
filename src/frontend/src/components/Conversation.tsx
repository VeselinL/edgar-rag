import { useEffect, useRef } from 'react'
import type { Theme } from '../hooks/useTheme'
import type { ChatMessage } from '../types'
import { Messages } from './Messages'
import type { Language } from '../i18n'
import { t } from '../i18n'

export function Conversation({
  messages,
  theme,
  language,
  onFeedback,
}: {
  messages: ChatMessage[]
  theme: Theme
  language: Language
  onFeedback: (messageId: string, value: 'helpful' | 'not_helpful') => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)

  useEffect(() => {
    if (!nearBottom.current) return
    const element = container.current
    if (element) element.scrollTop = element.scrollHeight
  }, [messages])

  return (
    <div
      className="conversation-scroll"
      ref={container}
      onScroll={(event) => {
        const element = event.currentTarget
        nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80
      }}
    >
      <div className="conversation" aria-label={t(language, 'conversation')}>
        <Messages messages={messages} theme={theme} language={language} onFeedback={onFeedback} />
      </div>
    </div>
  )
}

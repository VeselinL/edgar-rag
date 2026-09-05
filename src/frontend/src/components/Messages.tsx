import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Theme } from '../hooks/useTheme'
import type { AssistantMessage as AssistantMessageType, ChatMessage } from '../types'
import { AvaAvatar } from './AvaAvatar'
import { Sources } from './Sources'
import { WaitingBubble } from './WaitingBubble'
import type { Language } from '../i18n'
import { t } from '../i18n'

function UserMessage({ text }: { text: string }) {
  return <div className="message message--user"><p>{text}</p></div>
}

function AssistantMessage({
  message,
  theme,
  language,
  onFeedback,
}: {
  message: AssistantMessageType
  theme: Theme
  language: Language
  onFeedback: (messageId: string, value: 'helpful' | 'not_helpful') => void
}) {
  const waiting = message.state === 'waiting_for_first_token'
  return (
    <article className="message message--assistant" aria-label={t(language, 'avaResponse')}>
      <div className="assistant-avatar">
        <AvaAvatar decorative theme={theme} />
        {waiting && <WaitingBubble activity={message.activity} language={language} />}
      </div>
      <div className="assistant-content">
        {message.text && (
          <div className="markdown">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              skipHtml
              components={{
                a: ({ children, ...props }) => <a {...props} target="_blank" rel="noreferrer">{children}</a>,
              }}
            >
              {message.text}
            </ReactMarkdown>
          </div>
        )}
        {message.error && <p className="message-error" role="alert">{message.error}</p>}
        {message.state === 'completed' && <span className="sr-only" role="status">{t(language, 'responseComplete')}</span>}
        {(message.state === 'completed' || (message.state === 'error' && message.sources !== null)) && message.sources !== null && (
          <Sources
            sources={message.sources}
            sourceStatus={message.sourceStatus}
            malformedCount={message.malformedSourceCount}
            language={language}
          />
        )}
        {message.feedbackEligible && message.state === 'completed' && (
          <div className="feedback" aria-label={t(language, 'rateAnswer')}>
            <span>{t(language, 'wasUseful')}</span>
            <button
              type="button"
              aria-label={t(language, 'markHelpful')}
              aria-pressed={message.feedback === 'helpful'}
              disabled={message.feedback === 'submitting'}
              onClick={() => onFeedback(message.id, 'helpful')}
            >{t(language, 'helpful')}</button>
            <button
              type="button"
              aria-label={t(language, 'markNotHelpful')}
              aria-pressed={message.feedback === 'not_helpful'}
              disabled={message.feedback === 'submitting'}
              onClick={() => onFeedback(message.id, 'not_helpful')}
            >{t(language, 'notHelpful')}</button>
            {message.feedback === 'error' && <span role="status">{t(language, 'feedbackNotSaved')}</span>}
            {(message.feedback === 'helpful' || message.feedback === 'not_helpful') && (
              <span role="status">{t(language, 'feedbackSaved')}</span>
            )}
          </div>
        )}
      </div>
    </article>
  )
}

export function Messages({
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
  return (
    <>
      {messages.map((message) => message.role === 'user'
        ? <UserMessage key={message.id} text={message.text} />
        : <AssistantMessage key={message.id} message={message} theme={theme} language={language} onFeedback={onFeedback} />)}
    </>
  )
}

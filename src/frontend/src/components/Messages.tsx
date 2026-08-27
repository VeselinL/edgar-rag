import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Theme } from '../hooks/useTheme'
import type { AssistantMessage as AssistantMessageType, ChatMessage } from '../types'
import { AvaAvatar } from './AvaAvatar'
import { Sources } from './Sources'
import { WaitingBubble } from './WaitingBubble'

function UserMessage({ text }: { text: string }) {
  return <div className="message message--user"><p>{text}</p></div>
}

function AssistantMessage({ message, theme }: { message: AssistantMessageType; theme: Theme }) {
  const waiting = message.state === 'waiting_for_first_token'
  return (
    <article className="message message--assistant" aria-label="AVA response">
      <div className="assistant-avatar">
        <AvaAvatar decorative theme={theme} />
        {waiting && <WaitingBubble />}
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
        {message.state === 'completed' && <span className="sr-only" role="status">AVA's response is complete.</span>}
        {(message.state === 'completed' || (message.state === 'error' && message.sources !== null)) && message.sources !== null && (
          <Sources
            sources={message.sources}
            sourceStatus={message.sourceStatus}
            malformedCount={message.malformedSourceCount}
          />
        )}
      </div>
    </article>
  )
}

export function Messages({ messages, theme }: { messages: ChatMessage[]; theme: Theme }) {
  return (
    <>
      {messages.map((message) => message.role === 'user'
        ? <UserMessage key={message.id} text={message.text} />
        : <AssistantMessage key={message.id} message={message} theme={theme} />)}
    </>
  )
}

import { useEffect, useRef } from 'react'
import type { ChatMessage } from '../types'
import { Messages } from './Messages'

export function Conversation({ messages }: { messages: ChatMessage[] }) {
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
      <div className="conversation" aria-label="Current session conversation">
        <Messages messages={messages} />
      </div>
    </div>
  )
}

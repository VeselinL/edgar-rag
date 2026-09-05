export function WaitingBubble({ activity = 'Thinking', language = 'en' }: { activity?: string; language?: 'en' | 'sr' }) {
  return (
    <div className="waiting" role="status" aria-label={`${activity} (${language === 'sr' ? 'u toku' : 'in progress'})`}>
      <span className="waiting__label">{activity}</span>
      <span aria-hidden="true" />
      <span aria-hidden="true" />
      <span aria-hidden="true" />
    </div>
  )
}

export function WaitingBubble({ activity = 'Thinking' }: { activity?: string }) {
  return (
    <div className="waiting" role="status" aria-label={`${activity} (in progress)`}>
      <span className="waiting__label">{activity}</span>
      <span aria-hidden="true" />
      <span aria-hidden="true" />
      <span aria-hidden="true" />
    </div>
  )
}

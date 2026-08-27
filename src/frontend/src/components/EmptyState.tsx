import { AvaAvatar } from './AvaAvatar'

export function EmptyState() {
  return (
    <section className="empty-state" aria-labelledby="empty-heading">
      <AvaAvatar size="large" />
      <h1 id="empty-heading">Ask AVA</h1>
      <p className="empty-state__name">Autonomous Vehicle Analyst</p>
      <p className="empty-state__description">
        Ask questions grounded in SEC 10-K filings from eleven companies in the autonomous-vehicle ecosystem.
      </p>
    </section>
  )
}

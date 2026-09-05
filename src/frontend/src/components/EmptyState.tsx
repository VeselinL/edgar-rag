import type { Theme } from '../hooks/useTheme'
import { AvaAvatar } from './AvaAvatar'
import type { Language } from '../i18n'
import { t } from '../i18n'

export function EmptyState({ theme, language }: { theme: Theme; language: Language }) {
  return (
    <section className="empty-state" aria-labelledby="empty-heading">
      <AvaAvatar size="large" theme={theme} />
      <h1 id="empty-heading">{t(language, 'askAva')}</h1>
      <p className="empty-state__name">Autonomous Vehicle Analyst</p>
      <p className="empty-state__description">
        {t(language, 'emptyDescription')}
      </p>
    </section>
  )
}

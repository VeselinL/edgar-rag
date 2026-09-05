import type { Theme } from '../hooks/useTheme'
import { AvaAvatar } from './AvaAvatar'
import type { Language } from '../i18n'
import { t } from '../i18n'

interface HeaderProps {
  theme: Theme
  language: Language
  authenticationRequired?: boolean
  authenticated?: boolean
  onSignIn?: () => void
  onSignOut?: () => void
}

export function Header({
  theme,
  language,
  authenticationRequired = false,
  authenticated = true,
  onSignIn,
  onSignOut,
}: HeaderProps) {
  return (
    <header className="header">
      <div className="header__inner">
        <div className="header__left">
          <div className="brand" aria-label="AVA, Autonomous Vehicle Analyst">
            <AvaAvatar size="small" decorative theme={theme} />
            <span className="brand__text">
              <strong>AVA</strong>
              <span>Autonomous Vehicle Analyst</span>
            </span>
          </div>
        </div>
        <div className="header-actions">
          {authenticationRequired && !authenticated && (
            <button className="header-button" type="button" onClick={onSignIn}>{t(language, 'signIn')}</button>
          )}
          {authenticationRequired && authenticated && (
            <button className="header-button" type="button" onClick={onSignOut}>{t(language, 'signOut')}</button>
          )}
        </div>
      </div>
    </header>
  )
}

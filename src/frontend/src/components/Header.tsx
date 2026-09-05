import type { Theme } from '../hooks/useTheme'
import { AvaAvatar } from './AvaAvatar'
import type { Language } from '../i18n'
import { t } from '../i18n'

function SidebarIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  )
}

interface HeaderProps {
  theme: Theme
  language: Language
  historyEnabled?: boolean
  sidebarOpen?: boolean
  onToggleSidebar?: () => void
  authenticationRequired?: boolean
  authenticated?: boolean
  onSignIn?: () => void
  onSignOut?: () => void
}

export function Header({
  theme,
  language,
  historyEnabled = false,
  sidebarOpen = false,
  onToggleSidebar,
  authenticationRequired = false,
  authenticated = true,
  onSignIn,
  onSignOut,
}: HeaderProps) {
  return (
    <header className="header">
      <div className="header__inner">
        <div className="header__left">
          {historyEnabled && (
            <button
              className="icon-button sidebar-toggle"
              type="button"
              onClick={onToggleSidebar}
              aria-label={sidebarOpen ? t(language, 'closeSidebar') : t(language, 'openSidebar')}
              aria-expanded={sidebarOpen}
            >
              <SidebarIcon />
            </button>
          )}
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

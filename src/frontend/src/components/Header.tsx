import type { Theme } from '../hooks/useTheme'
import { AvaAvatar } from './AvaAvatar'

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20.5 14.2A8.4 8.4 0 0 1 9.8 3.5 8.5 8.5 0 1 0 20.5 14.2Z" />
    </svg>
  )
}

interface HeaderProps {
  theme: Theme
  onToggleTheme: () => void
  historyEnabled?: boolean
  memoryEnabled?: boolean
  onToggleHistory?: () => void
  onNewConversation?: () => void
  onToggleMemory?: () => void
  authenticationRequired?: boolean
  authenticated?: boolean
  onSignIn?: () => void
  onSignOut?: () => void
}

export function Header({
  theme,
  onToggleTheme,
  historyEnabled = false,
  memoryEnabled = false,
  onToggleHistory,
  onNewConversation,
  onToggleMemory,
  authenticationRequired = false,
  authenticated = true,
  onSignIn,
  onSignOut,
}: HeaderProps) {
  const label = theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'
  return (
    <header className="header">
      <div className="header__inner">
        <div className="brand" aria-label="AVA, Autonomous Vehicle Analyst">
          <AvaAvatar size="small" decorative theme={theme} />
          <span className="brand__text">
            <strong>AVA</strong>
            <span>Autonomous Vehicle Analyst</span>
          </span>
        </div>
        <div className="header-actions">
          {historyEnabled && (
            <>
              <button className="header-button" type="button" onClick={onToggleHistory}>History</button>
              <button className="header-button" type="button" onClick={onNewConversation}>New chat</button>
              <button
                className={`header-button ${memoryEnabled ? 'header-button--active' : ''}`}
                type="button"
                aria-pressed={memoryEnabled}
                onClick={onToggleMemory}
              >
                {memoryEnabled ? 'Memory on' : 'Memory off'}
              </button>
            </>
          )}
          {authenticationRequired && !authenticated && (
            <button className="header-button" type="button" onClick={onSignIn}>Sign in</button>
          )}
          {authenticationRequired && authenticated && (
            <button className="header-button" type="button" onClick={onSignOut}>Sign out</button>
          )}
          <button className="icon-button" type="button" onClick={onToggleTheme} aria-label={label} title={label}>
            {theme === 'light' ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>
      </div>
    </header>
  )
}

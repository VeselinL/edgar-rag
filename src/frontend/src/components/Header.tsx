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

export function Header({ theme, onToggleTheme }: { theme: Theme; onToggleTheme: () => void }) {
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
        <button className="icon-button" type="button" onClick={onToggleTheme} aria-label={label} title={label}>
          {theme === 'light' ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
    </header>
  )
}

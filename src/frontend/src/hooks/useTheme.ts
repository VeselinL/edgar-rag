import { useCallback, useState } from 'react'
import type { PreferenceTheme } from '../types'

export type Theme = 'light' | 'dark'

function appliedTheme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(appliedTheme)
  const apply = useCallback((preference: PreferenceTheme) => {
    const next: Theme = preference === 'system'
      ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
      : preference
    document.documentElement.dataset.theme = next
    document.documentElement.style.colorScheme = next
    setTheme(next)
  }, [])
  const setThemePreference = useCallback((preference: PreferenceTheme) => apply(preference), [apply])
  return { theme, setThemePreference }
}

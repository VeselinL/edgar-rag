import { useCallback, useState } from 'react'

export type Theme = 'light' | 'dark'

function appliedTheme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(appliedTheme)
  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = current === 'light' ? 'dark' : 'light'
      document.documentElement.dataset.theme = next
      document.documentElement.style.colorScheme = next
      localStorage.setItem('ava-theme', next)
      return next
    })
  }, [])
  return { theme, toggleTheme }
}

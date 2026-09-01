const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export interface AuthSessionState {
  mode: 'none' | 'oidc'
  authenticated: boolean
}

function cookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`
  const value = document.cookie.split(';').map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
  return value ? decodeURIComponent(value.slice(prefix.length)) : null
}

export function csrfHeaders(): Record<string, string> {
  const token = cookie('ava_csrf')
  return token ? { 'X-CSRF-Token': token } : {}
}

export async function getAuthSession(): Promise<AuthSessionState> {
  const response = await fetch(`${API_BASE_URL}/api/auth/session`, {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error('Authentication status is unavailable.')
  return response.json() as Promise<AuthSessionState>
}

export function signInUrl(returnTo = `${window.location.pathname}${window.location.search}`): string {
  return `${API_BASE_URL}/api/auth/login?return_to=${encodeURIComponent(returnTo || '/')}`
}

export async function signOut(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/auth/logout`, {
    method: 'POST',
    credentials: 'include',
    headers: { ...csrfHeaders() },
  })
  if (!response.ok) throw new Error('Sign out failed.')
}

import { csrfHeaders, getAuthSession, signInUrl, signOut } from './auth'

describe('auth API', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    document.cookie = 'ava_csrf=; Max-Age=0; path=/'
  })

  it('reads authentication state with credentials but no browser token storage', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      mode: 'oidc', authenticated: true,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    await expect(getAuthSession()).resolves.toEqual({ mode: 'oidc', authenticated: true })
    expect(fetch).toHaveBeenCalledWith(
      '/api/auth/session',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('builds a local return path and sends CSRF on logout', async () => {
    document.cookie = 'ava_csrf=logout-csrf; path=/'
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))

    expect(signInUrl('/research?q=Tesla')).toContain('return_to=%2Fresearch%3Fq%3DTesla')
    expect(csrfHeaders()).toEqual({ 'X-CSRF-Token': 'logout-csrf' })
    await signOut()
    expect(fetch).toHaveBeenCalledWith(
      '/api/auth/logout',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': 'logout-csrf' },
      }),
    )
  })
})

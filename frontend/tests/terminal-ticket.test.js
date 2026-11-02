import { afterEach, beforeEach, expect, it, vi } from 'vitest'

beforeEach(() => {
  vi.resetModules()
  vi.stubGlobal('localStorage', { getItem: () => 'long-login-secret' })
})
afterEach(() => { vi.unstubAllGlobals() })

it('requests a short ticket with bearer auth and only sends ticket to WebSocket', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ticket: 'one-use-ticket', expires_at: 100 }) })
  vi.stubGlobal('fetch', fetch)
  let socket
  class WS {
    constructor(url) { this.url = url; socket = this; queueMicrotask(() => this.onopen()) }
    close() {}
  }
  vi.stubGlobal('WebSocket', WS)
  const { useTerminalStore } = await import('../src/composables/useTerminalStore')
  const store = useTerminalStore()
  await store.createSession({ id: 71, host: 'fixture' }, { cwd: '/home/lab/中文 space' })
  expect(fetch.mock.calls[0][0]).toBe('/api/servers/71/terminal-ticket')
  expect(fetch.mock.calls[0][1].headers.Authorization).toBe('Bearer long-login-secret')
  expect(JSON.parse(fetch.mock.calls[0][1].body).cwd).toBe('/home/lab/中文 space')
  expect(new URL(socket.url).searchParams.get('ticket')).toBe('one-use-ticket')
  expect(socket.url).not.toContain('long-login-secret')
  expect(new URL(socket.url).searchParams.has('token')).toBe(false)
  store.closeAllSessions()
})

it('surfaces trust-required failure before opening a socket', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: { code: 'trust_required', message: 'Verify host first' } }) }))
  class UnexpectedWS { constructor() { throw new Error('unexpected WebSocket') } }
  vi.stubGlobal('WebSocket', UnexpectedWS)
  const { useTerminalStore } = await import('../src/composables/useTerminalStore')
  const store = useTerminalStore()
  await expect(store.createSession({ id: 71 })).rejects.toThrow('Verify host first')
  expect(store.sessions.value).toHaveLength(0)
})

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
    constructor(url) { this.url = url; socket = this; queueMicrotask(() => { this.onopen(); queueMicrotask(() => this.onmessage({ data: JSON.stringify({ type: 'panellab-terminal-ready', version: 1 }) })) }) }
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


it('WebSocket open alone is not SSH ready, and failed setup preserves remote error text', async () => {
  let socket
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok:true, json:async()=>({ticket:'short'}) }))
  vi.stubGlobal('WebSocket', class { constructor() { socket=this } close() {} })
  const { useTerminalStore } = await import('../src/composables/useTerminalStore')
  const store=useTerminalStore()
  const pending=store.createSession({id:71})
  const rejection=expect(pending).rejects.toThrow('连接已关闭')
  await vi.waitFor(()=>expect(socket).toBeDefined())
  socket.onopen()
  expect(store.currentSession.value.status).toBe('connecting')
  socket.onmessage({data:'Authentication failed\r\n'})
  socket.onclose()
  await rejection
  expect(store.currentSession.value.status).toBe('error')
  expect(store.currentSession.value.buffer).toEqual(['Authentication failed\r\n'])
  store.closeAllSessions()
})
it('consumes only the initial ready control frame and preserves subsequent terminal text', async () => {
  let socket
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true,json:async()=>({ticket:'short'})}))
  vi.stubGlobal('WebSocket', class { constructor(){socket=this} close(){} })
  const {useTerminalStore}=await import('../src/composables/useTerminalStore')
  const store=useTerminalStore()
  const pending=store.createSession({id:71})
  await vi.waitFor(()=>expect(socket).toBeDefined())
  const raw=JSON.stringify({type:'panellab-terminal-ready',version:1})
  socket.onmessage({data:raw})
  await pending
  expect(store.currentSession.value.buffer).toEqual([])
  socket.onmessage({data:raw})
  expect(store.currentSession.value.buffer).toEqual([raw])
  store.closeAllSessions()
})
it('bounds a missing readiness frame',async()=>{
  vi.useFakeTimers()
  let socket
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true,json:async()=>({ticket:'short'})}))
  vi.stubGlobal('WebSocket',class{constructor(){socket=this}close(){}})
  const {useTerminalStore}=await import('../src/composables/useTerminalStore')
  const store=useTerminalStore()
  const pending=store.createSession({id:71})
  const rejection=expect(pending).rejects.toThrow('SSH 就绪超时')
  await vi.advanceTimersByTimeAsync(0)
  socket.onopen()
  await vi.advanceTimersByTimeAsync(35000)
  await rejection
  expect(store.currentSession.value.status).toBe('error')
  store.closeAllSessions()
  vi.useRealTimers()
})

it('only the first WebSocket frame may declare readiness; preceding raw text makes later ready text raw', async () => {
  let socket
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true,json:async()=>({ticket:'short'})}))
  vi.stubGlobal('WebSocket',class{constructor(){socket=this}close(){}})
  const {useTerminalStore}=await import('../src/composables/useTerminalStore')
  const store=useTerminalStore()
  const outcome=store.createSession({id:71}).then(()=> 'ready',error=>error.message)
  await vi.waitFor(()=>expect(socket).toBeDefined())
  const marker=JSON.stringify({type:'panellab-terminal-ready',version:1})
  socket.onmessage({data:'Remote setup failed\r\n'})
  socket.onmessage({data:marker})
  expect(store.currentSession.value.status).not.toBe('connected')
  expect(store.currentSession.value.buffer).toEqual(['Remote setup failed\r\n',marker])
  socket.onclose()
  expect(await outcome).toBe('连接已关闭')
  store.closeAllSessions()
})

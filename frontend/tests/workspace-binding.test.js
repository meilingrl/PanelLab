import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Workspaces from '../src/views/dashboard/Workspaces.vue'
import { useTerminalStore } from '../src/composables/useTerminalStore'
const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

let wrapper, task, server, intercept, sockets, pinnedTarget
const store = useTerminalStore()
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const response = data => ({ ok: true, json: async () => data, blob: async () => new Blob(['old file']) })
const observation = command => ({ observed_at: '2026-11-03T00:00:00Z', process_status: 'running', process: { command }, system: {}, warnings: [] })
const listing = cwd => ({ cwd, parent: cwd === task.root_path ? null : task.root_path, entries: [
 { name: 'nested', path: cwd + '/nested', type: 'directory' },
 { name: 'result.txt', path: cwd + '/result.txt', type: 'file', size: 4 },
] })
function fixture(url) {
 const path = new URL(url, 'http://fixture')
 if(url === '/api/servers') return response({ items: [server, { ...server, id: 72, host: 'second-host' }] })
 if(url === '/api/workloads') return response({ items: [task] })
 if(path.pathname.endsWith('/connection')) return response({ pinned: pinnedTarget === JSON.stringify(server), fingerprint: pinnedTarget === JSON.stringify(server) ? 'OLD_PIN' : null, auth_mode: 'password' })
 if(path.pathname.endsWith('/connection/probe')) return response({ host: server.host, port: server.port, fingerprint: 'OLD_PROBE', key_type: 'ssh-ed25519', token: 'old-token' })
 if(path.pathname.endsWith('/connection/trust')) { pinnedTarget = JSON.stringify(server); return response({ fingerprint: 'OLD_PIN', auth_mode: 'password' }) }
 if(path.pathname.endsWith('/connection/check')) return response({ ok: true, home: '/old-home', observed_at: '2026-11-03T00:00:00Z' })
 if(path.pathname.endsWith('/observation')) return response(observation('CURRENT_PROCESS'))
 if(path.pathname.endsWith('/files')) return response(listing(path.searchParams.get('path') || task.root_path))
 if(path.pathname.endsWith('/text')) return response({ text: 'CURRENT_TEXT', size: 4 })
 if(path.pathname.endsWith('/download')) return response({})
 if(path.pathname.endsWith('/terminal-ticket')) return response({ ticket: 'fixture-ticket' })
 throw new Error('Unexpected request ' + url)
}
async function click(label, scope = wrapper) {
 await scope.findAll('button').find(b => b.text().includes(label)).trigger('click')
 await flushPromises()
}
async function refresh() { await click('刷新列表') }
async function mountWorkspace() { wrapper = mount(Workspaces); await flushPromises(); return wrapper }
const detail = () => wrapper.get('[data-testid="task-detail"]')
beforeEach(() => {
 task = { id: 1, name: 'Training', kind: 'training', server_id: 71, root_path: '/old', log_path: '/old/train.log', pid: 11, process_identity: 'boot:11' }
 server = { id: 71, name: 'Lab', host: 'old-host', port: 22, username: 'lab' }
 intercept = () => undefined; sockets = []; pinnedTarget = null; push.mockClear()
 vi.stubGlobal('localStorage', { getItem: () => 'fixture-token' })
 vi.stubGlobal('fetch', vi.fn((url, options) => Promise.resolve(intercept(url, options) ?? fixture(url))))
 vi.stubGlobal('WebSocket', class {
  constructor(url) { this.url = url; this.close = vi.fn(); sockets.push(this) }
 })
 vi.stubGlobal('URL', class extends URL { static createObjectURL = vi.fn(() => 'blob:fixture'); static revokeObjectURL = vi.fn() })
 vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => { wrapper?.unmount(); store.closeAllSessions(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it.each([
 ['server', { server_id: 72 }], ['root', { root_path: '/new' }], ['log', { log_path: '/old/new.log' }],
 ['PID', { pid: 22 }], ['process identity', { process_identity: 'boot:22' }],
])('list refresh invalidates delayed observations when the same workload changes %s', async (_, change) => {
 const old = deferred(); let first = true
 intercept = url => url.endsWith('/observation') && first ? (first = false, old.promise) : undefined
 await mountWorkspace()
 task = { ...task, ...change }; await refresh()
 old.resolve(response(observation('OLD_PROCESS'))); await flushPromises()
 expect(detail().text()).toContain('CURRENT_PROCESS')
 expect(detail().text()).not.toContain('OLD_PROCESS')
})

it('unchanged binding and metadata-only list refresh preserve browsed cwd and file preview', async () => {
 await mountWorkspace(); await click('文件', detail()); await click('nested', detail()); await click('result.txt', detail())
 task = { ...task, name: 'Renamed', notes: 'new note' }; server = { ...server, name: 'Renamed lab' }
 await refresh()
 expect(detail().get('pre').text()).toBe('CURRENT_TEXT')
 expect(detail().text()).toContain('终端目录：/old/nested')
 await detail().get('[data-testid="open-terminal"]').trigger('click'); await flushPromises()
 const request = fetch.mock.calls.find(([url]) => url.endsWith('/terminal-ticket'))
 expect(JSON.parse(request[1].body).cwd).toBe('/old/nested')
 sockets[0].onmessage({ data: JSON.stringify({ type: 'panellab-terminal-ready', version: 1 }) }); await flushPromises()
 expect(push).toHaveBeenCalledWith('/terminal')
})

it('same-ID workload rebind ignores delayed files, preview and download and opens the new terminal root', async () => {
 await mountWorkspace(); await click('文件', detail()); await click('nested', detail())
 const files = deferred(), preview = deferred(), download = deferred()
 let oldFileSignal, oldDownloadSignal
 intercept = (url, options) => {
  if(url.includes('/text?')) return preview.promise
  if(url.includes('/download?')) { oldDownloadSignal = options.signal; return download.promise }
  if(url.includes('/files')) { oldFileSignal = options.signal; return files.promise }
 }
 await click('result.txt', detail()); await click('下载', detail()); await click('刷新目录', detail())
 task = { ...task, server_id: 72, root_path: '/new', pid: 22, process_identity: 'boot:22' }
 intercept = () => undefined; await refresh(); await click('文件', detail())
 files.resolve(response({ cwd: '/old/nested', entries: [{ name: 'OLD_FILE', path: '/old/old', type: 'file' }] }))
 preview.resolve(response({ text: 'OLD_PREVIEW' })); download.resolve(response({})); await flushPromises()
 expect(detail().text()).not.toContain('OLD_FILE'); expect(detail().text()).not.toContain('OLD_PREVIEW')
 expect(detail().text()).toContain('终端目录：/new')
 expect(URL.createObjectURL).not.toHaveBeenCalled()
 expect(oldFileSignal.aborted).toBe(true); expect(oldDownloadSignal?.aborted).toBe(true)
 await detail().get('[data-testid="open-terminal"]').trigger('click'); await flushPromises()
 const [url, options] = fetch.mock.calls.find(([url]) => url.endsWith('/terminal-ticket'))
 expect(url).toBe('/api/servers/72/terminal-ticket'); expect(JSON.parse(options.body).cwd).toBe('/new')
 sockets[0].onmessage({ data: JSON.stringify({ type: 'panellab-terminal-ready', version: 1 }) }); await flushPromises()
 expect(store.currentSession.value.host).toBe('second-host')
})

it.each([['host', 'new-host'], ['port', 2222], ['username', 'new-user']])('same-ID server %s refresh clears checked connection and task context', async (field, value) => {
 await mountWorkspace(); await click('获取 / 更新主机指纹'); await click('已核对，信任')
 expect(wrapper.text()).toContain('已连接'); expect(wrapper.text()).toContain('OLD_PIN')
 await click('文件', detail()); await click('nested', detail())
 const old = deferred(); intercept = url => url.endsWith('/observation') ? old.promise : undefined
 await click('概览', detail()); await click('刷新观测', detail())
 server = { ...server, [field]: value }; intercept = () => undefined; await refresh()
 old.resolve(response(observation('OLD_TARGET_PROCESS'))); await flushPromises()
 expect(wrapper.text()).not.toContain('已连接'); expect(wrapper.text()).not.toContain('OLD_PIN')
 expect(detail().text()).not.toContain('OLD_TARGET_PROCESS')
 expect(detail().text()).toContain('终端目录：/old')
})

it.each(['connection', 'probe', 'trust', 'check'])('same-ID target change ignores delayed connection %s results', async action => {
 const old = deferred()
 if(action === 'connection') intercept = url => url.endsWith('/connection') ? old.promise : undefined
 await mountWorkspace()
 if(action === 'trust' || action === 'check') await click('获取 / 更新主机指纹')
 if(action !== 'connection') {
  intercept = url => url.endsWith('/connection/' + action) ? old.promise : undefined
  await click(action === 'probe' ? '获取 / 更新主机指纹' : '已核对，信任')
 }
 server = { ...server, host: 'new-host' }; intercept = () => undefined; await refresh()
 old.resolve(response({ pinned: true, fingerprint: 'LATE_OLD_PIN', token: 'old-token', home: 'LATE_OLD_HOME', ok: true })); await flushPromises()
 expect(wrapper.text()).not.toContain('LATE_OLD'); expect(wrapper.text()).not.toContain('已连接')
 expect(wrapper.find('[data-testid="trust"]').exists()).toBe(false)
 if(action === 'trust') expect(fetch.mock.calls.filter(([url]) => url.endsWith('/connection/check'))).toHaveLength(0)
})

it.each(['ticket', 'SSH readiness'])('context switch cancels pending terminal %s without selecting a stale session', async phase => {
 const ticket = deferred()
 if(phase === 'ticket') intercept = url => url.endsWith('/terminal-ticket') ? ticket.promise : undefined
 await mountWorkspace(); await detail().get('[data-testid="open-terminal"]').trigger('click'); await flushPromises()
 const signal = fetch.mock.calls.find(([url]) => url.endsWith('/terminal-ticket'))[1].signal
 task = { ...task, root_path: '/new' }; await refresh()
 if(phase === 'ticket') ticket.resolve(response({ ticket: 'late-ticket' }))
 else sockets[0].onmessage({ data: JSON.stringify({ type: 'panellab-terminal-ready', version: 1 }) })
 await flushPromises()
 expect(signal?.aborted).toBe(true)
 expect(store.sessions.value).toHaveLength(0); expect(push).not.toHaveBeenCalled()
 if(phase === 'ticket') expect(sockets).toHaveLength(0)
 else expect(sockets[0].close).toHaveBeenCalled()
 expect(detail().get('[data-testid="open-terminal"]').attributes('disabled')).toBeUndefined()
})

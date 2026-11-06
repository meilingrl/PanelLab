import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import TaskDetail from '../src/components/workspaces/TaskDetail.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({}) }))
vi.mock('../src/composables/useTerminalStore', () => ({ useTerminalStore: () => ({}) }))
const task = { id: 1, name: 'Train', server_id: 2, root_path: '/run', log_path: '/run/log', pid: null, process_identity: null }
const server = { id: 2, host: 'fixture', port: 22, username: 'lab' }
const snapshot = (text, workload = task) => ({ text, context: { workload, server } })
const ok = data => ({ ok: true, json: async () => data })
const failure = (code, message) => ({ ok: false, status: 409, json: async () => ({ detail: { code, message } }) })
function deferred() {
 let resolve
 const promise = new Promise(done => { resolve = done })
 return { promise, resolve }
}
let wrapper, logCalls, read, hidden
function open(props = {}) {
 wrapper = mount(TaskDetail, { props: { task, server, ...props }, global: { stubs: { ObservationPanel: true } } })
}
async function tab(name) {
 await wrapper.get('[data-testid="tab-' + name + '"]').trigger('click')
 await flushPromises()
}
async function follow(enabled = true) {
 await wrapper.get('[name="follow-log"]').setValue(enabled)
 await flushPromises()
}
async function advance(ms) { await vi.advanceTimersByTimeAsync(ms); await flushPromises() }
async function visibility(value) {
 hidden = value
 document.dispatchEvent(new Event('visibilitychange'))
 await flushPromises()
}
const manual = () => wrapper.get('[data-testid="refresh-log"]').trigger('click')
beforeEach(() => {
 vi.useFakeTimers()
 hidden = false
 logCalls = []
 read = n => ok(snapshot('tail ' + n))
 vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
 vi.stubGlobal('localStorage', { getItem: () => 'fixture-token' })
 vi.stubGlobal('fetch', vi.fn(async (url, options) => {
  if (url === '/api/workloads/1/log') { logCalls.push(options); return read(logCalls.length) }
  if (url.endsWith('/files')) return ok({ cwd: '/run', entries: [{ name: 'log', path: '/run/log', type: 'file' }] })
  if (url.includes('/text?')) return ok({ text: 'file preview' })
  throw new Error('Unexpected ' + url)
 }))
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('reads the bound log on first visit, defaults off, and replaces the tail after each completed read', async () => {
 const pending = deferred()
 read = n => n === 2 ? pending.promise : ok(snapshot('tail ' + n))
 open(); expect(logCalls).toHaveLength(0)
 await tab('log'); expect(wrapper.get('pre').text()).toBe('tail 1')
 await advance(60000); expect(logCalls).toHaveLength(1)
 await follow(); await manual(); await advance(60000)
 expect(logCalls).toHaveLength(2)
 pending.resolve(ok(snapshot('rotated'))); await flushPromises()
 expect(wrapper.get('pre').text()).toBe('rotated')
 await advance(4999); expect(logCalls).toHaveLength(2)
 await advance(1); expect(wrapper.get('pre').text()).toBe('tail 3')
})

it('holds the exact snapshot and scroll position while reading older lines, then shows only the newest tail', async () => {
 open(); await tab('log'); await follow()
 const pre = wrapper.get('pre')
 Object.defineProperties(pre.element, { scrollHeight: { value: 500 }, clientHeight: { value: 100 } })
 pre.element.scrollTop = 100
 await pre.trigger('scroll'); await advance(15000)
 expect(pre.text()).toBe('tail 2'); expect(pre.element.scrollTop).toBe(100)
 expect(logCalls).toHaveLength(5)
 await tab('overview'); await tab('log')
 const restored = wrapper.get('pre')
 expect(restored.element.scrollTop).toBe(100)
 expect(restored.text()).toBe('tail 2')
 Object.defineProperties(restored.element, { scrollHeight: { value: 500 }, clientHeight: { value: 100 } })
 await wrapper.get('[data-testid="latest-log"]').trigger('click'); await flushPromises()
 expect(restored.text()).toBe('tail 6'); expect(restored.element.scrollTop).toBeGreaterThanOrEqual(400)
})

it('pauses on hidden or inactive tabs, ignores cancelled reads, and resumes only an enabled follow', async () => {
 const old = deferred(); read = n => n === 2 ? old.promise : ok(snapshot('tail ' + n))
 open(); await tab('log'); await follow()
 await visibility(true); expect(logCalls[1].signal.aborted).toBe(true)
 old.resolve(ok(snapshot('late hidden text'))); await flushPromises()
 expect(wrapper.get('pre').text()).toBe('tail 1')
 await advance(30000); expect(logCalls).toHaveLength(2)
 await visibility(false); expect(logCalls).toHaveLength(3)
 await tab('overview'); await advance(60000); expect(logCalls).toHaveLength(3)
 await tab('log'); expect(wrapper.get('pre').text()).toBe('tail 4')
 const paused = deferred(); read = () => paused.promise
 await advance(5000); await follow(false)
 expect(logCalls[4].signal.aborted).toBe(true)
 paused.resolve(ok(snapshot('late after pause'))); await advance(60000)
 expect(wrapper.get('pre').text()).toBe('tail 4'); expect(logCalls).toHaveLength(5)
})

it('backs off after failures, retains the last good text through retries and allows manual recovery', async () => {
 open(); await tab('log')
 read = () => Promise.reject(new Error('SSH offline')); await follow()
 expect(wrapper.get('pre').text()).toBe('tail 1'); expect(wrapper.text()).toContain('已过期')
 for (const delay of [10000, 20000, 40000, 60000, 60000]) {
  const count = logCalls.length
  await advance(delay - 1); expect(logCalls).toHaveLength(count)
  await advance(1); expect(logCalls).toHaveLength(count + 1)
 }
 const retry = deferred(); read = () => retry.promise
 await manual(); expect(wrapper.text()).toContain('SSH offline')
 retry.resolve(ok(snapshot('recovered'))); await flushPromises()
 expect(wrapper.get('pre').text()).toBe('recovered'); expect(wrapper.text()).not.toContain('SSH offline')
})

it.each([
 ['new context', () => ok(snapshot('foreign log', { ...task, root_path: '/changed' }))],
 ['unbound on server', () => failure('log_unbound', 'log removed')],
])('stops on %s until refreshed task metadata and a successful manual verification', async (_, mismatch) => {
 open(); await tab('log'); read = mismatch; await follow()
 expect(wrapper.get('pre').text()).toBe('tail 1'); expect(wrapper.text()).toContain('已过期')
 await wrapper.get('[data-testid="refresh-task-info"]').trigger('click')
 expect(wrapper.emitted('refresh-task-info')).toHaveLength(1)
 expect(wrapper.get('[data-testid="refresh-log"]').element.disabled).toBe(true)
 await advance(120000); expect(logCalls).toHaveLength(2)
 read = () => ok(snapshot('verified')); await wrapper.setProps({ metadataRevision: 1 })
 await manual(); await flushPromises(); expect(wrapper.get('pre').text()).toBe('verified')
})

it('invalidates late reads on same-id rebinding and unmount and resets follow to off', async () => {
 const old = deferred(); read = n => n === 2 ? old.promise : ok(snapshot('original'))
 open(); await tab('log'); await follow()
 const updated = { ...task, log_path: '/run/new' }
 await wrapper.setProps({ task: updated })
 expect(logCalls[1].signal.aborted).toBe(true)
 read = () => ok(snapshot('new log', updated)); await tab('log')
 old.resolve(ok(snapshot('late old log'))); await advance(60000)
 expect(wrapper.get('pre').text()).toBe('new log')
 expect(wrapper.get('[name="follow-log"]').element.checked).toBe(false)
 expect(logCalls).toHaveLength(3)
 const late = deferred(); read = () => late.promise; await follow()
 wrapper.unmount(); wrapper = null
 expect(logCalls[3].signal.aborted).toBe(true)
 late.resolve(ok(snapshot('late unmount', updated))); await advance(60000)
 expect(logCalls).toHaveLength(4); expect(vi.getTimerCount()).toBe(0)
})

it('skips unbound tasks and never polls a file preview, including the file at the bound log path', async () => {
 open({ task: { ...task, log_path: null } }); await tab('log'); await advance(60000)
 expect(logCalls).toHaveLength(0); expect(wrapper.text()).toContain('尚未选择日志')
 await wrapper.setProps({ task }); await tab('files')
 await wrapper.findAll('button').find(button => button.text().includes('▤ log')).trigger('click'); await flushPromises()
 expect(wrapper.get('pre').text()).toBe('file preview')
 expect(wrapper.find('[name="follow-log"]').exists()).toBe(false)
 await advance(60000); expect(logCalls).toHaveLength(0)
 expect(fetch.mock.calls.filter(([url]) => url.includes('/text?'))).toHaveLength(1)
})

it('invalid intervals abort in-flight follow; a valid interval resumes on its new schedule', async () => {
 const late = deferred()
 read = n => n === 2 ? late.promise : ok(snapshot('tail ' + n))
 open(); await tab('log'); await follow()
 const interval = wrapper.get('[name="log-interval"]')
 await interval.setValue('61')
 expect(interval.attributes('aria-invalid')).toBe('true'); expect(logCalls[1].signal.aborted).toBe(true)
 late.resolve(ok(snapshot('old cadence'))); await advance(60000)
 expect(wrapper.get('pre').text()).toBe('tail 1'); expect(logCalls).toHaveLength(2)
 await interval.setValue('2'); await flushPromises(); expect(wrapper.get('pre').text()).toBe('tail 3')
 await advance(1999); expect(logCalls).toHaveLength(3)
 await advance(1); expect(wrapper.get('pre').text()).toBe('tail 4')
})

it('preserves lines when the read settles before the browser delivers its scroll event', async () => {
 const pending = deferred()
 read = n => n === 2 ? pending.promise : ok(snapshot('reading now'))
 open(); await tab('log'); await follow()
 const pre = wrapper.get('pre')
 Object.defineProperties(pre.element, { scrollHeight: { value: 500 }, clientHeight: { value: 100 } })
 pre.element.scrollTop = 100
 pending.resolve(ok(snapshot('new tail'))); await flushPromises()
 expect(pre.text()).toBe('reading now'); expect(pre.element.scrollTop).toBe(100)
})

it('retries a cancelled first manual read when the user returns', async () => {
 const first = deferred()
 read = n => n === 1 ? first.promise : ok(snapshot('current'))
 open(); await tab('log')
 await tab('overview'); expect(logCalls[0].signal.aborted).toBe(true)
 await tab('log'); expect(logCalls).toHaveLength(2)
 first.resolve(ok(snapshot('cancelled'))); await flushPromises()
 expect(wrapper.get('pre').text()).toBe('current')
 await tab('overview'); await tab('log'); expect(logCalls).toHaveLength(2)
})

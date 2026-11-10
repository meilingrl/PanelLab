import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ObservationPanel from '../src/components/workspaces/ObservationPanel.vue'
import Workspaces from '../src/views/dashboard/Workspaces.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../src/composables/useTerminalStore', () => ({ useTerminalStore: () => ({}) }))
const server = { id: 71, name: 'Lab', host: 'fixture', port: 22, username: 'lab' }
const task = { id: 1, name: 'Training', kind: 'training', server_id: 71, root_path: '/run', log_path: null, pid: 42, process_identity: 'boot:42' }
function sample(seconds = 10, user = 100, idle = 100) {
 return {
  workload_id: 1, observed_at: new Date(Date.parse('2026-11-03T00:00:00Z') + seconds * 1000).toISOString(),
  context: { workload: { ...task }, server: { ...server } },
  sampling: { boot_id: 'boot-fixture', monotonic_seconds: seconds },
  process_status: 'running', process: { command: `sample-${seconds}` },
  system: {
   cpu: { percent: 99, ticks: { user, nice: 0, system: 0, idle, iowait: 0, irq: 0, softirq: 0, steal: 0 }, load_average: [0, 0, 0] },
   network: [{ name: 'eth0', ifindex: 7, received_bytes: seconds * 100, sent_bytes: seconds * 50 }],
  }, warnings: [],
 }
}
const response = data => ({ ok: true, json: async () => data })
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
let wrappers, observationCalls, responder, hidden
function mountPanel(extra = {}) {
 const w = mount(ObservationPanel, { props: { task, server, ...extra } }); wrappers.push(w); return w
}
async function advance(ms) { await vi.advanceTimersByTimeAsync(ms); await flushPromises() }
async function manual(w) { await w.get('[data-testid="refresh-observation"]').trigger('click'); await flushPromises() }
async function follow(w, value = true) { await w.get('[name="follow-observation"]').setValue(value); await flushPromises() }
function visibility(value) { hidden = value; document.dispatchEvent(new Event('visibilitychange')) }
beforeEach(() => {
 vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-03T00:00:00Z'))
 wrappers = []; observationCalls = []; hidden = false; responder = () => response(sample())
 vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
 vi.stubGlobal('localStorage', { getItem: () => 'fixture-token' })
 vi.stubGlobal('fetch', vi.fn((url, options) => {
  if (url.endsWith('/observation')) { observationCalls.push({ url, options }); return Promise.resolve(responder(observationCalls.length)) }
  if (url === '/api/servers') return Promise.resolve(response({ items: [server] }))
  if (url === '/api/workloads') return Promise.resolve(response({ items: [task] }))
  if (url.endsWith('/connection')) return Promise.resolve(response({ pinned: false }))
  throw new Error('Unexpected request ' + url)
 }))
})
afterEach(() => { wrappers.forEach(w => w.unmount()); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('derives interval CPU and interface rates only after a second sample; follow defaults off', async () => {
 responder = n => response(n === 1 ? sample() : sample(15, 120, 130))
 const w = mountPanel(); await flushPromises()
 expect(w.text()).toContain('区间 CPU'); expect(w.text()).not.toContain('99%')
 expect(w.get('[data-testid="cpu-rate"]').text()).toBe('不可用')
 await advance(60000); expect(observationCalls).toHaveLength(1)
 await manual(w)
 expect(w.get('[data-testid="cpu-rate"]').text()).toBe('不可用')
 // The minute-old first sample expired; a third usable sample can form a fresh interval.
 responder = () => response(sample(20, 140, 160)); await manual(w)
 expect(w.get('[data-testid="cpu-rate"]').text()).toBe('40.0%')
 expect(w.text()).toContain('约 100 B/s'); expect(w.text()).toContain('5.0 秒')
 expect(w.text()).toContain('累计接收 2.0 KiB')
})
it('shares pending work between follow/manual and schedules only after settlement', async () => {
 const pending = deferred(); responder = n => n === 2 ? pending.promise : response(sample(n * 5))
 const w = mountPanel(); await flushPromises(); await follow(w)
 expect(observationCalls).toHaveLength(2)
 await manual(w); await advance(30000); expect(observationCalls).toHaveLength(2)
 pending.resolve(response(sample(15))); await flushPromises()
 await advance(4999); expect(observationCalls).toHaveLength(2)
 await advance(1); expect(observationCalls).toHaveLength(3)
})
it.each(['pause', 'hidden', 'tab', 'unmount'])('%s cancels pending work and ignores late data', async reason => {
 const pending = deferred(); responder = n => n === 2 ? pending.promise : response(sample(n * 5))
 const w = mountPanel(); await flushPromises(); await follow(w)
 const signal = observationCalls[1].options.signal
 if (reason === 'pause') await follow(w, false)
 if (reason === 'hidden') { visibility(true); await flushPromises() }
 if (reason === 'tab') await w.setProps({ active: false })
 if (reason === 'unmount') w.unmount()
 expect(signal.aborted).toBe(true)
 pending.resolve(response({ ...sample(20), warnings: ['LATE_OBSERVATION'] })); await flushPromises()
 await advance(60000); expect(observationCalls).toHaveLength(2)
 if (reason !== 'unmount') expect(w.text()).not.toContain('LATE_OBSERVATION')
 if (reason === 'hidden') { visibility(false); await flushPromises(); expect(observationCalls).toHaveLength(3) }
 if (reason === 'tab') { await w.setProps({ active: true }); await flushPromises(); expect(observationCalls).toHaveLength(3) }
})
it('retains data/error during retry, backs off and resets the baseline after failure', async () => {
 const retry = deferred()
 responder = n => n === 1 ? response(sample()) : n === 2 || n === 3 ? Promise.reject(new Error('offline')) : n === 4 ? retry.promise : response(sample(20, 140, 160))
 const w = mountPanel(); await flushPromises(); await follow(w)
 expect(w.text()).toContain('offline'); expect(w.text()).toContain('sample-10')
 await advance(9999); expect(observationCalls).toHaveLength(2)
 await advance(1); expect(observationCalls).toHaveLength(3)
 await advance(19999); expect(observationCalls).toHaveLength(3)
 await advance(1); expect(observationCalls).toHaveLength(4)
 expect(w.text()).toContain('offline'); expect(w.text()).toContain('已过期')
 retry.resolve(response(sample(15, 120, 130))); await flushPromises()
 expect(w.text()).not.toContain('offline'); expect(w.get('[data-testid="cpu-rate"]').text()).toBe('不可用')
 await advance(5000); expect(observationCalls).toHaveLength(5)
 expect(w.get('[data-testid="cpu-rate"]').text()).toBe('40.0%')
})
it.each(['', '1', '61', '2.5'])('pauses invalid interval %s without clamping', async value => {
 const w = mountPanel(); await flushPromises(); await follow(w)
 await w.get('[name="observation-interval"]').setValue(value)
 expect(w.get('[role="alert"]').text()).toContain('2–60')
 await advance(120000); expect(observationCalls).toHaveLength(2)
 await w.get('[name="observation-interval"]').setValue('2'); await flushPromises()
 expect(observationCalls).toHaveLength(3)
})
it('accepts 60 seconds and ages snapshots using twice the configured interval', async () => {
 const w = mountPanel(); await flushPromises()
 await w.get('[name="observation-interval"]').setValue('60')
 await advance(60000); expect(w.text()).not.toContain('已过期')
 await advance(61000); expect(w.text()).toContain('已过期')
})
it.each(['context', 'host', 'pid'])('rejects missing/mismatched %s and blocks follow', async field => {
 responder = n => {
  const data = sample(n * 5)
  if (n > 1) {
   data.warnings = ['WRONG_CONTEXT']
   if (field === 'context') delete data.context
   if (field === 'host') data.context.server.host = 'other'
   if (field === 'pid') data.context.workload.pid = 999
  }
  return response(data)
 }
 const w = mountPanel(); await flushPromises(); await follow(w)
 expect(w.text()).toContain('任务信息'); expect(w.text()).toContain('已过期')
 expect(w.text()).not.toContain('WRONG_CONTEXT'); expect(w.text()).toContain('sample-5')
 expect(w.get('[data-testid="refresh-observation"]').attributes('disabled')).toBeDefined()
 await w.get('[data-testid="refresh-task-info"]').trigger('click')
 expect(w.emitted('refresh-task-info')).toHaveLength(1)
 await advance(120000); expect(observationCalls).toHaveLength(2)
})
it('refreshes unchanged metadata through the parent, requires manual retry, then recovers follow', async () => {
 responder = n => { const data = sample(n * 5); if (n === 2) data.context.server.host = 'old-target'; return response(data) }
 const w = mount(Workspaces); wrappers.push(w); await flushPromises()
 const panel = w.getComponent(ObservationPanel)
 await follow(panel); expect(observationCalls).toHaveLength(2)
 await panel.get('[data-testid="refresh-task-info"]').trigger('click'); await flushPromises()
 expect(fetch.mock.calls.filter(([url]) => url === '/api/servers')).toHaveLength(2)
 expect(panel.get('[name="follow-observation"]').element.checked).toBe(true)
 expect(panel.get('[data-testid="refresh-observation"]').attributes('disabled')).toBeUndefined()
 await advance(120000); expect(observationCalls).toHaveLength(2)
 await manual(panel); expect(observationCalls).toHaveLength(3)
 expect(panel.text()).not.toContain('已过期')
 await advance(5000); expect(observationCalls).toHaveLength(4)
})
it('same-ID changes abort delayed data, while display-only metadata preserves follow state', async () => {
 const pending = deferred(); responder = n => n === 2 ? pending.promise : response(sample(n * 5))
 const w = mountPanel(); await flushPromises(); await follow(w)
 await w.setProps({ task: { ...task, name: 'Renamed', notes: 'memo' }, server: { ...server, name: 'Renamed lab' } })
 expect(observationCalls[1].options.signal.aborted).toBe(false)
 await w.setProps({ server: { ...server, host: 'changed' } }); await flushPromises()
 expect(observationCalls[1].options.signal.aborted).toBe(true)
 pending.resolve(response({ ...sample(15), warnings: ['OLD_HOST'] })); await flushPromises()
 expect(w.text()).not.toContain('OLD_HOST'); expect(w.text()).not.toContain('sample-10')
})
it('a timely second sample displays valid rates, and pausing requires a new baseline', async () => {
 responder = n => response(sample(5 * n, 20 * n, 30 * n))
 const w = mountPanel(); await flushPromises()
 await manual(w); expect(w.get('[data-testid="cpu-rate"]').text()).toBe('40.0%')
 await follow(w); await follow(w, false)
 expect(w.get('[data-testid="cpu-rate"]').text()).toBe('不可用')
 await manual(w); expect(w.get('[data-testid="cpu-rate"]').text()).toBe('不可用')
 await manual(w); expect(w.get('[data-testid="cpu-rate"]').text()).toBe('40.0%')
})
it('uses the two second lower bound and caps consecutive failure backoff at sixty seconds', async () => {
 responder = n => n === 1 ? response(sample()) : Promise.reject(new Error('offline'))
 const w = mountPanel(); await flushPromises()
 await w.get('[name="observation-interval"]').setValue('2'); await follow(w)
 for (const seconds of [4, 8, 16, 32, 60, 60]) {
  const before = observationCalls.length
  await advance(seconds * 1000 - 1); expect(observationCalls).toHaveLength(before)
  await advance(1); expect(observationCalls).toHaveLength(before + 1)
 }
})
it('manual retry bypasses backoff while remaining single flight and retaining stale evidence', async () => {
 const retry = deferred()
 responder = n => n === 1 ? response(sample()) : n === 2 ? Promise.reject(new Error('offline')) : retry.promise
 const w = mountPanel(); await flushPromises(); await follow(w)
 await manual(w); expect(observationCalls).toHaveLength(3)
 expect(w.text()).toContain('offline'); expect(w.text()).toContain('已过期')
 await manual(w); await advance(30000); expect(observationCalls).toHaveLength(3)
 retry.resolve(response(sample(15, 120, 130))); await flushPromises()
 expect(w.text()).not.toContain('offline'); expect(w.get('[data-testid="cpu-rate"]').text()).toBe('不可用')
})
it('an invalid interval aborts pending work, and restoring it starts a new generation', async () => {
 const old = deferred(); responder = n => n === 2 ? old.promise : response(sample(n * 5))
 const w = mountPanel(); await flushPromises(); await follow(w)
 await w.get('[name="observation-interval"]').setValue('1')
 expect(observationCalls[1].options.signal.aborted).toBe(true)
 await w.get('[name="observation-interval"]').setValue('5'); await flushPromises()
 expect(observationCalls).toHaveLength(3)
 old.resolve(response({ ...sample(25), warnings: ['LATE_INVALID_INTERVAL'] })); await flushPromises()
 expect(w.text()).not.toContain('LATE_INVALID_INTERVAL')
})
it('overview activity comes from the real task tabs and does not poll log, file or run endpoints', async () => {
 const w = mount(Workspaces); wrappers.push(w); await flushPromises()
 const panel = w.getComponent(ObservationPanel); await follow(panel)
 // This fixture has no log/file/runs routes: accidental automatic reads throw.
 await w.get('[data-testid="tab-log"]').trigger('click'); await flushPromises()
 const requests = observationCalls.length
 await advance(60000); expect(observationCalls).toHaveLength(requests)
 await w.get('[data-testid="tab-overview"]').trigger('click'); await flushPromises()
 expect(observationCalls).toHaveLength(requests + 1)
})
it('failed task-info reload cannot unlock a context mismatch; repeated mismatch remains blocked', async () => {
 responder = () => { const data = sample(); data.context.server.host = 'wrong'; return response(data) }
 const w = mount(Workspaces); wrappers.push(w); await flushPromises()
 const panel = w.getComponent(ObservationPanel)
 fetch.mockImplementation((url, options) => {
  if (url === '/api/servers') return Promise.reject(new Error('list offline'))
  if (url === '/api/workloads') return Promise.resolve(response({ items: [task] }))
  throw new Error('Unexpected ' + url)
 })
 await panel.get('[data-testid="refresh-task-info"]').trigger('click'); await flushPromises()
 expect(panel.get('[data-testid="refresh-observation"]').attributes('disabled')).toBeDefined()
 expect(w.text()).toContain('list offline')
 await advance(120000); expect(observationCalls).toHaveLength(1)
})
it('reboot, recreated interfaces, counter resets and clock gaps prime again before rates recover', async () => {
 const samples = [sample(10), sample(15, 120, 130), sample(20, 140, 160), sample(25, 160, 190), sample(30, 180, 220), sample(35, 200, 250), sample(40, 199, 280), sample(45, 219, 310), sample(200, 239, 340), sample(205, 259, 370)]
 samples.slice(2).forEach(data => { data.sampling.boot_id = 'new-boot' })
 samples.slice(4).forEach(data => { data.system.network[0].ifindex = 8 })
 samples[6].system.network[0].received_bytes = 3000
 samples[7].system.network[0].received_bytes = 3500
 responder = n => response(samples[n - 1])
 const w = mountPanel(); await flushPromises()
 for (const [cpu, network] of [['40.0%', true], ['不可用', false], ['40.0%', true], ['40.0%', false], ['40.0%', true], ['不可用', false], ['40.0%', true], ['不可用', false], ['40.0%', true]]) {
  await manual(w)
  expect(w.get('[data-testid="cpu-rate"]').text()).toBe(cpu)
  expect(w.text().includes('约 100 B/s')).toBe(network)
 }
})
it('hidden/manual mode stops timers, ages on return and requires an explicit fresh baseline', async () => {
 responder = n => response(sample(n * 5, n * 20, n * 30))
 const w = mountPanel(); await flushPromises(); await manual(w)
 expect(w.get('[data-testid="cpu-rate"]').text()).toBe('40.0%')
 visibility(true); await flushPromises(); expect(vi.getTimerCount()).toBe(0)
 await advance(120000); visibility(false); await flushPromises()
 expect(observationCalls).toHaveLength(2); expect(w.text()).toContain('已过期')
 expect(w.get('[data-testid="cpu-rate"]').text()).toBe('不可用')
 await manual(w); expect(w.get('[data-testid="cpu-rate"]').text()).toBe('不可用')
 w.unmount(); expect(vi.getTimerCount()).toBe(0)
})

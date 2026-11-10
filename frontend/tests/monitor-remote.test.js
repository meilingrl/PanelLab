import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
const charts = vi.hoisted(() => [])
vi.mock('chart.js', () => ({ Chart: class { static register() {} constructor() { this.destroyed = false; this.data = { labels: [], datasets: [{}, {}] }; charts.push(this) } update() {} destroy() { this.destroyed = true } }, registerables: [] }))
import Monitor from '../src/views/dashboard/Monitor.vue'

const RouterLink = { template: '<a><slot /></a>' }
const local = { cpu_percent: 5, memory: { percent: 10, used_mb: 1, total_mb: 10 }, disk: [], network: null }
const remote = { target: 'server:71', cpu_percent: null, load_average: [0.5, 0.4, 0.3], memory: { percent: 75, used_mb: 6144, total_mb: 8192 },
  disk: [{ mountpoint: '/data', total_gb: 100, used_gb: 70, free_gb: 25, percent: 73.7 }],
  network: { bytes_recv: 1, bytes_sent: 2, rate_recv_kbps: null, rate_sent_kbps: null, network_history: [] },
  gpu: [{ index: 0, name: 'A100', utilization_percent: 87, memory_used_bytes: 1024 ** 3, memory_total_bytes: 40 * 1024 ** 3 }],
  warnings: ['Process visibility is restricted'] }
let wrappers = [], pending = {}, legacy
const response = (data, ok = true, status = ok ? 200 : 503) => ({ ok, status, json: async () => data })
const statsCalls = () => fetch.mock.calls.filter(([u]) => u.startsWith('/api/monitor/stats'))
beforeEach(() => {
  legacy = { legacy_configured: true, host: 'old.invalid', port: 22, username: 'root' }
  pending = {}
  vi.stubGlobal('localStorage', { getItem: () => 'fixture-token' })
  vi.stubGlobal('fetch', vi.fn(async (url, o = {}) => {
    if (url === '/api/monitor/remote-config' && o.method === 'DELETE') { legacy = { legacy_configured: false }; return response(legacy) }
    if (url === '/api/monitor/remote-config') return response(legacy)
    if (url === '/api/monitor/hosts') return response({ hosts: [{ id: 'local', label: '本机', pinned: true },
      { id: 'server:71', label: 'trainer · 10.0.0.2', pinned: true }, { id: 'server:73', label: 'web · 10.0.0.3', pinned: false }] })
    if (url.startsWith('/api/monitor/processes')) return response({ processes: [] })
    if (url === '/api/monitor/stats?target=local') return response(local)
    if (url === '/api/monitor/stats?target=server%3A71') return pending.remote ? new Promise(r => { pending.release = () => r(response(remote)) }) : response(remote)
    if (url === '/api/monitor/stats?target=server%3A73') return response({ detail: { code: 'trust_required', message: 'Verify and trust the SSH host fingerprint first' } }, false, 409)
    throw Error('Unexpected ' + url)
  }))
})
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers = []; vi.unstubAllGlobals() })
const mountMonitor = async () => { const w = mount(Monitor, { global: { stubs: { RouterLink, 'router-link': RouterLink } } }); wrappers.push(w); await flushPromises(); return w }

it('lists trusted workspace servers and disables untrusted ones; no SSH form remains', async () => {
  const w = await mountMonitor()
  const options = w.findAll('#monitor-target option')
  expect(options.map(o => o.text())).toEqual(['本机', 'trainer · 10.0.0.2', 'web · 10.0.0.3（需先在任务工作区确认指纹）'])
  expect(options[2].attributes('disabled')).toBeDefined()
  expect(w.find('input[type="password"]').exists()).toBe(false)
  expect(w.text()).not.toContain('psutil')
})

it('shows remote CPU baseline, disk, GPU and warnings, hiding local-only processes', async () => {
  const w = await mountMonitor()
  await w.get('#monitor-target').setValue('server:71'); await flushPromises()
  expect(statsCalls().at(-1)[0]).toBe('/api/monitor/stats?target=server%3A71')
  expect(w.text()).toContain('等待第二次采样')
  expect(w.text()).toContain('负载 0.5 / 0.4 / 0.3')
  expect(w.text()).toContain('/data')
  expect(w.get('[data-testid="gpu-row"]').text()).toContain('A100')
  expect(w.get('[data-testid="monitor-warnings"]').text()).toContain('Process visibility')
  expect(w.text()).not.toContain('进程列表')
})

it('shows structured API errors as text', async () => {
  const w = await mountMonitor()
  await w.get('#monitor-target').setValue('server:73'); await flushPromises()
  expect(w.text()).toContain('Verify and trust the SSH host fingerprint first')
  expect(w.text()).not.toContain('[object Object]')
})

it('ignores a slow remote reply after switching back to local', async () => {
  pending.remote = true
  const w = await mountMonitor()
  await w.get('#monitor-target').setValue('server:71'); await flushPromises()
  await w.get('#monitor-target').setValue('local'); await flushPromises()
  pending.release(); await flushPromises()
  expect(w.text()).toContain('进程列表')
  expect(w.find('[data-testid="gpu-row"]').exists()).toBe(false)
})

it('reports the legacy unpinned config and deletes it on request', async () => {
  const w = await mountMonitor()
  expect(w.get('[data-testid="legacy-remote"]').text()).toContain('root@old.invalid:22')
  await w.get('[data-testid="legacy-remote"] button').trigger('click'); await flushPromises()
  expect(fetch.mock.calls.some(([u, o]) => u === '/api/monitor/remote-config' && o?.method === 'DELETE')).toBe(true)
  expect(w.find('[data-testid="legacy-remote"]').exists()).toBe(false)
})

it('rebuilds the traffic chart after a failed poll replaces its canvas, and tolerates unknown disk percent', async () => {
  vi.useFakeTimers()
  try {
    charts.length = 0
    const history = [{ observed_at: '2026-11-10T00:00:05Z', rate_recv_kbps: 1, rate_sent_kbps: 1 }]
    let fail = false
    fetch.mockImplementation(async (url, o = {}) => {
      if (url === '/api/monitor/stats?target=server%3A71') return fail
        ? response({ detail: { code: 'connection_failed', message: 'SSH connection or authentication failed' } }, false)
        : response({ ...remote, disk: [{ mountpoint: '/empty', total_gb: 0.1, used_gb: 0, free_gb: 0, percent: null }],
          network: { ...remote.network, network_history: history } })
      if (url === '/api/monitor/remote-config') return response({ legacy_configured: false })
      if (url === '/api/monitor/hosts') return response({ hosts: [{ id: 'local', label: '本机', pinned: true }, { id: 'server:71', label: 'trainer', pinned: true }] })
      if (url.startsWith('/api/monitor/processes')) return response({ processes: [] })
      return response(local)
    })
    const w = await mountMonitor()
    await w.get('#monitor-target').setValue('server:71'); await flushPromises()
    expect(charts).toHaveLength(1)
    expect(w.findAll('.gauge-value-sm').at(-1).text()).toBe('—')
    fail = true; await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(w.text()).toContain('SSH connection or authentication failed')
    fail = false; await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(charts).toHaveLength(2)
    expect(charts[0].destroyed).toBe(true)
  } finally { vi.useRealTimers() }
})

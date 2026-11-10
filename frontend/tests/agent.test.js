import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Agent from '../src/views/dashboard/Agent.vue'

const RouterLink = { props: ['to'], template: '<a :href="to"><slot /></a>' }
const response = (data, ok = true, status = ok ? 200 : 503) => ({ ok, status, json: async () => data })
const failure = (code, message, status) => response({ detail: { code, message } }, false, status)
const job = (over = {}) => ({ id: 'backup', name: '每日备份', revision: 3, paused: false, command: ['/bin/true'], workdir: '/srv',
  schedule: { cron: ['0 3 * * *'], tz: 'Asia/Shanghai' }, timeout_seconds: 3600, retries: 0, env: {},
  secret_env: [{ name: 'API_TOKEN', status: '已设置' }], ...over })
const run = (over = {}) => ({ id: 5, job_id: 'backup', trigger: 'schedule', status: 'failed', scheduled_for: '2026-11-11T03:00:00Z',
  attempt: 1, started_at: '2026-11-11T03:00:00Z', finished_at: '2026-11-11T03:00:12Z', exit_code: 1, output_bytes: 3, truncated: false, ...over })
const status = (over = {}) => ({ version: '1', protocol_version: 1, hostname: 'trainer', job_count: 1, running: [], firing: [], ...over })
let wrappers = [], routes
const calls = () => fetch.mock.calls.map(([u, o]) => `${o?.method || 'GET'} ${u}`)
const count = (c) => calls().filter(x => x === c).length
beforeEach(() => {
  routes = {}
  vi.stubGlobal('localStorage', { getItem: () => 'fixture-token' })
  vi.stubGlobal('fetch', vi.fn(async (url, o = {}) => {
    const key = `${o.method || 'GET'} ${url}`
    for (const [pattern, handler] of Object.entries(routes)) if (key === pattern || (pattern.endsWith('*') && key.startsWith(pattern.slice(0, -1)))) return handler(url, o)
    if (key === 'GET /api/monitor/hosts') return response({ hosts: [{ id: 'local', label: '本机', pinned: true },
      { id: 'server:71', label: 'trainer · 10.0.0.2', pinned: true }, { id: 'server:72', label: 'web · 10.0.0.3', pinned: true },
      { id: 'server:73', label: 'raw · 10.0.0.4', pinned: false }] })
    if (key === 'GET /api/servers/71/agent') return response(status())
    if (key === 'GET /api/servers/71/agent/jobs') return response({ jobs: [job()] })
    if (key.startsWith('GET /api/servers/71/agent/jobs/backup/runs')) return response({ runs: [run()] })
    if (key === 'POST /api/servers/71/agent/schedule-preview') return response({ tz: 'Asia/Shanghai', runs: [{ utc: 'u', local: '2026-11-12 03:00' }] })
    throw Error('Unexpected ' + key)
  }))
})
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers = []; vi.unstubAllGlobals(); vi.useRealTimers() })
const mountAgent = async () => { const w = mount(Agent, { global: { stubs: { RouterLink, 'router-link': RouterLink } } }); wrappers.push(w); await flushPromises(); return w }

it('shows install guide when agent not installed', async () => {
  routes['GET /api/servers/71/agent'] = () => failure('agent_not_installed', 'Agent 未安装', 404)
  const w = await mountAgent()
  expect(w.get('[data-testid="agent-not-installed"]').text()).toContain('未安装')
  expect(w.find('[data-testid="job-list"]').exists()).toBe(false)
})

it('explains permission error with relogin hint', async () => {
  routes['GET /api/servers/71/agent'] = () => failure('agent_permission', '', 403)
  const w = await mountAgent()
  expect(w.get('[data-testid="agent-error"]').text()).toContain('请重新登录 SSH，或确认当前 SSH 用户已加入 panellab 组')
  routes['GET /api/servers/71/agent'] = () => failure('agent_unreachable', 'x', 503)
  await w.get('#agent-server').setValue('server:72'); await w.get('#agent-server').setValue('server:71'); await flushPromises()
  expect(w.get('[data-testid="agent-error"]').text()).toContain('sudo systemctl status panellab-agent')
})

it('lists jobs with next run and last status in Chinese; only trusted servers are selectable', async () => {
  const w = await mountAgent()
  const options = w.findAll('#agent-server option')
  expect(options.map(o => o.text())).toEqual(['trainer · 10.0.0.2', 'web · 10.0.0.3', 'raw · 10.0.0.4（需先在任务工作区确认指纹）'])
  expect(options[2].attributes('disabled')).toBeDefined()
  const row = w.get('[data-testid="job-row"]').text()
  expect(row).toContain('每日备份'); expect(row).toContain('0 3 * * *'); expect(row).toContain('Asia/Shanghai')
  expect(row).toContain('2026-11-12 03:00'); expect(row).toContain('失败'); expect(row).toContain('12 秒')
  await w.setProps({}); await flushPromises()
  expect(count('POST /api/servers/71/agent/schedule-preview')).toBe(1)
})

it('firing alerts are shown above jobs', async () => {
  routes['GET /api/servers/71/agent'] = () => response(status({ firing: [{ key: 'job:backup', since: '2026-11-11T03:00:12Z', message: '作业失败：每日备份' }] }))
  const w = await mountAgent()
  const alerts = w.get('[data-testid="firing-alerts"]'); expect(alerts.text()).toContain('作业失败：每日备份')
  expect(alerts.element.compareDocumentPosition(w.get('[data-testid="job-list"]').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it('run now / pause / resume call the API and refresh', async () => {
  let paused = false
  routes['GET /api/servers/71/agent/jobs'] = () => response({ jobs: [job({ paused })] })
  routes['POST /api/servers/71/agent/jobs/backup/run'] = () => response({ run_id: 9 })
  routes['POST /api/servers/71/agent/jobs/backup/pause'] = () => { paused = true; return response(job({ paused })) }
  routes['POST /api/servers/71/agent/jobs/backup/resume'] = () => { paused = false; return response(job({ paused })) }
  routes['GET /api/servers/71/agent/runs/9/output*'] = () => response({ data: '', offset: 0, eof: true, truncated: false })
  const w = await mountAgent()
  await w.get('[data-testid="job-row"]').trigger('click'); await flushPromises()
  expect(w.get('[data-testid="job-detail"]').text()).toContain('已设置'); expect(w.text()).toContain('API_TOKEN')
  const before = count('GET /api/servers/71/agent/jobs')
  await w.get('[data-testid="run-now"]').trigger('click'); await flushPromises()
  expect(count('POST /api/servers/71/agent/jobs/backup/run')).toBe(1)
  expect(count('GET /api/servers/71/agent/jobs')).toBe(before + 1)
  await w.get('[data-testid="pause-job"]').trigger('click'); await flushPromises()
  expect(count('POST /api/servers/71/agent/jobs/backup/pause')).toBe(1)
  expect(w.get('[data-testid="job-row"]').text()).toContain('已暂停')
  await w.get('[data-testid="resume-job"]').trigger('click'); await flushPromises()
  expect(count('POST /api/servers/71/agent/jobs/backup/resume')).toBe(1)
  expect(w.find('[data-testid="pause-job"]').exists()).toBe(true)
})

it('delete needs confirmation', async () => {
  routes['DELETE /api/servers/71/agent/jobs/backup'] = () => response({ ok: true })
  const w = await mountAgent()
  await w.get('[data-testid="job-row"]').trigger('click'); await flushPromises()
  await w.get('[data-testid="delete-job"]').trigger('click'); await flushPromises()
  expect(count('DELETE /api/servers/71/agent/jobs/backup')).toBe(0)
  await w.get('[data-testid="confirm-delete-job"]').trigger('click'); await flushPromises()
  expect(count('DELETE /api/servers/71/agent/jobs/backup')).toBe(1)
  expect(w.find('[data-testid="job-detail"]').exists()).toBe(false)
})

it('late reply from previous server is ignored', async () => {
  let release
  routes['GET /api/servers/71/agent'] = () => new Promise(r => { release = () => r(response(status({ hostname: 'old-host' }))) })
  routes['GET /api/servers/72/agent'] = () => response(status({ hostname: 'web-host' }))
  routes['GET /api/servers/72/agent/jobs'] = () => response({ jobs: [job({ id: 'web-job', name: '网站任务' })] })
  routes['GET /api/servers/72/agent/jobs/web-job/runs*'] = () => response({ runs: [] })
  routes['POST /api/servers/72/agent/schedule-preview'] = () => response({ tz: 'UTC', runs: [] })
  const w = await mountAgent()
  await w.get('#agent-server').setValue('server:72'); await flushPromises()
  release(); await flushPromises()
  expect(w.text()).toContain('网站任务'); expect(w.text()).not.toContain('old-host'); expect(w.text()).not.toContain('每日备份')
})

it('output follow stops when run finishes and shows truncated notice', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  routes['GET /api/servers/71/agent/jobs/backup/runs*'] = () => response({ runs: [run({ id: 7, status: 'running', finished_at: null, exit_code: null })] })
  const outputs = [
    { data: 'hello ', offset: 6, eof: false, truncated: false },
    { data: '', offset: 6, eof: false, truncated: false },
    { data: 'world', offset: 11, eof: true, truncated: true },
  ]
  routes['GET /api/servers/71/agent/runs/7/output*'] = () => response(outputs.shift() || { data: '', offset: 11, eof: true, truncated: true })
  const w = await mountAgent()
  await w.get('[data-testid="job-row"]').trigger('click'); await flushPromises()
  await w.get('[data-testid="run-row"]').trigger('click'); await flushPromises()
  const outCalls = () => calls().filter(c => c.includes('/runs/7/output'))
  expect(outCalls()[0]).toContain('offset=0')
  expect(w.get('[data-testid="run-output"]').text()).toContain('hello')
  await vi.advanceTimersByTimeAsync(2000); await flushPromises()
  expect(outCalls()[1]).toContain('offset=6')
  await vi.advanceTimersByTimeAsync(2000); await flushPromises()
  expect(w.get('[data-testid="run-output"]').text()).toContain('hello world')
  expect(w.text()).toContain('输出超过 10 MiB，已截断')
  const n = outCalls().length
  await vi.advanceTimersByTimeAsync(10000); await flushPromises()
  expect(outCalls().length).toBe(n)
})

it('refresh after run now keeps filter, selected run and output', async () => {
  routes['GET /api/servers/71/agent/jobs/backup/runs*'] = (u) => response({ runs: u.includes('status=failed') ? [run()] : [run(), run({ id: 6, status: 'succeeded' })] })
  routes['POST /api/servers/71/agent/jobs/backup/run'] = () => response({ run_id: 9 })
  routes['GET /api/servers/71/agent/runs/5/output*'] = () => response({ data: 'boom', offset: 4, eof: true, truncated: false })
  const w = await mountAgent()
  await w.get('[data-testid="job-row"]').trigger('click'); await flushPromises()
  await w.get('[data-testid="run-filter"]').setValue('failed'); await flushPromises()
  await w.get('[data-testid="run-row"]').trigger('click'); await flushPromises()
  await w.get('[data-testid="run-now"]').trigger('click'); await flushPromises()
  expect(w.get('[data-testid="run-filter"]').element.value).toBe('failed')
  expect(w.get('[data-testid="run-output"]').text()).toContain('boom')
})

it('failed schedule preview shows a dash with a hint and retries on refresh', async () => {
  let ok = false
  routes['POST /api/servers/71/agent/schedule-preview'] = () => ok ? response({ tz: 'UTC', runs: [{ utc: 'u', local: '2026-11-12 03:00' }] }) : failure('agent_unreachable', 'x', 503)
  routes['POST /api/servers/71/agent/jobs/backup/pause'] = () => response(job())
  const w = await mountAgent()
  const cell = () => w.findAll('[data-testid="job-row"] td')[2]
  expect(cell().text()).toBe('—'); expect(cell().attributes('title')).toBe('无法计算下次运行时间')
  ok = true
  await w.get('[data-testid="job-row"]').trigger('click'); await flushPromises()
  await w.get('[data-testid="pause-job"]').trigger('click'); await flushPromises()
  expect(cell().text()).toBe('2026-11-12 03:00')
})

it('cancel is offered from status running even when a filter hides the running row', async () => {
  routes['GET /api/servers/71/agent'] = () => response(status({ running: [{ run_id: 12, job_id: 'backup', started_at: 'x' }] }))
  routes['POST /api/servers/71/agent/runs/12/cancel'] = () => response({ cancelled: true })
  const w = await mountAgent()
  await w.get('[data-testid="job-row"]').trigger('click'); await flushPromises()
  await w.get('[data-testid="run-filter"]').setValue('failed'); await flushPromises()
  await w.get('[data-testid="cancel-run"]').trigger('click')
  expect(count('POST /api/servers/71/agent/runs/12/cancel')).toBe(0)
  await w.get('[data-testid="confirm-cancel-run"]').trigger('click'); await flushPromises()
  expect(count('POST /api/servers/71/agent/runs/12/cancel')).toBe(1)
})

it('M8: negative exit codes read as signals in the run history and the job list', async () => {
  routes['GET /api/servers/71/agent/jobs/backup/runs*'] = () => response({ runs: [run({ id: 6, status: 'cancelled', exit_code: -15 }), run({ id: 5, exit_code: 3 })] })
  const w = await mountAgent()
  expect(w.get('[data-testid="job-row"] .ws-badge').attributes('title')).toBe('退出码：信号 15')
  await w.get('[data-testid="job-row"]').trigger('click'); await flushPromises()
  const rows = w.findAll('[data-testid="run-row"]').map(r => r.text())
  expect(rows[0]).toContain('信号 15'); expect(rows[0]).not.toContain('-15')
  expect(rows[1]).toContain('3')
})

it('M6: a failed heartbeat shows in the status bar; ok or unconfigured shows nothing', async () => {
  routes['GET /api/servers/71/agent'] = () => response(status({ heartbeat: { configured: true, at: '2026-11-11T03:00:00Z', ok: false, error: '心跳请求失败' } }))
  let w = await mountAgent()
  expect(w.get('[data-testid="agent-heartbeat-failed"]').text()).toMatch(/^心跳请求失败（.+）$/)
  w.unmount(); wrappers = []
  for (const hb of [{ configured: true, at: 'x', ok: true, error: null }, { configured: false, at: null, ok: false, error: null }]) {
    routes['GET /api/servers/71/agent'] = () => response(status({ heartbeat: hb }))
    w = await mountAgent()
    expect(w.find('[data-testid="agent-heartbeat-failed"]').exists()).toBe(false)
    w.unmount(); wrappers = []
  }
})

import { mount, flushPromises } from '@vue/test-utils'
import { clearAgentCache } from '../src/composables/agentCache'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import JobForm from '../src/components/agent/JobForm.vue'
import AlertSettings from '../src/components/agent/AlertSettings.vue'
import AgentInstall from '../src/components/agent/AgentInstall.vue'
import Agent from '../src/views/dashboard/Agent.vue'

const push = vi.fn(), createSession = vi.fn(async () => ({ id: 's1' }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))
vi.mock('../src/composables/useTerminalStore', () => ({ useTerminalStore: () => ({ createSession, closeSession: vi.fn() }) }))

const RouterLink = { props: ['to'], template: '<a :href="to"><slot /></a>' }
const response = (data, ok = true, status = ok ? 200 : 503) => ({ ok, status, json: async () => data })
const failure = (code, message, status) => response({ detail: { code, message } }, false, status)
const job = (over = {}) => ({ id: 'backup', name: '每日备份', revision: 3, paused: false, command: ['/bin/true'], workdir: '/srv', env: { A: '1' },
  env_file: null, schedule: { cron: ['0 3 * * *'], tz: 'Asia/Shanghai' }, timeout_seconds: 3600, retries: 0, retry_delay_seconds: 60,
  catch_up_window_minutes: 60, alert: { on_failure: true, on_recovery: true, include_output: true }, created_at: 'x', updated_at: 'y',
  pause_changed_at: null, secret_env: [{ name: 'API_TOKEN', status: '已设置' }, { name: 'OLD', status: '已设置' }], ...over })
const alertsView = (over = {}) => ({ smtp: { host: 'smtp.x.com', port: 587, security: 'starttls', username: 'bot', from: 'a@x.com', to: ['b@x.com'] },
  smtp_password: '已设置', thresholds: { disk_percent: 90, ssh_failures: 30 }, heartbeat_url: null, ssh_enabled: true, ...over })
let wrappers = [], routes
const body = (i = 0, method) => JSON.parse(fetch.mock.calls.filter(([, o]) => !method || o?.method === method)[i][1].body)
beforeEach(() => {
  clearAgentCache()
  routes = {}; push.mockClear(); createSession.mockClear()
  vi.stubGlobal('localStorage', { getItem: () => 'fixture-token' })
  vi.stubGlobal('fetch', vi.fn(async (url, o = {}) => {
    const key = `${o.method || 'GET'} ${url}`
    for (const [pattern, handler] of Object.entries(routes)) if (key === pattern) return handler(url, o)
    if (key === 'POST /api/servers/71/agent/schedule-preview') return response({ tz: 'Asia/Shanghai', runs: [{ utc: 'u', local: '2026-11-12T03:00:00+08:00' }, { utc: 'u', local: '2026-11-13T03:00:00+08:00' }] })
    if (key === 'POST /api/servers/71/agent/jobs') return response(job({ id: 'new-job' }))
    if (key === 'PUT /api/servers/71/agent/jobs/backup') return response(job())
    if (key === 'GET /api/servers/71/agent/alerts') return response(alertsView())
    if (key === 'PUT /api/servers/71/agent/alerts') return response(alertsView())
    throw Error('Unexpected ' + key)
  }))
})
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers = []; vi.unstubAllGlobals(); vi.useRealTimers() })
const mnt = (C, props) => { const w = mount(C, { props, global: { stubs: { RouterLink, 'router-link': RouterLink } } }); wrappers.push(w); return w }
const mountForm = async (props = {}) => { const w = mnt(JobForm, { serverId: 71, defaultTz: 'Asia/Shanghai', ...props }); await flushPromises(); return w }
const t = (w, id) => w.get(`[data-testid="${id}"]`)
async function settle(ms = 400) { await vi.advanceTimersByTimeAsync(ms); await flushPromises() }
async function fillNew(w, command = '/bin/true') {
  await t(w, 'jf-id').setValue('new-job'); await t(w, 'jf-name').setValue('新作业'); await t(w, 'jf-command').setValue(command); await t(w, 'jf-workdir').setValue('/srv')
}

it('command lines become argv array', async () => {
  const w = await mountForm()
  await fillNew(w, '/bin/sh\n-c\necho hi\n\nlast\n\n\n')
  await t(w, 'jf-save').trigger('click'); await flushPromises()
  const sent = body(0, 'POST').job
  expect(sent.command).toEqual(['/bin/sh', '-c', 'echo hi', '', 'last'])
  expect(sent).toMatchObject({ id: 'new-job', name: '新作业', workdir: '/srv', schedule: { cron: [], tz: 'Asia/Shanghai' } })
  expect(body(0, 'POST').secret_env).toBeUndefined()
  expect(w.emitted('saved')).toHaveLength(1)
  expect(w.text()).toContain('不经过 shell'); expect(w.text()).toContain('/bin/sh -c')
  expect(w.text()).toContain('作业以服务器上的 panellab 用户运行；能定义作业，就等于能在这台服务器上执行命令')
})

it('requires a first argument and valid id, env and secret names', async () => {
  const w = await mountForm()
  await fillNew(w, '\n-x'); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(t(w, 'jf-error').text()).toContain('第一行'); expect(fetch.mock.calls.filter(([, o]) => o?.method === 'POST')).toHaveLength(0)
  await t(w, 'jf-command').setValue('/bin/true'); await t(w, 'jf-id').setValue('Bad_ID'); await t(w, 'jf-save').trigger('click')
  expect(t(w, 'jf-error').text()).toContain('作业 ID')
  await t(w, 'jf-id').setValue('ok-id'); await t(w, 'jf-add-env').trigger('click'); await t(w, 'env-name-0').setValue('1BAD'); await t(w, 'jf-save').trigger('click')
  expect(t(w, 'jf-error').text()).toContain('环境变量名')
  await t(w, 'env-name-0').setValue('GOOD'); await t(w, 'jf-add-secret').trigger('click'); await t(w, 'new-secret-name-0').setValue('bad name'); await t(w, 'new-secret-value-0').setValue('v')
  await t(w, 'jf-save').trigger('click'); expect(t(w, 'jf-error').text()).toContain('密钥变量名')
  expect(fetch.mock.calls.filter(([, o]) => o?.method === 'POST')).toHaveLength(0)
})

it('invalid cron disables save and shows error from preview', async () => {
  vi.useFakeTimers()
  routes['POST /api/servers/71/agent/schedule-preview'] = (u, o) => JSON.parse(o.body).schedule.cron[0] === 'nope'
    ? failure('invalid', 'schedule: 无效的 cron 表达式 nope', 422)
    : response({ tz: 'Asia/Shanghai', runs: [{ utc: 'u', local: '2026-11-12T03:00:00+08:00' }] })
  const w = await mountForm(); await fillNew(w)
  await t(w, 'jf-add-cron').trigger('click'); await t(w, 'jf-cron-0').setValue('nope')
  expect(t(w, 'jf-save').attributes('disabled')).toBeDefined()        // pending
  expect(fetch.mock.calls.filter(([u]) => u.includes('schedule-preview'))).toHaveLength(0)   // debounced
  await settle()
  expect(t(w, 'jf-preview').text()).toContain('无效的 cron 表达式 nope'); expect(t(w, 'jf-save').attributes('disabled')).toBeDefined()
  await t(w, 'jf-cron-0').setValue('0 3 * * *'); await settle()
  expect(t(w, 'jf-preview').text()).toContain('2026-11-12'); expect(t(w, 'jf-save').attributes('disabled')).toBeUndefined()
  await t(w, 'jf-cron-0').setValue('x'); await t(w, 'jf-cron-0').setValue('0 4 * * *'); await settle()
  expect(fetch.mock.calls.filter(([u]) => u.includes('schedule-preview')).length).toBe(3)    // typing bursts collapse to one request
})

it('shows next three times and manual-only text', async () => {
  vi.useFakeTimers()
  const w = await mountForm()
  expect(t(w, 'jf-preview').text()).toContain('仅手动运行')
  await t(w, 'jf-add-cron').trigger('click'); await t(w, 'jf-cron-0').setValue('0 3 * * *'); await settle()
  expect(t(w, 'jf-preview').text()).toContain('2026-11-12 03:00'); expect(t(w, 'jf-preview').text()).toContain('2026-11-13 03:00')
})

it('stale revision shows conflict and keeps input', async () => {
  routes['PUT /api/servers/71/agent/jobs/backup'] = () => failure('conflict', 'job revision is 4, not 3', 409)
  const w = await mountForm({ job: job() })
  await t(w, 'jf-name').setValue('改过的名字'); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(t(w, 'jf-error').text()).toContain('该作业已在别处修改，请刷新后再改')
  expect(t(w, 'jf-name').element.value).toBe('改过的名字'); expect(w.emitted('saved')).toBeUndefined()
})

it('update sends revision and only changed fields; id is read-only', async () => {
  const w = await mountForm({ job: job() })
  expect(w.find('[data-testid="jf-id"]').attributes('readonly')).toBeDefined()
  await t(w, 'jf-name').setValue('新名字'); await t(w, 'jf-timeout').setValue('120'); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(body(0, 'PUT')).toEqual({ revision: 3, changes: { name: '新名字', timeout_seconds: 120 } })
  expect(w.emitted('saved')).toHaveLength(1)
})

it('secret env unchanged sends no value; delete sends null', async () => {
  const w = await mountForm({ job: job() })
  expect(t(w, 'secret-mode-API_TOKEN').element.value).toBe('keep')
  expect(w.find('[data-testid="secret-value-API_TOKEN"]').exists()).toBe(false)
  await t(w, 'jf-name').setValue('x'); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(body(0, 'PUT').secret_env).toBeUndefined()
  await t(w, 'secret-mode-OLD').setValue('delete')
  await t(w, 'secret-mode-API_TOKEN').setValue('set')
  const input = t(w, 'secret-value-API_TOKEN'); expect(input.attributes('type')).toBe('password'); expect(input.attributes('autocomplete')).toBe('new-password')
  await input.setValue('s3cret')
  await t(w, 'jf-add-secret').trigger('click'); await t(w, 'new-secret-name-0').setValue('NEW_ONE'); await t(w, 'new-secret-value-0').setValue('v2')
  await t(w, 'jf-save').trigger('click'); await flushPromises()
  const sent = body(1, 'PUT'); expect(sent.secret_env).toEqual({ OLD: null, API_TOKEN: 's3cret', NEW_ONE: 'v2' }); expect(sent.changes).toEqual({ name: 'x' })
  expect(JSON.stringify(sent.changes)).not.toContain('secret_env')
})

it('secret only change with no other edits still saves', async () => {
  const w = await mountForm({ job: job() })
  await t(w, 'secret-mode-OLD').setValue('delete'); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(body(0, 'PUT')).toEqual({ revision: 3, changes: {}, secret_env: { OLD: null } })
})

it('env_file: create sends it, blank omitted; edit clears with null; unchanged sends nothing; relative rejected', async () => {
  let w = await mountForm(); await fillNew(w)
  await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect('env_file' in body(0, 'POST').job).toBe(false)
  await t(w, 'jf-env-file').setValue('rel/.env'); await t(w, 'jf-save').trigger('click'); expect(t(w, 'jf-error').text()).toContain('环境变量文件')
  await t(w, 'jf-env-file').setValue('  /srv/.env  '); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(body(1, 'POST').job.env_file).toBe('/srv/.env')
  expect(w.text()).toContain('环境变量文件（可选）'); expect(w.text()).toContain('文件需 panellab 用户可读'); expect(w.text()).toContain('秘密环境变量')
  w = await mountForm({ job: job({ env_file: '/srv/.env' }) })
  await t(w, 'jf-name').setValue('n'); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(JSON.parse(fetch.mock.calls.filter(([, o]) => o?.method === 'PUT')[0][1].body).changes).toEqual({ name: 'n' })
  await t(w, 'jf-env-file').setValue('   '); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(JSON.parse(fetch.mock.calls.filter(([, o]) => o?.method === 'PUT')[1][1].body).changes).toEqual({ name: 'n', env_file: null })
})

it('smtp password never prefilled', async () => {
  const w = mnt(AlertSettings, { serverId: 71 }); await flushPromises()
  expect(t(w, 'smtp-host').element.value).toBe('smtp.x.com')
  expect(w.text()).toContain('已设置')
  expect(w.find('[data-testid="smtp-password"]').exists()).toBe(false)
  await t(w, 'smtp-password-mode').setValue('set')
  const pw = t(w, 'smtp-password'); expect(pw.element.value).toBe(''); expect(pw.attributes('type')).toBe('password'); expect(pw.attributes('autocomplete')).toBe('new-password')
  await t(w, 'alerts-save').trigger('click'); expect(t(w, 'alerts-error').text()).toContain('密码')
  await pw.setValue('hunter2'); await t(w, 'alerts-save').trigger('click'); await flushPromises()
  expect(body(0, 'PUT').smtp.password).toBe('hunter2'); expect(w.emitted('saved')).toHaveLength(1)
  expect(w.html()).not.toContain('hunter2')
})

it('smtp password absent when kept, null when deleted', async () => {
  const w = mnt(AlertSettings, { serverId: 71 }); await flushPromises()
  await t(w, 'alerts-save').trigger('click'); await flushPromises()
  expect('password' in body(0, 'PUT').smtp).toBe(false)
  expect(body(0, 'PUT')).toMatchObject({ smtp: { host: 'smtp.x.com', port: 587, security: 'starttls', username: 'bot', from: 'a@x.com', to: ['b@x.com'] }, ssh_enabled: true, heartbeat_url: null })
  expect(body(0, 'PUT').thresholds).toEqual({ disk_percent: 90, ssh_failures: 30 })
  await t(w, 'smtp-password-mode').setValue('delete'); await t(w, 'alerts-save').trigger('click'); await flushPromises()
  expect(body(1, 'PUT').smtp.password).toBeNull()
})

it('R21: thresholds explain the recovery level', async () => {
  const w = mnt(AlertSettings, { serverId: 71 }); await flushPromises()
  expect(t(w, 'recover-hint').text()).toBe('恢复线为触发线减 5 个百分点（或默认值，取较低者）')
})

it('heartbeat rejects http url', async () => {
  const w = mnt(AlertSettings, { serverId: 71 }); await flushPromises()
  await t(w, 'heartbeat-url').setValue('http://hc.example.com/ping'); await t(w, 'alerts-save').trigger('click'); await flushPromises()
  expect(t(w, 'alerts-error').text()).toContain('https'); expect(fetch.mock.calls.filter(([, o]) => o?.method === 'PUT')).toHaveLength(0)
  await t(w, 'heartbeat-url').setValue('https://hc.example.com/ping'); await t(w, 'alerts-save').trigger('click'); await flushPromises()
  expect(body(0, 'PUT').heartbeat_url).toBe('https://hc.example.com/ping')
})

it('alert settings validates smtp and thresholds before sending', async () => {
  const w = mnt(AlertSettings, { serverId: 71 }); await flushPromises()
  const attempt = async (id, value, text) => {
    const old = t(w, id).element.value; await t(w, id).setValue(value); await t(w, 'alerts-save').trigger('click')
    expect(t(w, 'alerts-error').text()).toContain(text); await t(w, id).setValue(old)
  }
  await attempt('smtp-port', '70000', '端口'); await attempt('smtp-to', 'nobody', '收件人'); await attempt('smtp-to', Array(11).fill('a@x.com').join('\n'), '收件人')
  await attempt('th-disk_percent', '0', '磁盘'); await attempt('th-sustain_minutes', '1441', '分钟'); await attempt('th-ssh_failures', '100001', 'SSH')
  await attempt('smtp-host', '', '主机')
  expect(fetch.mock.calls.filter(([, o]) => o?.method === 'PUT')).toHaveLength(0)
})

it('alert settings show the agent rejection message and test email result', async () => {
  routes['PUT /api/servers/71/agent/alerts'] = () => failure('invalid', 'smtp.host 必须是非空字符串', 422)
  routes['POST /api/servers/71/agent/alerts/test'] = () => response({ ok: false, code: 'auth', message: '登录失败' })
  const w = mnt(AlertSettings, { serverId: 71 }); await flushPromises()
  await t(w, 'alerts-save').trigger('click'); await flushPromises()
  expect(t(w, 'alerts-error').text()).toContain('smtp.host 必须是非空字符串')
  await t(w, 'test-email').trigger('click'); await flushPromises()
  expect(t(w, 'test-email-result').text()).toContain('登录失败')
  routes['POST /api/servers/71/agent/alerts/test'] = () => response({ ok: true })
  await t(w, 'test-email').trigger('click'); await flushPromises()
  expect(t(w, 'test-email-result').text()).toContain('已发送')
})

it('install shows sudo command after upload', async () => {
  const command = "sudo bash '/home/u/panellab-agent-1-abc/install.sh'"
  routes['POST /api/servers/71/agent/install'] = () => response({ path: '/home/u/panellab-agent-1-abc', command, version: '1' })
  const writeText = vi.fn(async () => {}); vi.stubGlobal('navigator', { clipboard: { writeText } })
  const w = mnt(AgentInstall, { serverId: 71, host: 'trainer' })
  expect(w.text()).toContain('panellab'); expect(w.text()).toContain('/opt/panellab-agent'); expect(w.text()).toContain('/var/lib/panellab-agent'); expect(w.text()).toContain('/etc/panellab-agent')
  expect(w.find('[data-testid="install-command"]').exists()).toBe(false)
  await t(w, 'install-upload').trigger('click'); await flushPromises()
  expect(t(w, 'install-command').text()).toBe(command)
  const call = fetch.mock.calls.find(([u]) => u.endsWith('/install')); expect(call[1].body).toBeUndefined()   // never sends a password
  expect(w.find('input[type="password"]').exists()).toBe(false)
  await t(w, 'install-copy').trigger('click'); await flushPromises(); expect(writeText).toHaveBeenCalledWith(command)
  await t(w, 'install-terminal').trigger('click'); await flushPromises()
  expect(createSession).toHaveBeenCalledWith(expect.objectContaining({ id: 71 }), expect.objectContaining({ cwd: '/home/u/panellab-agent-1-abc' })); expect(push).toHaveBeenCalledWith('/terminal')
  await t(w, 'install-recheck').trigger('click'); expect(w.emitted('recheck')).toHaveLength(1)
})

it('install shows the error when upload fails', async () => {
  routes['POST /api/servers/71/agent/install'] = () => failure('agent_install_failed', '上传安装包失败', 502)
  const w = mnt(AgentInstall, { serverId: 71 }); await t(w, 'install-upload').trigger('click'); await flushPromises()
  expect(t(w, 'install-error').text()).toContain('上传安装包失败'); expect(w.find('[data-testid="install-command"]').exists()).toBe(false)
})

const status = () => ({ version: '1', protocol_version: 1, hostname: 'trainer', job_count: 1, running: [], firing: [], timezone: 'Asia/Shanghai' })
function agentRoutes() {
  routes['GET /api/monitor/hosts'] = () => response({ hosts: [{ id: 'server:71', label: 'trainer · 10.0.0.2', pinned: true }] })
  routes['GET /api/servers/71/agent'] = () => response(status())
  routes['GET /api/servers/71/agent/jobs'] = () => response({ jobs: [job()] })
  routes['GET /api/servers/71/agent/jobs/backup/runs?limit=1'] = () => response({ runs: [] })
  routes['GET /api/servers/71/agent/jobs/backup/runs?limit=20'] = () => response({ runs: [] })
}
it('page opens the form from edit, refreshes and closes after save; settings and new job open forms', async () => {
  agentRoutes()
  const w = mnt(Agent); await flushPromises()
  await w.get('[data-testid="job-row"]').trigger('click'); await flushPromises()
  await t(w, 'edit-job').trigger('click'); await flushPromises()
  expect(t(w, 'agent-form-slot').exists()).toBe(true); expect(t(w, 'jf-id').element.value).toBe('backup')
  const before = fetch.mock.calls.filter(([u, o]) => u === '/api/servers/71/agent/jobs' && o?.method === 'GET').length
  await t(w, 'jf-name').setValue('改名'); await t(w, 'jf-save').trigger('click'); await flushPromises()
  expect(w.find('[data-testid="agent-form-slot"]').exists()).toBe(false)
  expect(fetch.mock.calls.filter(([u, o]) => u === '/api/servers/71/agent/jobs' && o?.method === 'GET').length).toBe(before + 1)
  await w.findAll('.ws-actions button').find(b => b.text() === '告警设置').trigger('click'); await flushPromises()
  expect(w.find('[data-testid="smtp-host"]').exists()).toBe(true)
  await w.findAll('.ws-actions button').find(b => b.text() === '新建作业').trigger('click'); await flushPromises()
  expect(w.find('[data-testid="jf-id"]').exists()).toBe(true); expect(w.find('[data-testid="smtp-host"]').exists()).toBe(false)
})

it('page shows install panel when the agent is not installed', async () => {
  agentRoutes(); routes['GET /api/servers/71/agent'] = () => failure('agent_not_installed', 'x', 404)
  const w = mnt(Agent); await flushPromises()
  expect(t(w, 'install-upload').exists()).toBe(true)
  routes['GET /api/servers/71/agent'] = () => response(status())
  await t(w, 'install-recheck').trigger('click'); await flushPromises()
  expect(t(w, 'agent-status').exists()).toBe(true)
})

it('R20: failed mail shows a warning with an entry to alert settings; unconfigured shows a hint; ok shows no warning', async () => {
  agentRoutes()
  routes['GET /api/servers/71/agent'] = () => response({ ...status(), alerts_configured: true, mail: { at: '2026-11-11T03:00:00Z', ok: false, error: '登录失败' } })
  let w = mnt(Agent); await flushPromises()
  expect(t(w, 'agent-mail-failed').text()).toContain('最近一次告警邮件发送失败：登录失败（')
  await t(w, 'agent-mail-failed').get('button').trigger('click'); await flushPromises()
  expect(w.find('[data-testid="smtp-host"]').exists()).toBe(true)
  w.unmount()
  routes['GET /api/servers/71/agent'] = () => response({ ...status(), alerts_configured: false, mail: { at: null, ok: false, error: null } })
  w = mnt(Agent); await flushPromises()
  expect(t(w, 'agent-mail-unconfigured').text()).toContain('尚未配置告警邮件'); expect(w.find('[data-testid="agent-mail-failed"]').exists()).toBe(false)
  w.unmount()
  routes['GET /api/servers/71/agent'] = () => response({ ...status(), alerts_configured: true, mail: { at: '2026-11-11T03:00:00Z', ok: true, error: null } })
  w = mnt(Agent); await flushPromises()
  expect(w.find('[data-testid="agent-mail-failed"]').exists()).toBe(false); expect(w.find('[data-testid="agent-mail-unconfigured"]').exists()).toBe(false)
  expect(t(w, 'agent-mail-ok').text()).toContain('最近发信成功')
})

it('R20: upgrade button reveals the install panel with the upgrade heading and both versions; not installed says 安装 Agent', async () => {
  agentRoutes()
  routes['POST /api/servers/71/agent/install'] = () => response({ path: '/home/u/d', command: 'sudo bash x', version: '2' })
  let w = mnt(Agent); await flushPromises()
  expect(w.find('[data-testid="agent-install"]').exists()).toBe(false)
  await t(w, 'agent-upgrade').trigger('click'); await flushPromises()
  expect(t(w, 'install-heading').text()).toBe('升级 Agent'); expect(t(w, 'install-upgrade-note').text()).toContain('等正在运行的作业结束')
  expect(t(w, 'install-versions').text()).toContain('当前已安装版本 1')
  await t(w, 'install-upload').trigger('click'); await flushPromises()
  expect(t(w, 'install-versions').text()).toContain('安装包版本 2')
  w.unmount()
  routes['GET /api/servers/71/agent'] = () => failure('agent_not_installed', 'x', 404)
  w = mnt(Agent); await flushPromises()
  expect(t(w, 'install-heading').text()).toBe('安装 Agent'); expect(w.find('[data-testid="agent-upgrade"]').exists()).toBe(false)
  w.unmount()
  routes['GET /api/servers/71/agent'] = () => failure('protocol_mismatch', 'x', 409)
  w = mnt(Agent); await flushPromises()
  expect(t(w, 'install-heading').text()).toBe('升级 Agent')
})

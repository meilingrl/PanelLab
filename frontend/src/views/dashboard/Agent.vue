<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { agentApi } from '../../composables/useAgentApi'
import { request } from '../../composables/useWorkspaceApi'
import '../../components/workspaces/workspaces.css'
import JobList from '../../components/agent/JobList.vue'
import JobDetail from '../../components/agent/JobDetail.vue'

const hosts = ref([]), serverKey = ref(''), loading = ref(false), hostsError = ref('')
const agentStatus = ref(null), jobs = ref([]), error = ref(null), notice = ref('')
const lastRuns = ref({}), previews = ref({}), selectedId = ref(null)
// Hooks for Task 11: `editing` is null, 'new' or a job object; `settings` toggles alert settings.
// Task 11 renders JobForm / AlertSettings in the form slot below and replaces the not-installed notice with AgentInstall.
const editing = ref(null), settings = ref(false)
const serverId = computed(() => Number(serverKey.value.split(':')[1]))
const selectedJob = computed(() => jobs.value.find(j => j.id === selectedId.value))
const previewKey = j => `${j.id}@${j.revision}`
const nextRuns = computed(() => Object.fromEntries(jobs.value.filter(j => previewKey(j) in previews.value).map(j => [j.id, previews.value[previewKey(j)]])))

const ERROR_TEXT = {
  agent_permission: '请重新登录 SSH，或确认当前 SSH 用户已加入 panellab 组',
  agent_unreachable: '服务器上的 Agent 未在运行：可在终端运行 sudo systemctl status panellab-agent 查看',
  agent_protocol: 'Agent 返回了无法识别的响应，请确认 Agent 版本后重试',
  protocol_mismatch: 'Agent 与面板的协议版本不一致，请在本页重新安装或升级 Agent',
  remote_timeout: '连接服务器超时，请稍后重试',
  trust_required: '尚未确认这台服务器的 SSH 指纹，请先在任务工作区确认',
}
const errorText = e => (e.code === 'agent_permission' ? (e.message && !/^请求失败 \(/.test(e.message) ? e.message : ERROR_TEXT.agent_permission) : ERROR_TEXT[e.code]) || e.message || '请求失败'

let generation = 0, controller = null
async function pool(tasks, size = 4) { const queue = [...tasks]; await Promise.all(Array.from({ length: Math.min(size, queue.length) }, async () => { while (queue.length) await queue.shift()() })) }
// Job views carry neither a next run time nor the latest result, so both are fetched per job (4 at a time):
// next run via schedule-preview, cached per job revision; last result via runs?limit=1.
function loadExtras(seq, signal, id) {
  const tasks = []
  for (const job of jobs.value) {
    tasks.push(async () => {
      try { const r = await agentApi.runs(id, job.id, { limit: 1 }, signal); if (seq === generation) lastRuns.value = { ...lastRuns.value, [job.id]: r.runs?.[0] || null } } catch (_) { /* keep placeholder */ }
    })
    if (job.paused || !(job.schedule?.cron || []).length || previews.value[previewKey(job)]) continue
    tasks.push(async () => {
      try { const r = await agentApi.preview(id, job.schedule, signal); if (seq === generation) previews.value = { ...previews.value, [previewKey(job)]: r.runs?.[0]?.local || '无' } } catch (_) { if (seq === generation) previews.value = { ...previews.value, [previewKey(job)]: false } } // false = failed; retried on next refresh
    })
  }
  return pool(tasks)
}
async function fetchAll(seq, signal, id) {
  const [st, list] = await Promise.all([agentApi.status(id, signal), agentApi.jobs(id, signal)])
  if (seq !== generation) return
  agentStatus.value = st; jobs.value = list.jobs || []
  if (selectedId.value && !selectedJob.value) selectedId.value = null
  await loadExtras(seq, signal, id)
}
async function load() {
  const seq = ++generation; controller?.abort(); controller = new AbortController()
  agentStatus.value = null; jobs.value = []; error.value = null; notice.value = ''; lastRuns.value = {}; previews.value = {}
  selectedId.value = null; editing.value = null; settings.value = false
  if (!serverKey.value) { loading.value = false; return }
  loading.value = true
  try {
    // Status first: a missing or unreachable Agent must not also surface a jobs error.
    const st = await agentApi.status(serverId.value, controller.signal); if (seq !== generation) return
    agentStatus.value = st
    const list = await agentApi.jobs(serverId.value, controller.signal); if (seq !== generation) return
    jobs.value = list.jobs || []
    await loadExtras(seq, controller.signal, serverId.value)
  } catch (e) { if (seq === generation && e.name !== 'AbortError') error.value = e }
  finally { if (seq === generation) loading.value = false }
}
async function refresh() {
  const seq = generation, signal = controller.signal
  try { await fetchAll(seq, signal, serverId.value); if (seq === generation) notice.value = '' }
  catch (e) { if (seq === generation && e.name !== 'AbortError') notice.value = errorText(e) }
}
function deleted() { selectedId.value = null; refresh() }
onMounted(async () => {
  try {
    const data = await request('/monitor/hosts')
    hosts.value = (data.hosts || []).filter(h => h.id !== 'local')
    const first = hosts.value.find(h => h.pinned !== false)
    if (first) { serverKey.value = first.id; load() }
  } catch (e) { hostsError.value = e.message }
})
onBeforeUnmount(() => { generation++; controller?.abort() })
</script>
<template>
  <div class="workspaces agent-page">
    <header class="ws-page-head"><div><span class="ws-eyebrow">SERVER AGENT</span><h1>服务器 Agent</h1><p>在服务器上按计划运行作业，查看运行结果与输出。</p></div>
      <div v-if="agentStatus" class="ws-actions"><button @click="settings = true; editing = null">告警设置</button><button class="ws-primary" @click="editing = 'new'; settings = false">新建作业</button></div></header>
    <div class="ws-filter-bar"><label>服务器<select id="agent-server" v-model="serverKey" @change="load">
      <option v-for="h in hosts" :key="h.id" :value="h.id" :disabled="h.pinned === false">{{ h.label }}{{ h.pinned === false ? '（需先在任务工作区确认指纹）' : '' }}</option></select></label>
      <button :disabled="!serverKey || loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button></div>
    <p v-if="hostsError" class="ws-error" role="alert">{{ hostsError }}</p>
    <p v-else-if="!hosts.some(h => h.pinned !== false)" class="ws-notice">还没有已确认指纹的服务器。请先在 <router-link to="/workspaces">任务工作区</router-link> 添加服务器并确认指纹。</p>

    <section v-if="error?.code === 'agent_not_installed'" class="ws-panel ws-empty" data-testid="agent-not-installed">
      <h2>这台服务器的 Agent 未安装</h2><p>Agent 是运行在服务器上的小程序，负责按计划执行作业。安装入口将在本页提供。</p></section>
    <section v-else-if="error" class="ws-error" role="alert" data-testid="agent-error">{{ errorText(error) }}
      <template v-if="error.code === 'trust_required'"> <router-link to="/workspaces">前往任务工作区</router-link></template>
      <p v-if="error.message && error.code !== 'agent_permission' && ERROR_TEXT[error.code]" class="ws-muted">详情：{{ error.message }}</p></section>
    <template v-else-if="agentStatus">
      <p v-if="notice" class="ws-error" role="alert">{{ notice }}</p>
      <div class="ws-row ws-wrap agent-statusbar" data-testid="agent-status"><span class="ws-badge good">Agent 运行中</span>
        <span>{{ agentStatus.hostname }}</span><span class="ws-muted">版本 {{ agentStatus.version }}</span><span class="ws-muted">{{ agentStatus.job_count ?? jobs.length }} 个作业</span>
        <span class="ws-muted">正在运行 {{ (agentStatus.running || []).length }}</span><span v-if="agentStatus.draining" class="ws-badge warn">正在排空</span></div>
      <section v-if="(agentStatus.firing || []).length" class="ws-error" data-testid="firing-alerts" role="alert"><strong>触发中的告警</strong>
        <ul><li v-for="a in agentStatus.firing" :key="a.key">{{ a.message }}</li></ul></section>
      <div v-if="editing || settings" data-testid="agent-form-slot" class="ws-panel"><p class="ws-muted">表单将在后续版本提供。</p><button @click="editing = null; settings = false">关闭</button></div>
      <JobList :jobs="jobs" :next-runs="nextRuns" :last-runs="lastRuns" :selected-id="selectedId" @select="selectedId = $event" />
      <JobDetail v-if="selectedJob" :key="serverId + ':' + selectedJob.id" :server-id="serverId" :job="selectedJob" :running-runs="(agentStatus.running || []).filter(r => r.job_id === selectedJob.id)" @changed="refresh" @deleted="deleted" @edit="editing = $event; settings = false" />
    </template>
    <p v-else-if="loading" class="ws-muted">加载中…</p>
  </div>
</template>
<style>
.agent-page .agent-table-wrap{overflow-x:auto}.agent-page .agent-table{width:100%;border-collapse:collapse;font-size:13px}
.agent-page .agent-table th,.agent-page .agent-table td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}
.agent-page .agent-table th{font-size:11px;color:var(--text-secondary);font-weight:600}.agent-page .agent-table tbody tr{cursor:pointer}
.agent-page .agent-table tbody tr:hover,.agent-page .agent-table tr.selected{background:var(--ws-tint)}
.agent-page .agent-jobs,.agent-page .agent-detail{margin-top:16px;padding:18px 20px}.agent-page .agent-statusbar{margin:12px 0;justify-content:flex-start}
.agent-page .agent-secret{margin-right:12px}
</style>

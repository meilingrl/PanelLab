<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { agentApi, runStatusLabels, triggerLabel, durationText, exitCodeText } from '../../composables/useAgentApi'
import { formatTime } from '../../composables/useWorkspaceApi'
import RunOutput from './RunOutput.vue'
// `changed` asks the page to refetch status and jobs; `edit` carries the job for the form added by Task 11.
// runningRuns: this job's entries from the status op's `running` list ({run_id, job_id, started_at}); the cancel target comes from it, not from the loaded history page.
const props = defineProps({ serverId: { type: Number, required: true }, job: { type: Object, required: true }, runningRuns: { type: Array, default: () => [] } })
const emit = defineEmits(['changed', 'edit', 'deleted'])
const PAGE = 20
const statusFilter = ref(''), runs = ref([]), hasMore = ref(false), loadingRuns = ref(false), selectedRun = ref(null)
const busy = ref(false), error = ref(''), confirm = ref('')   // confirm: '' | 'delete' | 'cancel'
const running = computed(() => { const r = props.runningRuns[0]; return r ? { id: r.run_id } : null })
// generation guards job/server scope (actions); runsSeq guards the latest runs request only.
let generation = 0, runsSeq = 0, controller = null
function reset() { generation++; runsSeq++; controller?.abort(); runs.value = []; hasMore.value = false; selectedRun.value = null; confirm.value = ''; error.value = ''; busy.value = false }
async function loadRuns({ more = false, select } = {}) {
  const seq = more ? runsSeq : ++runsSeq
  const id = props.job.id, serverId = props.serverId
  if (!more) { controller?.abort(); controller = new AbortController() }
  const signal = controller.signal
  loadingRuns.value = true
  try {
    const r = await agentApi.runs(serverId, id, { status: statusFilter.value, limit: PAGE, before: more ? runs.value.at(-1)?.id : undefined }, signal)
    if (seq !== runsSeq) return
    const rows = r.runs || []
    runs.value = more ? [...runs.value, ...rows] : rows
    hasMore.value = rows.length === PAGE
    if (select) selectedRun.value = rows.find(x => x.id === select) || selectedRun.value
    else if (selectedRun.value) selectedRun.value = rows.find(x => x.id === selectedRun.value.id) || selectedRun.value
  } catch (e) { if (seq === runsSeq && e.name !== 'AbortError') error.value = e.message }
  finally { if (seq === runsSeq) loadingRuns.value = false }
}
// Separate primitive sources: a parent refresh swaps the job object but must not reset filter, selection or confirmations.
watch([() => props.serverId, () => props.job.id], () => { reset(); statusFilter.value = ''; loadRuns() }, { immediate: true })
watch(statusFilter, () => { selectedRun.value = null; loadRuns() })
onBeforeUnmount(() => { generation++; runsSeq++; controller?.abort() })
async function act(name, after) {
  if (busy.value) return
  const seq = generation, id = props.job.id
  busy.value = true; error.value = ''
  try { const result = await agentApi[name](props.serverId, id); if (seq !== generation) return; await after?.(result) }
  catch (e) { if (seq === generation) error.value = e.message }
  finally { if (seq === generation) busy.value = false }
}
const runNow = () => act('run', async r => { await loadRuns({ select: r.run_id }); emit('changed') })
const pauseResume = () => act(props.job.paused ? 'resume' : 'pause', () => emit('changed'))
const remove = () => act('deleteJob', () => emit('deleted', props.job.id))
async function cancelRun() {
  const target = running.value; if (!target || busy.value) return
  const seq = generation; busy.value = true; error.value = ''
  try { await agentApi.cancel(props.serverId, target.id); if (seq !== generation) return; confirm.value = ''; await loadRuns(); emit('changed') }
  catch (e) { if (seq === generation) error.value = e.message }
  finally { if (seq === generation) busy.value = false }
}
// A run the status still lists as running has just ended: ask the page for fresh status once. The next status no longer lists it, so this cannot repeat.
function outputFinished() {
  const id = selectedRun.value?.id
  const live = id != null && (selectedRun.value.status === 'running' || props.runningRuns.some(r => r.run_id === id))
  loadRuns(); if (live) emit('changed')
}
const cron = computed(() => (props.job.schedule?.cron || []).join('；') || '无计划（仅手动）')
</script>
<template>
  <section class="ws-panel agent-detail" data-testid="job-detail" :aria-label="'作业 ' + job.name">
    <div class="ws-row ws-wrap"><div><span class="ws-eyebrow">JOB</span><h3>{{ job.name }}</h3></div><span v-if="job.paused" class="ws-badge warn">已暂停</span></div>
    <dl class="ws-facts">
      <div><dt>命令</dt><dd class="ws-mono">{{ (job.command || []).join(' ') }}</dd></div>
      <div><dt>工作目录</dt><dd class="ws-mono">{{ job.workdir || '默认' }}</dd></div>
      <div><dt>计划</dt><dd>{{ cron }} · {{ job.schedule?.tz }}</dd></div>
      <div><dt>超时 / 重试</dt><dd>{{ job.timeout_seconds }} 秒 / {{ job.retries }} 次</dd></div>
      <div v-if="Object.keys(job.env || {}).length"><dt>环境变量</dt><dd class="ws-mono">{{ Object.keys(job.env).join('、') }}</dd></div>
      <div v-if="(job.secret_env || []).length"><dt>密钥变量</dt><dd><span v-for="s in job.secret_env" :key="s.name" class="agent-secret"><code>{{ s.name }}</code> {{ s.status }}</span></dd></div>
    </dl>
    <div class="ws-actions">
      <button class="ws-primary" data-testid="run-now" :disabled="busy" @click="runNow">立即运行</button>
      <button v-if="!job.paused" data-testid="pause-job" :disabled="busy" @click="pauseResume">暂停</button>
      <button v-else data-testid="resume-job" :disabled="busy" @click="pauseResume">恢复</button>
      <button data-testid="edit-job" :disabled="busy" @click="emit('edit', job)">编辑</button>
      <button v-if="running && confirm !== 'cancel'" data-testid="cancel-run" :disabled="busy" @click="confirm = 'cancel'">取消正在运行</button>
      <button v-if="confirm !== 'delete'" data-testid="delete-job" :disabled="busy" @click="confirm = 'delete'">删除</button>
    </div>
    <div v-if="confirm === 'delete'" role="alertdialog" aria-label="确认删除" class="ws-danger-zone"><strong>删除作业「{{ job.name }}」及其运行历史与密钥变量，无法恢复。</strong>
      <div class="ws-actions"><button data-testid="confirm-delete-job" :disabled="busy" @click="remove">确认删除</button><button :disabled="busy" @click="confirm = ''">保留作业</button></div></div>
    <div v-if="confirm === 'cancel' && running" role="alertdialog" aria-label="确认取消" class="ws-danger-zone"><strong>取消运行 #{{ running.id }}？进程会被终止。</strong>
      <div class="ws-actions"><button data-testid="confirm-cancel-run" :disabled="busy" @click="cancelRun">确认取消</button><button :disabled="busy" @click="confirm = ''">继续运行</button></div></div>
    <p v-if="error" class="ws-error" role="alert">{{ error }}</p>
    <h4>运行历史</h4>
    <div class="ws-filter-bar"><label>状态<select v-model="statusFilter" data-testid="run-filter"><option value="">全部</option><option v-for="(label, value) in runStatusLabels" :key="value" :value="value">{{ label }}</option></select></label>
      <button :disabled="loadingRuns" @click="loadRuns()">{{ loadingRuns ? '加载中…' : '刷新' }}</button></div>
    <p v-if="!runs.length && !loadingRuns" class="ws-muted">没有符合条件的运行记录。</p>
    <div v-else class="agent-table-wrap"><table class="agent-table">
      <thead><tr><th>#</th><th>触发</th><th>状态</th><th>开始</th><th>耗时</th><th>退出码</th></tr></thead>
      <tbody><tr v-for="r in runs" :key="r.id" data-testid="run-row" :class="{selected: selectedRun?.id === r.id}" tabindex="0" @click="selectedRun = r" @keydown.enter="selectedRun = r">
        <td>{{ r.id }}</td><td>{{ triggerLabel(r) }}</td>
        <td><span class="ws-badge" :class="{good: r.status === 'succeeded', warn: r.status !== 'succeeded' && r.status !== 'running'}">{{ runStatusLabels[r.status] || r.status }}</span></td>
        <td>{{ formatTime(r.started_at) }}</td><td>{{ durationText(r) }}</td><td>{{ exitCodeText(r.exit_code) }}</td></tr></tbody>
    </table></div>
    <button v-if="hasMore" data-testid="load-more-runs" :disabled="loadingRuns" @click="loadRuns({ more: true })">加载更早的记录</button>
    <RunOutput v-if="selectedRun && !['missed', 'skipped_overlap'].includes(selectedRun.status)" :key="serverId + ':' + selectedRun.id" :server-id="serverId" :run="selectedRun" @finished="outputFinished" />
  </section>
</template>

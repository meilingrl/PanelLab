<script setup>
import { computed, reactive, ref, watch, onBeforeUnmount } from 'vue'
import { agentApi } from '../../composables/useAgentApi'
// Create or edit one job. `saved` carries the job view returned by the Agent; `cancel` closes the form.
// Update requests carry only the fields that differ from the loaded job, so edits made elsewhere to other fields survive.
const props = defineProps({ serverId: { type: Number, required: true }, job: { type: Object, default: null }, defaultTz: { type: String, default: 'UTC' } })
const emit = defineEmits(['saved', 'cancel'])
const ID_RE = /^[a-z0-9][a-z0-9-]{0,47}$/, NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/
const editing = !!props.job, orig = props.job
const form = reactive({
  id: orig?.id || '', name: orig?.name || '', command: (orig?.command || []).join('\n'), workdir: orig?.workdir || '',
  cron: [...(orig?.schedule?.cron || [])], tz: orig?.schedule?.tz || props.defaultTz || 'UTC',
  timeout_seconds: orig?.timeout_seconds ?? 3600, retries: orig?.retries ?? 0, retry_delay_seconds: orig?.retry_delay_seconds ?? 60,
  catch_up_window_minutes: orig?.catch_up_window_minutes ?? 60, env_file: orig?.env_file || '',
  alert: { on_failure: true, on_recovery: true, include_output: true, ...(orig?.alert || {}) },
  env: Object.entries(orig?.env || {}).map(([name, value]) => ({ name, value })),
})
// Existing secrets: mode keep | set | delete. Values are write-only and exist only while typed.
const secrets = reactive((orig?.secret_env || []).map(s => ({ name: s.name, mode: 'keep', value: '' })))
const newSecrets = reactive([])
const busy = ref(false), error = ref(''), preview = reactive({ state: 'ok', runs: [], message: '' })
let alive = true, seq = 0, timer = null, controller = null

const cronList = () => form.cron.map(c => c.trim()).filter(Boolean)
function parseCommand(text) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  while (lines.length && lines.at(-1) === '') lines.pop()
  return lines
}
function schedulePreview() {
  clearTimeout(timer); controller?.abort(); const mine = ++seq
  const cron = cronList()
  if (!cron.length) { preview.state = 'ok'; preview.runs = []; preview.message = ''; return }
  preview.state = 'pending'; preview.message = ''
  timer = setTimeout(async () => {
    controller = new AbortController()
    try {
      const r = await agentApi.preview(props.serverId, { cron, tz: form.tz.trim() }, controller.signal)
      if (mine === seq && alive) { preview.runs = (r.runs || []).slice(0, 3).map(x => x.local); preview.state = 'ok' }
    } catch (e) { if (mine === seq && alive && e.name !== 'AbortError') { preview.state = 'error'; preview.message = e.message || '无法计算计划' } }
  }, 400)
}
watch(() => [form.cron.map(c => c.trim()).join('\n'), form.tz], schedulePreview, { immediate: true })
onBeforeUnmount(() => { alive = false; seq++; clearTimeout(timer); controller?.abort() })
const formatRun = s => String(s).replace(/^(\d{4}-\d\d-\d\d)T(\d\d:\d\d).*$/, '$1 $2')

const same = (a, b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b))
function sorted(v) { return v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(k => [k, sorted(v[k])])) : v }
const toInt = v => (typeof v === 'number' ? v : /^\s*-?\d+\s*$/.test(String(v)) ? Number(v) : NaN)

function collect() {
  const command = parseCommand(form.command)
  if (!form.name.trim() || form.name.length > 100) return { error: '请填写作业名称（最多 100 个字符）' }
  if (!editing && !ID_RE.test(form.id)) return { error: '作业 ID 只能用小写字母、数字和连字符，以字母或数字开头，最长 48 个字符' }
  if (!command.length || !command[0]) return { error: '命令的第一行（程序路径）不能为空' }
  if (!form.workdir.startsWith('/')) return { error: '工作目录必须是服务器上的绝对路径' }
  if (form.env_file && (!form.env_file.startsWith('/') || /[\x00-\x1f\x7f]/.test(form.env_file))) return { error: '环境变量文件必须是服务器上的绝对路径，且不能含控制字符' }
  const env = {}
  for (const row of form.env) {
    if (!NAME_RE.test(row.name)) return { error: `环境变量名不合法：${row.name || '（空）'}` }
    if (row.name in env) return { error: `环境变量名重复：${row.name}` }
    env[row.name] = row.value
  }
  const secretEnv = {}
  for (const s of secrets) {
    if (s.mode === 'delete') secretEnv[s.name] = null
    else if (s.mode === 'set') { if (!s.value) return { error: `请填写密钥变量 ${s.name} 的新值，或选择保持不变` }; secretEnv[s.name] = s.value }
  }
  for (const s of newSecrets) {
    if (!NAME_RE.test(s.name)) return { error: `密钥变量名不合法：${s.name || '（空）'}` }
    if (s.name in secretEnv || secrets.some(x => x.name === s.name) || s.name in env) return { error: `变量名重复：${s.name}` }
    if (!s.value) return { error: `请填写密钥变量 ${s.name} 的值` }
    secretEnv[s.name] = s.value
  }
  const ints = { timeout_seconds: [1, 86400, '超时'], retries: [0, 10, '重试次数'], retry_delay_seconds: [0, 86400, '重试间隔'], catch_up_window_minutes: [0, 1440, '补跑窗口'] }
  const nums = {}
  for (const [k, [lo, hi, label]] of Object.entries(ints)) {
    const n = toInt(form[k]); if (!Number.isInteger(n) || n < lo || n > hi) return { error: `${label}必须是 ${lo}–${hi} 的整数` }
    nums[k] = n
  }
  if (!form.tz.trim()) return { error: '请填写时区' }
  const fields = { name: form.name.trim(), command, workdir: form.workdir, env, env_file: form.env_file || null, schedule: { cron: cronList(), tz: form.tz.trim() }, ...nums, alert: { ...form.alert } }
  return { fields, secretEnv }
}
// An unchanged schedule is already valid on the Agent, so only an edited schedule has to wait for (and pass) its preview.
const scheduleChanged = computed(() => !editing || !same({ cron: cronList(), tz: form.tz.trim() }, orig.schedule))
const canSave = computed(() => !busy.value && (preview.state === 'ok' || !scheduleChanged.value))

async function save() {
  if (!canSave.value) return
  const c = collect(); if (c.error) { error.value = c.error; return }
  busy.value = true; error.value = ''
  try {
    let result
    if (editing) {
      const changes = {}
      for (const [k, v] of Object.entries(c.fields)) if (!same(v, orig[k])) changes[k] = v
      const payload = { revision: orig.revision, changes }
      if (Object.keys(c.secretEnv).length) payload.secret_env = c.secretEnv
      if (!Object.keys(changes).length && !payload.secret_env) { emit('cancel'); return }
      result = await agentApi.updateJob(props.serverId, orig.id, payload)
    } else {
      const job = { id: form.id, ...c.fields }; if (job.env_file === null) delete job.env_file
      const payload = { job }
      if (Object.keys(c.secretEnv).length) payload.secret_env = c.secretEnv
      result = await agentApi.createJob(props.serverId, payload)
    }
    secrets.forEach(s => { s.value = ''; s.mode = 'keep' }); newSecrets.splice(0)
    if (alive) emit('saved', result)
  } catch (e) {
    if (alive) error.value = editing && e.status === 409 ? '该作业已在别处修改，请刷新后再改' : e.message || '保存失败'
  } finally { if (alive) busy.value = false }
}
</script>
<template>
  <section class="ws-panel ws-form agent-form" aria-label="作业编辑" data-testid="job-form">
    <div class="ws-row"><div><span class="ws-eyebrow">JOB</span><h2>{{ editing ? '编辑作业' : '新建作业' }}</h2></div><button type="button" :disabled="busy" @click="emit('cancel')">关闭</button></div>
    <p class="ws-notice" data-testid="jf-trust">作业以服务器上的 panellab 用户运行；能定义作业，就等于能在这台服务器上执行命令。</p>
    <form @submit.prevent>
      <div class="ws-form-grid">
        <label>作业 ID<input data-testid="jf-id" v-model="form.id" :readonly="editing" maxlength="48" placeholder="例如 nightly-backup" autocomplete="off"><small class="ws-muted">创建后不能修改</small></label>
        <label>名称<input data-testid="jf-name" v-model="form.name" maxlength="100" :disabled="busy"></label>
        <label class="ws-full">命令（每行一个参数）<textarea data-testid="jf-command" v-model="form.command" rows="4" class="ws-mono" spellcheck="false" :disabled="busy" placeholder="/usr/bin/python3&#10;/srv/backup.py&#10;--full" />
          <small class="ws-muted">不经过 shell；需要管道等请写 /bin/sh -c，把整条命令放在下一行。</small></label>
        <label class="ws-full">环境变量文件（可选）<input data-testid="jf-env-file" v-model="form.env_file" class="ws-mono" placeholder="/srv/app/.env" :disabled="busy"><small class="ws-muted">KEY=VALUE 每行一个，支持 export 前缀和引号；文件需 panellab 用户可读</small></label>
        <label class="ws-full">工作目录<input data-testid="jf-workdir" v-model="form.workdir" class="ws-mono" placeholder="/srv/app" :disabled="busy"></label>
      </div>
      <fieldset :disabled="busy"><legend>计划</legend>
        <div v-for="(c, i) in form.cron" :key="i" class="ws-row"><input :data-testid="'jf-cron-' + i" v-model="form.cron[i]" class="ws-mono" placeholder="分 时 日 月 周，例如 0 3 * * *" :aria-label="'计划 ' + (i + 1)">
          <button type="button" @click="form.cron.splice(i, 1)">移除</button></div>
        <div class="ws-actions"><button type="button" data-testid="jf-add-cron" @click="form.cron.push('')">添加计划</button></div>
        <label>时区<input data-testid="jf-tz" v-model="form.tz" placeholder="Asia/Shanghai"></label>
        <div data-testid="jf-preview" role="status" class="agent-preview">
          <span v-if="preview.state === 'pending'" class="ws-muted">计算中…</span>
          <span v-else-if="preview.state === 'error'" class="ws-error">{{ preview.message }}</span>
          <span v-else-if="!preview.runs.length" class="ws-muted">仅手动运行</span>
          <template v-else><span class="ws-muted">接下来：</span><ul><li v-for="r in preview.runs" :key="r">{{ formatRun(r) }}</li></ul></template></div>
      </fieldset>
      <div class="ws-form-grid">
        <label>超时（秒）<input data-testid="jf-timeout" v-model="form.timeout_seconds" inputmode="numeric"></label>
        <label>失败重试次数<input data-testid="jf-retries" v-model="form.retries" inputmode="numeric"></label>
        <label>重试间隔（秒）<input data-testid="jf-retry-delay" v-model="form.retry_delay_seconds" inputmode="numeric"></label>
        <label>补跑窗口（分钟）<input data-testid="jf-catchup" v-model="form.catch_up_window_minutes" inputmode="numeric"><small class="ws-muted">Agent 离线错过的运行，在窗口内补跑；0 为不补跑</small></label>
      </div>
      <fieldset :disabled="busy"><legend>告警</legend>
        <label><input type="checkbox" v-model="form.alert.on_failure"> 失败时通知</label>
        <label><input type="checkbox" v-model="form.alert.on_recovery"> 恢复时通知</label>
        <label><input type="checkbox" v-model="form.alert.include_output"> 通知附带输出</label></fieldset>
      <fieldset :disabled="busy"><legend>环境变量（明文）</legend>
        <p class="ws-muted">API 密钥、密码等请放在下方"秘密环境变量"中</p>
        <div v-for="(row, i) in form.env" :key="i" class="ws-row"><input :data-testid="'env-name-' + i" v-model="row.name" class="ws-mono" placeholder="NAME" :aria-label="'环境变量名 ' + (i + 1)"><input :data-testid="'env-value-' + i" v-model="row.value" placeholder="值" :aria-label="'环境变量值 ' + (i + 1)">
          <button type="button" @click="form.env.splice(i, 1)">移除</button></div>
        <div class="ws-actions"><button type="button" data-testid="jf-add-env" @click="form.env.push({ name: '', value: '' })">添加环境变量</button></div></fieldset>
      <fieldset :disabled="busy"><legend>秘密环境变量（只写，保存后不再显示）</legend>
        <div v-for="s in secrets" :key="s.name" class="ws-row"><code>{{ s.name }}</code>
          <select :data-testid="'secret-mode-' + s.name" v-model="s.mode" :aria-label="s.name + ' 的操作'"><option value="keep">保持不变</option><option value="set">改为新值</option><option value="delete">删除</option></select>
          <input v-if="s.mode === 'set'" :data-testid="'secret-value-' + s.name" v-model="s.value" type="password" autocomplete="new-password" placeholder="新值" :aria-label="s.name + ' 的新值'"></div>
        <div v-for="(s, i) in newSecrets" :key="'n' + i" class="ws-row"><input :data-testid="'new-secret-name-' + i" v-model="s.name" class="ws-mono" placeholder="NAME" :aria-label="'新密钥变量名 ' + (i + 1)">
          <input :data-testid="'new-secret-value-' + i" v-model="s.value" type="password" autocomplete="new-password" placeholder="值" :aria-label="'新密钥变量值 ' + (i + 1)"><button type="button" @click="newSecrets.splice(i, 1)">移除</button></div>
        <div class="ws-actions"><button type="button" data-testid="jf-add-secret" @click="newSecrets.push({ name: '', value: '' })">添加密钥变量</button></div></fieldset>
      <p v-if="error" class="ws-error" role="alert" data-testid="jf-error">{{ error }}</p>
      <div class="ws-actions"><button class="ws-primary" type="button" data-testid="jf-save" :disabled="!canSave" @click="save">{{ busy ? '保存中…' : '保存作业' }}</button><button type="button" :disabled="busy" @click="emit('cancel')">取消</button></div>
    </form>
  </section>
</template>

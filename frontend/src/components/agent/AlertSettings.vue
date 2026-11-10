<script setup>
import { reactive, ref, onMounted, onBeforeUnmount } from 'vue'
import { agentApi } from '../../composables/useAgentApi'
// Alert settings of one server. The SMTP password is write-only: never shown, never prefilled, never kept after save.
const props = defineProps({ serverId: { type: Number, required: true } })
const emit = defineEmits(['saved', 'cancel'])
const THRESHOLDS = [
  ['disk_percent', '磁盘使用率（%）', 1, 100, '磁盘', 90], ['cpu_percent', 'CPU 使用率（%）', 1, 100, 'CPU', 95], ['mem_percent', '内存使用率（%）', 1, 100, '内存', 95],
  ['sustain_minutes', '持续分钟数', 1, 1440, '持续分钟数', 10], ['ssh_failures', 'SSH 登录失败次数', 1, 100000, 'SSH 失败次数', 30],
]
const loading = ref(true), busy = ref(false), error = ref(''), loadError = ref('')
const loaded = ref(null)
const form = reactive({ smtpOn: false, host: '', port: 587, security: 'starttls', username: '', from: '', to: '', pwMode: 'keep', pw: '', th: {}, heartbeat: '', ssh: true })
const test = reactive({ busy: false, result: null })
let alive = true
onBeforeUnmount(() => { alive = false })
function fill(a) {
  loaded.value = a
  const s = a.smtp
  Object.assign(form, { smtpOn: !!s, host: s?.host || '', port: s?.port ?? 587, security: s?.security || 'starttls', username: s?.username || '', from: s?.from || '', to: (s?.to || []).join('\n'),
    pwMode: 'keep', pw: '', th: Object.fromEntries(THRESHOLDS.map(([k]) => [k, a.thresholds?.[k] ?? ''])), heartbeat: a.heartbeat_url || '', ssh: a.ssh_enabled !== false })
}
onMounted(async () => {
  try { const a = await agentApi.alerts(props.serverId); if (alive) fill(a) } catch (e) { if (alive) loadError.value = e.message } finally { if (alive) loading.value = false }
})
const num = v => (typeof v === 'number' ? v : /^\s*-?\d+\s*$/.test(String(v)) ? Number(v) : NaN)

function build() {
  const out = {}
  if (form.smtpOn) {
    const port = num(form.port), to = form.to.split(/[\n,;]/).map(x => x.trim()).filter(Boolean)
    if (!form.host.trim() || /[\r\n]/.test(form.host)) return { error: '请填写 SMTP 主机' }
    if (!Number.isInteger(port) || port < 1 || port > 65535) return { error: 'SMTP 端口必须是 1–65535 的整数' }
    if (!['starttls', 'ssl', 'none'].includes(form.security)) return { error: '请选择安全方式' }
    if (!form.from.trim()) return { error: '请填写发件人' }
    if (!to.length || to.length > 10 || !to.every(x => x.includes('@'))) return { error: '收件人需要 1–10 个邮件地址（每行一个，必须含 @）' }
    const smtp = { host: form.host.trim(), port, security: form.security, username: form.username.trim() || null, from: form.from.trim(), to }
    if (form.pwMode === 'set') { if (!form.pw) return { error: '请填写新的 SMTP 密码，或选择保持不变' }; smtp.password = form.pw }
    else if (form.pwMode === 'delete') smtp.password = null
    out.smtp = smtp
  } else if (loaded.value?.smtp) out.smtp = null
  // Thresholds this form does not manage (recovery levels, windows) pass through unchanged.
  const th = { ...(loaded.value?.thresholds || {}) }
  for (const [key, , lo, hi, label] of THRESHOLDS) {
    const raw = form.th[key]
    if (raw === '' || raw === null || raw === undefined) { delete th[key]; continue }
    const n = num(raw); if (!Number.isInteger(n) || n < lo || n > hi) return { error: `${label}阈值必须是 ${lo}–${hi} 的整数` }
    th[key] = n
  }
  out.thresholds = th
  const url = form.heartbeat.trim()
  if (url && (!url.startsWith('https://') || url.length > 2048 || /\s/.test(url))) return { error: '心跳 URL 只接受 https:// 地址（最长 2048 字符）' }
  out.heartbeat_url = url || null
  out.ssh_enabled = form.ssh
  return { payload: out }
}
async function save() {
  if (busy.value) return
  const r = build(); if (r.error) { error.value = r.error; return }
  busy.value = true; error.value = ''
  try { await agentApi.saveAlerts(props.serverId, r.payload); form.pw = ''; form.pwMode = 'keep'; if (alive) emit('saved') } catch (e) { if (alive) error.value = e.message || '保存失败' } finally { if (alive) busy.value = false }
}
async function sendTest() {
  if (test.busy) return
  test.busy = true; test.result = null
  try { const r = await agentApi.testEmail(props.serverId); if (alive) test.result = r.ok ? { ok: true, text: '测试邮件已发送' } : { ok: false, text: `发送失败：${r.message || r.code || '未知错误'}` } }
  catch (e) { if (alive) test.result = { ok: false, text: `发送失败：${e.message}` } }
  finally { if (alive) test.busy = false }
}
</script>
<template>
  <section class="ws-panel ws-form agent-form" aria-label="告警设置" data-testid="alert-settings">
    <div class="ws-row"><div><span class="ws-eyebrow">ALERTS</span><h2>告警设置</h2></div><button type="button" :disabled="busy" @click="emit('cancel')">关闭</button></div>
    <p v-if="loading" class="ws-muted">加载中…</p>
    <p v-else-if="loadError" class="ws-error" role="alert">{{ loadError }}</p>
    <form v-else @submit.prevent>
      <fieldset :disabled="busy"><legend>邮件（SMTP）</legend>
        <label><input type="checkbox" data-testid="smtp-on" v-model="form.smtpOn"> 启用邮件告警</label>
        <div v-if="form.smtpOn" class="ws-form-grid">
          <label>主机<input data-testid="smtp-host" v-model="form.host" placeholder="smtp.example.com"></label>
          <label>端口<input data-testid="smtp-port" v-model="form.port" inputmode="numeric"></label>
          <label>安全方式<select data-testid="smtp-security" v-model="form.security"><option value="starttls">STARTTLS</option><option value="ssl">SSL</option><option value="none">无</option></select></label>
          <label>用户名（可留空）<input data-testid="smtp-username" v-model="form.username" autocomplete="off"></label>
          <div class="ws-field"><strong>密码（只写）：{{ loaded?.smtp_password || '未设置' }}</strong>
            <select data-testid="smtp-password-mode" v-model="form.pwMode" aria-label="密码操作"><option value="keep">保持不变</option><option value="set">改为新值</option><option value="delete">删除</option></select>
            <input v-if="form.pwMode === 'set'" data-testid="smtp-password" v-model="form.pw" type="password" autocomplete="new-password" placeholder="新密码" aria-label="新密码"></div>
          <label>发件人<input data-testid="smtp-from" v-model="form.from" placeholder="panellab@example.com"></label>
          <label class="ws-full">收件人（每行一个，最多 10 个）<textarea data-testid="smtp-to" v-model="form.to" rows="3" /></label>
        </div>
        <div class="ws-actions"><button type="button" data-testid="test-email" :disabled="test.busy || !loaded?.smtp" @click="sendTest">{{ test.busy ? '发送中…' : '发送测试邮件' }}</button>
          <span v-if="!loaded?.smtp" class="ws-muted">先保存邮件设置，再发送测试邮件（测试使用已保存的设置）</span></div>
        <p v-if="test.result" data-testid="test-email-result" role="status" :class="test.result.ok ? 'ws-muted' : 'ws-error'">{{ test.result.text }}</p>
      </fieldset>
      <fieldset :disabled="busy"><legend>阈值（留空使用默认值）</legend>
        <p class="ws-muted" data-testid="recover-hint">恢复线为触发线减 5 个百分点（或默认值，取较低者）</p><div class="ws-form-grid">
        <label v-for="[key, label, , , , dflt] in THRESHOLDS" :key="key">{{ label }}<input :data-testid="'th-' + key" v-model="form.th[key]" inputmode="numeric" :placeholder="'默认 ' + dflt"></label></div>
        <label><input type="checkbox" data-testid="ssh-enabled" v-model="form.ssh"> 启用 SSH 登录失败告警</label></fieldset>
      <label class="ws-field">心跳 URL（只接受 https，可留空）<input data-testid="heartbeat-url" v-model="form.heartbeat" type="text" placeholder="https://hc-ping.example.com/xxxx" :disabled="busy"></label>
      <p v-if="error" class="ws-error" role="alert" data-testid="alerts-error">{{ error }}</p>
      <div class="ws-actions"><button class="ws-primary" type="button" data-testid="alerts-save" :disabled="busy" @click="save">{{ busy ? '保存中…' : '保存设置' }}</button><button type="button" :disabled="busy" @click="emit('cancel')">取消</button></div>
    </form>
  </section>
</template>

<script setup>
import { reactive, ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { agentApi } from '../../composables/useAgentApi'
// Alert settings of one server. The SMTP password is write-only: never shown, never prefilled, never kept after save.
const props = defineProps({ serverId: { type: Number, required: true } })
const emit = defineEmits(['saved', 'cancel'])
const THRESHOLDS = [
  ['disk_percent', '磁盘使用率（%）', 1, 100, '磁盘', 90], ['cpu_percent', 'CPU 使用率（%）', 1, 100, 'CPU', 95], ['mem_percent', '内存使用率（%）', 1, 100, '内存', 95],
  ['sustain_minutes', '持续分钟数', 1, 1440, '持续分钟数', 10], ['ssh_failures', 'SSH 登录失败次数', 1, 100000, 'SSH 失败次数', 60],
]
// Provider presets, keyed by the (lower-case) domain of the address. All use implicit TLS on 465.
const QQ_HINT = 'QQ 邮箱网页版 → 设置 → 账号与安全 → 开启 SMTP 服务并生成授权码'
const GMAIL_HINT = '需先开启两步验证，再在 Google 账号 → 安全 → 应用专用密码 中生成'
const GENERIC_HINT = '在邮箱网页版的设置中开启 SMTP 服务并获取授权码'
const PRESETS = {}
for (const [domains, host, hint] of [[['qq.com', 'foxmail.com'], 'smtp.qq.com', QQ_HINT], [['163.com'], 'smtp.163.com', GENERIC_HINT], [['126.com'], 'smtp.126.com', GENERIC_HINT],
  [['yeah.net'], 'smtp.yeah.net', GENERIC_HINT], [['gmail.com', 'googlemail.com'], 'smtp.gmail.com', GMAIL_HINT], [['aliyun.com'], 'smtp.aliyun.com', GENERIC_HINT]])
  for (const d of domains) PRESETS[d] = { host, port: 465, security: 'ssl', hint }
const domainOf = address => { const a = String(address || '').trim(), i = a.lastIndexOf('@'); return i > 0 ? a.slice(i + 1).trim().toLowerCase() : '' }
const presetOf = address => { const a = String(address || '').trim(), i = a.lastIndexOf('@'); return i > 0 ? PRESETS[a.slice(i + 1).trim().toLowerCase()] || null : null }
const loading = ref(true), busy = ref(false), error = ref(''), loadError = ref('')
const loaded = ref(null)
const form = reactive({ smtpOn: false, host: '', port: 587, security: 'starttls', username: '', from: '', to: '', advanced: false, pwMode: 'keep', pw: '', th: {}, heartbeat: '', ssh: true })
const test = reactive({ busy: false, result: null })
const autoAdvanced = ref(false)
const preset = computed(() => presetOf(form.from))
let alive = true
onBeforeUnmount(() => { alive = false })
function fill(a) {
  loaded.value = a
  const s = a.smtp
  const p = presetOf(s?.from)
  const simple = !s || (!!p && s.host === p.host && s.port === p.port && s.security === p.security && s.username === s.from)
  autoAdvanced.value = false
  Object.assign(form, { advanced: !simple, smtpOn: !!s, host: s?.host || '', port: s?.port ?? 587, security: s?.security || 'starttls', username: s?.username || '', from: s?.from || '', to: (s?.to || []).join('\n'),
    pwMode: 'keep', pw: '', th: Object.fromEntries(THRESHOLDS.map(([k]) => [k, a.thresholds?.[k] ?? ''])), heartbeat: a.heartbeat_url || '', ssh: a.ssh_enabled !== false })
}
onMounted(async () => {
  try { const a = await agentApi.alerts(props.serverId); if (alive) fill(a) } catch (e) { if (alive) loadError.value = e.message } finally { if (alive) loading.value = false }
})
// Typing the address fills the raw SMTP fields from the provider preset; unknown providers open the advanced settings.
function onEmail(e) {
  const prev = form.from.trim(), prevPreset = presetOf(prev); form.from = e.target.value; const addr = form.from.trim(), p = presetOf(addr)
  if (!form.to.trim() || form.to.trim() === prev) form.to = addr
  // Only touch the raw fields when the provider changes, or they are blank or still the previous preset's own values.
  const blank = !form.host.trim() && !form.username.trim()
  const stillPreset = !!prevPreset && form.host === prevPreset.host && num(form.port) === prevPreset.port && form.security === prevPreset.security && form.username.trim() === prev
  const mayFill = domainOf(addr) !== domainOf(prev) || blank || stillPreset
  const followUser = !form.username.trim() || form.username.trim() === prev
  if (p) {
    if (mayFill) Object.assign(form, { host: p.host, port: p.port, security: p.security, username: addr })
    else if (followUser) form.username = addr
    if (autoAdvanced.value) { form.advanced = false; autoAdvanced.value = false }
  } else if (/@[^@\s]+\.[^@\s]+$/.test(addr)) { if (!form.advanced) autoAdvanced.value = true; form.advanced = true; if (followUser) form.username = addr }
}
// A stored password belongs to the saved provider: changing the host means it must be re-entered.
const providerChanged = computed(() => !!loaded.value?.smtp && !!loaded.value.smtp_password && loaded.value.smtp_password !== '未设置' && form.host.trim() !== loaded.value.smtp.host)
const pwAuto = ref(false)
watch(providerChanged, v => {
  if (v && form.pwMode === 'keep') { form.pwMode = 'set'; pwAuto.value = true }
  else if (!v && pwAuto.value && form.pwMode === 'set') { form.pwMode = 'keep'; form.pw = ''; pwAuto.value = false }
})
const num = v => (typeof v === 'number' ? v : /^\s*-?\d+\s*$/.test(String(v)) ? Number(v) : NaN)

function build() {
  const out = {}
  if (form.smtpOn) {
    const port = num(form.port), to = [...new Set(form.to.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean))]
    if (!form.advanced && !/^[^@\s]+@[^@\s]+$/.test(form.from.trim())) return { error: '请填写有效的邮箱地址' }
    if (!form.host.trim() || /[\r\n]/.test(form.host)) return { error: '请填写 SMTP 主机' }
    if (!Number.isInteger(port) || port < 1 || port > 65535) return { error: 'SMTP 端口必须是 1–65535 的整数' }
    if (!['starttls', 'ssl', 'none'].includes(form.security)) return { error: '请选择安全方式' }
    if (!form.from.trim()) return { error: '请填写发件人' }
    if (!to.length || to.length > 10 || !to.every(x => x.includes('@'))) return { error: '收件人需要 1–10 个邮件地址（每行一个，必须含 @）' }
    const smtp = { host: form.host.trim(), port, security: form.security, username: form.username.trim() || null, from: form.from.trim(), to }
    if (providerChanged.value && form.pwMode !== 'delete' && !(form.pwMode === 'set' && form.pw)) return { error: '更换了邮件服务商，请重新填写授权码' }
    if (form.pwMode === 'set') { if (!form.pw) return { error: '请填写新的授权码 / 密码，或选择保持不变' }; smtp.password = form.pw }
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
        <label class="ws-follow-label"><input type="checkbox" data-testid="smtp-on" v-model="form.smtpOn"> 启用邮件告警</label>
        <div v-if="form.smtpOn" class="ws-form-grid">
          <label>邮箱地址<input data-testid="smtp-email" :value="form.from" type="email" autocomplete="off" placeholder="you@qq.com" @input="onEmail"></label>
          <div class="ws-field"><strong>授权码 / 应用专用密码（只写）：{{ loaded?.smtp_password || '未设置' }}</strong>
            <select data-testid="smtp-password-mode" v-model="form.pwMode" aria-label="密码操作"><option value="keep">保持不变</option><option value="set">改为新值</option><option value="delete">删除</option></select>
            <input v-if="form.pwMode === 'set'" data-testid="smtp-password" v-model="form.pw" type="password" autocomplete="new-password" placeholder="授权码 / 应用专用密码" aria-label="新授权码"></div>
          <label class="ws-full">收件人（默认是邮箱地址；每行一个或用逗号分隔，最多 10 个）<textarea data-testid="smtp-to" v-model="form.to" rows="3" /></label>
          <p v-if="providerChanged" class="ws-error ws-full" data-testid="smtp-provider-changed">更换了邮件服务商，请重新填写授权码</p>
          <p v-if="preset" class="ws-muted ws-full" data-testid="smtp-hint">{{ preset.hint }}</p>
          <p class="ws-muted ws-full" data-testid="smtp-port-hint">阿里云服务器默认封禁 25 端口，请使用 465</p>
          <div class="ws-full"><button type="button" data-testid="smtp-advanced-toggle" :aria-expanded="form.advanced" @click="form.advanced = !form.advanced; autoAdvanced = false">{{ form.advanced ? '收起高级设置' : '高级设置' }}</button></div>
          <template v-if="form.advanced">
            <p v-if="!preset" class="ws-muted ws-full" data-testid="smtp-unknown-hint">未识别的邮箱服务商，请填写服务商提供的 SMTP 设置</p>
            <div class="ws-form-grid ws-full" data-testid="smtp-advanced">
              <label>主机<input data-testid="smtp-host" v-model="form.host" placeholder="smtp.example.com"></label>
              <label>端口<input data-testid="smtp-port" v-model="form.port" inputmode="numeric"></label>
              <label>安全方式<select data-testid="smtp-security" v-model="form.security"><option value="starttls">STARTTLS</option><option value="ssl">SSL</option><option value="none">无</option></select></label>
              <label>用户名（可留空）<input data-testid="smtp-username" v-model="form.username" autocomplete="off"></label>
              <label>发件人<input data-testid="smtp-from" v-model="form.from" placeholder="panellab@example.com"></label>
            </div>
          </template>
        </div>
        <div class="ws-actions"><button type="button" data-testid="test-email" :disabled="test.busy || !loaded?.smtp" @click="sendTest">{{ test.busy ? '发送中…' : '发送测试邮件' }}</button>
          <span v-if="!loaded?.smtp" class="ws-muted">先保存邮件设置，再发送测试邮件（测试使用已保存的设置）</span></div>
        <p v-if="test.result" data-testid="test-email-result" role="status" :class="test.result.ok ? 'ws-muted' : 'ws-error'">{{ test.result.text }}</p>
      </fieldset>
      <fieldset :disabled="busy"><legend>阈值（留空使用默认值）</legend>
        <p class="ws-muted" data-testid="recover-hint">恢复线为触发线减 5 个百分点（或默认值，取较低者）</p><div class="ws-form-grid">
        <label v-for="[key, label, , , , dflt] in THRESHOLDS" :key="key">{{ label }}<input :data-testid="'th-' + key" v-model="form.th[key]" inputmode="numeric" :placeholder="'默认 ' + dflt"></label></div>
        <label class="ws-follow-label"><input type="checkbox" data-testid="ssh-enabled" v-model="form.ssh"> 启用 SSH 登录失败告警</label></fieldset>
      <label class="ws-field">心跳 URL（只接受 https，可留空）<input data-testid="heartbeat-url" v-model="form.heartbeat" type="text" placeholder="https://hc-ping.example.com/xxxx" :disabled="busy"></label>
      <p v-if="error" class="ws-error" role="alert" data-testid="alerts-error">{{ error }}</p>
      <div class="ws-actions"><button class="ws-primary" type="button" data-testid="alerts-save" :disabled="busy" @click="save">{{ busy ? '保存中…' : '保存设置' }}</button><button type="button" :disabled="busy" @click="emit('cancel')">取消</button></div>
    </form>
  </section>
</template>

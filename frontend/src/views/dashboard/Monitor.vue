<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'

const hosts = ref([{ id: 'local', label: '本机' }])
const currentTarget = ref('local')
const stats = ref(null)
const loading = ref(true)
const error = ref('')
let pollTimer = null
const POLL_INTERVAL = 5000

const remoteConfig = ref({ configured: false })
const showRemoteForm = ref(false)
const remoteHost = ref('')
const remotePort = ref(22)
const remoteUser = ref('')
const remotePassword = ref('')
const saveRemoteLoading = ref(false)
const saveRemoteError = ref('')
const saveRemoteSuccess = ref('')

function formatMB(n) {
  if (n == null) return '—'
  return n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${Number(n).toFixed(1)} MB`
}

function formatGB(n) {
  if (n == null) return '—'
  return `${Number(n).toFixed(2)} GB`
}

function formatBytes(b) {
  if (b == null || b === 0) return '0 B'
  const u = 1024
  if (b < u) return `${b} B`
  if (b < u * u) return `${(b / u).toFixed(1)} KB`
  if (b < u * u * u) return `${(b / u / u).toFixed(1)} MB`
  return `${(b / u / u / u).toFixed(2)} GB`
}

async function fetchHosts() {
  try {
    const res = await fetch('/api/monitor/hosts', {
      headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}` },
    })
    if (res.ok) {
      const data = await res.json()
      hosts.value = data.hosts || [{ id: 'local', label: '本机' }]
    }
  } catch (_) {}
}

async function fetchStats() {
  try {
    const url = `/api/monitor/stats?target=${encodeURIComponent(currentTarget.value)}`
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}` },
    })
    if (!res.ok) {
      if (res.status === 401) return
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || '获取失败')
    }
    const data = await res.json()
    stats.value = data
    error.value = ''
  } catch (e) {
    if (e.name !== 'AbortError') {
      error.value = e.message || '系统指标暂不可用'
      stats.value = null
    }
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await fetchRemoteConfig()
  await fetchHosts()
  fetchStats()
  pollTimer = setInterval(fetchStats, POLL_INTERVAL)
})

watch(currentTarget, () => {
  loading.value = true
  error.value = ''
  stats.value = null
  fetchStats()
})

async function fetchRemoteConfig() {
  try {
    const res = await fetch('/api/monitor/remote-config', {
      headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}` },
    })
    if (res.ok) {
      remoteConfig.value = await res.json()
      if (remoteConfig.value.configured) {
        remoteHost.value = remoteConfig.value.host || ''
        remotePort.value = remoteConfig.value.port || 22
        remoteUser.value = remoteConfig.value.username || ''
      }
    }
  } catch (_) {}
}

async function saveRemoteConfig() {
  saveRemoteError.value = ''
  saveRemoteSuccess.value = ''
  if (!remoteHost.value.trim() || !remoteUser.value.trim() || !remotePassword.value) {
    saveRemoteError.value = '请填写主机、用户名和密码'
    return
  }
  saveRemoteLoading.value = true
  try {
    const res = await fetch('/api/monitor/remote-config', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
      },
      body: JSON.stringify({
        host: remoteHost.value.trim(),
        port: Number(remotePort.value) || 22,
        username: remoteUser.value.trim(),
        password: remotePassword.value,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      saveRemoteError.value = data.detail || data.message || '保存失败'
      return
    }
    saveRemoteSuccess.value = data.message || '已保存'
    remotePassword.value = ''
    showRemoteForm.value = false
    await fetchHosts()
    remoteConfig.value = { configured: true, host: remoteHost.value, port: remotePort.value, username: remoteUser.value }
  } catch (e) {
    saveRemoteError.value = e.message || '网络错误'
  } finally {
    saveRemoteLoading.value = false
  }
}

function openRemoteForm() {
  showRemoteForm.value = true
  saveRemoteError.value = ''
  saveRemoteSuccess.value = ''
}

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="page-monitor">
    <h1>系统监控</h1>
    <div class="toolbar">
      <label class="target-label" for="monitor-target">监控目标</label>
      <select id="monitor-target" v-model="currentTarget" class="target-select">
        <option v-for="h in hosts" :key="h.id" :value="h.id">{{ h.label }}</option>
      </select>
      <span class="subtitle">CPU、内存、磁盘与网络，每 5 秒刷新。</span>
    </div>

    <div v-if="loading && !stats" class="state state-loading">加载中…</div>
    <div v-else-if="error && !stats" class="state state-error">{{ error }}</div>
    <template v-else-if="stats">
      <section class="section">
        <h2 class="section-title">CPU</h2>
        <div class="card">
          <div v-if="stats.cpu_percent != null" class="metric-row">
            <span class="label">使用率</span>
            <span class="value">{{ stats.cpu_percent }}%</span>
            <div class="bar-wrap">
              <div class="bar" :style="{ width: Math.min(100, stats.cpu_percent) + '%' }"></div>
            </div>
          </div>
          <p v-else class="muted">暂不可用</p>
        </div>
      </section>

      <section class="section">
        <h2 class="section-title">内存</h2>
        <div class="card">
          <template v-if="stats.memory">
            <div class="metric-row">
              <span class="label">总 / 已用 / 可用</span>
              <span class="value">{{ formatMB(stats.memory.total_mb) }} / {{ formatMB(stats.memory.used_mb) }} / {{ formatMB(stats.memory.available_mb) }}</span>
            </div>
            <div class="metric-row">
              <span class="label">使用率</span>
              <span class="value">{{ stats.memory.percent }}%</span>
              <div class="bar-wrap">
                <div class="bar" :style="{ width: Math.min(100, stats.memory.percent) + '%' }"></div>
              </div>
            </div>
          </template>
          <p v-else class="muted">暂不可用</p>
        </div>
      </section>

      <section class="section">
        <h2 class="section-title">磁盘</h2>
        <div v-if="stats.disk && stats.disk.length" class="card disk-list">
          <div v-for="d in stats.disk" :key="d.mountpoint" class="disk-item">
            <div class="disk-mount">{{ d.mountpoint }}</div>
            <div class="metric-row">
              <span class="label">总 / 已用 / 可用</span>
              <span class="value">{{ formatGB(d.total_gb) }} / {{ formatGB(d.used_gb) }} / {{ formatGB(d.free_gb) }}</span>
            </div>
            <div class="metric-row">
              <span class="label">使用率</span>
              <span class="value">{{ d.percent }}%</span>
              <div class="bar-wrap">
                <div class="bar" :style="{ width: Math.min(100, d.percent) + '%' }"></div>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="card">
          <p class="muted">暂不可用</p>
        </div>
      </section>

      <section class="section">
        <h2 class="section-title">网络</h2>
        <div class="card">
          <template v-if="stats.network">
            <div class="metric-row">
              <span class="label">累计接收</span>
              <span class="value">{{ formatBytes(stats.network.bytes_recv) }}</span>
            </div>
            <div class="metric-row">
              <span class="label">累计发送</span>
              <span class="value">{{ formatBytes(stats.network.bytes_sent) }}</span>
            </div>
          </template>
          <p v-else class="muted">暂不可用</p>
        </div>
      </section>
    </template>

    <section class="section remote-section">
      <h2 class="section-title">远程服务器连接</h2>
      <div class="card">
        <template v-if="remoteConfig.configured && !showRemoteForm">
          <p class="remote-summary">
            已配置：<strong>{{ remoteConfig.host }}:{{ remoteConfig.port }}</strong>（用户 {{ remoteConfig.username }}）
          </p>
          <button type="button" class="btn-secondary" @click="openRemoteForm">修改配置</button>
        </template>
        <template v-else>
          <p class="remote-hint">填写 Linux 服务器的 SSH 信息，保存后可在上方选择「远程服务器」查看其监控。远程需已安装 Python3 与 psutil。</p>
          <form class="remote-form" @submit.prevent="saveRemoteConfig">
            <div class="form-row">
              <label for="remote-host">主机（IP 或域名）</label>
              <input id="remote-host" v-model="remoteHost" type="text" placeholder="例如 192.168.1.100" />
            </div>
            <div class="form-row">
              <label for="remote-port">端口</label>
              <input id="remote-port" v-model.number="remotePort" type="number" min="1" max="65535" placeholder="22" />
            </div>
            <div class="form-row">
              <label for="remote-user">用户名</label>
              <input id="remote-user" v-model="remoteUser" type="text" placeholder="例如 root" />
            </div>
            <div class="form-row">
              <label for="remote-password">密码</label>
              <input id="remote-password" v-model="remotePassword" type="password" placeholder="SSH 登录密码" autocomplete="off" />
            </div>
            <p v-if="saveRemoteError" class="form-error">{{ saveRemoteError }}</p>
            <p v-if="saveRemoteSuccess" class="form-success">{{ saveRemoteSuccess }}</p>
            <button type="submit" class="btn-primary" :disabled="saveRemoteLoading">
              {{ saveRemoteLoading ? '保存中…' : '保存并连接' }}
            </button>
          </form>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page-monitor {
  max-width: 720px;
}

.page-monitor h1 {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.25rem 0;
}

.toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}

.target-label {
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--text-secondary);
}

.target-select {
  padding: 0.4rem 0.75rem;
  font-size: 0.95rem;
  color: var(--text-primary);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}

.target-select:hover,
.target-select:focus {
  border-color: var(--accent);
  outline: none;
}

.subtitle {
  font-size: 0.95rem;
  color: var(--text-muted);
  margin: 0;
}

.state {
  padding: 2rem;
  text-align: center;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--bg-secondary);
}

.state-loading {
  color: var(--text-secondary);
}

.state-error {
  color: var(--error, #c00);
}

.section {
  margin-bottom: 1.5rem;
}

.section-title {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.75rem 0;
}

.card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.25rem;
}

.metric-row {
  margin-bottom: 0.75rem;
}

.metric-row:last-child {
  margin-bottom: 0;
}

.metric-row .label {
  display: block;
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 0.25rem;
}

.metric-row .value {
  font-size: 1rem;
  font-weight: 500;
  color: var(--text-primary);
}

.bar-wrap {
  height: 6px;
  background: var(--bg-tertiary);
  border-radius: 3px;
  margin-top: 0.35rem;
  overflow: hidden;
}

.bar {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.disk-list .disk-item {
  padding-bottom: 1rem;
  border-bottom: 1px solid var(--border);
}

.disk-list .disk-item:last-child {
  padding-bottom: 0;
  border-bottom: none;
}

.disk-mount {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 0.5rem;
}

.muted {
  margin: 0;
  font-size: 0.95rem;
  color: var(--text-muted);
}

.remote-section {
  margin-top: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
}

.remote-summary {
  margin: 0 0 0.75rem 0;
  font-size: 0.95rem;
  color: var(--text-secondary);
}

.remote-summary strong {
  color: var(--text-primary);
}

.remote-hint {
  margin: 0 0 1rem 0;
  font-size: 0.9rem;
  color: var(--text-muted);
}

.remote-form .form-row {
  margin-bottom: 1rem;
}

.remote-form .form-row label {
  display: block;
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin-bottom: 0.35rem;
}

.remote-form .form-row input {
  width: 100%;
  max-width: 280px;
  padding: 0.5rem 0.75rem;
  font-size: 0.95rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  box-sizing: border-box;
}

.remote-form .form-row input:focus {
  outline: none;
  border-color: var(--accent);
}

.form-error {
  margin: 0 0 0.75rem 0;
  font-size: 0.9rem;
  color: var(--error, #c00);
}

.form-success {
  margin: 0 0 0.75rem 0;
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.btn-secondary,
.btn-primary {
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  border-radius: 6px;
  cursor: pointer;
}

.btn-secondary {
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
}

.btn-secondary:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.btn-primary {
  color: #fff;
  background: var(--accent);
  border: none;
}

.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>

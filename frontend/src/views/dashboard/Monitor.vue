<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { Chart, registerables } from 'chart.js'

Chart.register(...registerables)

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

const processes = ref([])
const processLoading = ref(false)
const processError = ref('')
const processSortBy = ref('cpu_percent')
const processLimit = ref(50)
const processNameFilter = ref('')

const trafficChartRef = ref(null)
let trafficChart = null
const installPsutilLoading = ref(false)
const installPsutilMessage = ref('')
const installPsutilError = ref('')

const GAUGE_R = 40
const GAUGE_C = 2 * Math.PI * GAUGE_R
function gaugeOffset(percent) {
  if (percent == null || percent < 0) return GAUGE_C
  return GAUGE_C * (1 - Math.min(100, percent) / 100)
}

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
  fetchProcesses()
  pollTimer = setInterval(fetchStats, POLL_INTERVAL)
})

watch(currentTarget, () => {
  loading.value = true
  error.value = ''
  stats.value = null
  fetchStats()
  if (currentTarget.value === 'local') fetchProcesses()
})

watch(
  () => stats.value?.network?.network_history,
  () => {
    if (currentTarget.value === 'local' && stats.value?.network?.network_history?.length) nextTick(updateTrafficChart)
  },
  { deep: true }
)

async function fetchProcesses() {
  if (currentTarget.value !== 'local') return
  processLoading.value = true
  processError.value = ''
  try {
    const params = new URLSearchParams({
      limit: String(processLimit.value),
      sort_by: processSortBy.value,
    })
    if (processNameFilter.value.trim()) params.set('name_filter', processNameFilter.value.trim())
    const res = await fetch(`/api/monitor/processes?${params}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}` },
    })
    if (!res.ok) {
      if (res.status === 401) return
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || '获取进程列表失败')
    }
    const data = await res.json()
    processes.value = data.processes || []
  } catch (e) {
    if (e.name !== 'AbortError') {
      processError.value = e.message || '进程列表暂不可用'
      processes.value = []
    }
  } finally {
    processLoading.value = false
  }
}

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

async function installPsutilOnRemote() {
  installPsutilError.value = ''
  installPsutilMessage.value = ''
  installPsutilLoading.value = true
  try {
    const res = await fetch('/api/monitor/remote-install-psutil', {
      method: 'POST',
      headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}` },
    })
    const data = await res.json().catch(() => ({}))
    if (res.status === 501) {
      installPsutilMessage.value = data.detail || '功能开发中，请在远程主机手动执行: pip3 install --user psutil'
      return
    }
    if (!res.ok) {
      installPsutilError.value = data.detail || data.message || '安装失败'
      return
    }
    if (data.ok) {
      installPsutilMessage.value = data.message || '已在远程主机安装 psutil'
    } else {
      installPsutilError.value = data.detail || '安装失败'
    }
  } catch (e) {
    installPsutilError.value = e.message || '网络错误'
  } finally {
    installPsutilLoading.value = false
  }
}

function updateTrafficChart() {
  const net = stats.value?.network
  const history = net?.network_history || []
  if (!trafficChartRef.value || !Array.isArray(history) || history.length === 0) return
  const labels = history.map((p) => {
    const d = new Date(p.ts * 1000)
    return d.getMinutes() + ':' + String(d.getSeconds()).padStart(2, '0')
  })
  const sentData = history.map((p) => p.rate_sent_kbps ?? 0)
  const recvData = history.map((p) => p.rate_recv_kbps ?? 0)
  if (!trafficChart) {
    const ctx = trafficChartRef.value.getContext('2d')
    const border = getComputedStyle(document.documentElement).getPropertyValue('--border').trim() || '#ddd'
    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#4a9'
    const text = getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim() || '#666'
    trafficChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: '接收 KB/s', data: recvData, borderColor: accent, backgroundColor: accent + '20', fill: true, tension: 0.4 },
          { label: '发送 KB/s', data: sentData, borderColor: text, backgroundColor: text + '20', fill: true, tension: 0.4 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'top' } },
        scales: {
          x: { ticks: { color: text, maxTicksLimit: 10 } },
          y: { beginAtZero: true, ticks: { color: text } },
        },
      },
    })
  } else {
    trafficChart.data.labels = labels
    trafficChart.data.datasets[0].data = recvData
    trafficChart.data.datasets[1].data = sentData
    trafficChart.update('none')
  }
}

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (trafficChart) {
    trafficChart.destroy()
    trafficChart = null
  }
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
        <div class="card card-gauge">
          <div v-if="stats.cpu_percent != null" class="gauge-wrap">
            <svg class="gauge-svg" viewBox="0 0 100 100">
              <circle class="gauge-bg" cx="50" cy="50" :r="GAUGE_R" fill="none" stroke-width="10" />
              <circle class="gauge-fill" cx="50" cy="50" :r="GAUGE_R" fill="none" stroke-width="10" :stroke-dasharray="GAUGE_C" :stroke-dashoffset="gaugeOffset(stats.cpu_percent)" transform="rotate(-90 50 50)" />
            </svg>
            <span class="gauge-value">{{ stats.cpu_percent }}%</span>
          </div>
          <p v-else class="muted">暂不可用</p>
        </div>
      </section>

      <section class="section">
        <h2 class="section-title">内存</h2>
        <div class="card card-gauge">
          <template v-if="stats.memory">
            <div class="gauge-wrap">
              <svg class="gauge-svg" viewBox="0 0 100 100">
                <circle class="gauge-bg" cx="50" cy="50" :r="GAUGE_R" fill="none" stroke-width="10" />
                <circle class="gauge-fill" cx="50" cy="50" :r="GAUGE_R" fill="none" stroke-width="10" :stroke-dasharray="GAUGE_C" :stroke-dashoffset="gaugeOffset(stats.memory.percent)" transform="rotate(-90 50 50)" />
              </svg>
              <span class="gauge-value">{{ stats.memory.percent }}%</span>
            </div>
            <p class="gauge-detail">已用 {{ formatMB(stats.memory.used_mb) }} / 共 {{ formatMB(stats.memory.total_mb) }}</p>
          </template>
          <p v-else class="muted">暂不可用</p>
        </div>
      </section>

      <section class="section">
        <h2 class="section-title">磁盘</h2>
        <div v-if="stats.disk && stats.disk.length" class="card disk-list">
          <div v-for="d in stats.disk" :key="d.mountpoint" class="disk-item disk-item-gauge">
            <div class="disk-mount">{{ d.mountpoint }}</div>
            <div class="gauge-wrap gauge-wrap-sm">
              <svg class="gauge-svg gauge-svg-sm" viewBox="0 0 100 100">
                <circle class="gauge-bg" cx="50" cy="50" r="36" fill="none" stroke-width="8" />
                <circle class="gauge-fill" cx="50" cy="50" r="36" fill="none" stroke-width="8" stroke-dasharray="226" :stroke-dashoffset="226 * (1 - Math.min(100, d.percent) / 100)" transform="rotate(-90 50 50)" />
              </svg>
              <span class="gauge-value gauge-value-sm">{{ d.percent }}%</span>
            </div>
            <p class="gauge-detail">已用 {{ formatGB(d.used_gb) }} / 共 {{ formatGB(d.total_gb) }}</p>
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
              <span class="label">累计接收 / 发送</span>
              <span class="value">{{ formatBytes(stats.network.bytes_recv) }} / {{ formatBytes(stats.network.bytes_sent) }}</span>
            </div>
            <div v-if="stats.network.rate_recv_kbps != null || stats.network.rate_sent_kbps != null" class="metric-row">
              <span class="label">当前速率（KB/s）</span>
              <span class="value">接收 {{ stats.network.rate_recv_kbps ?? '—' }} / 发送 {{ stats.network.rate_sent_kbps ?? '—' }}</span>
            </div>
            <div v-if="currentTarget === 'local' && stats.network.network_history?.length" class="traffic-chart-wrap">
              <canvas ref="trafficChartRef" height="200"></canvas>
            </div>
          </template>
          <p v-else class="muted">暂不可用</p>
        </div>
      </section>

      <section v-if="currentTarget === 'local'" class="section">
        <h2 class="section-title">进程列表</h2>
        <div class="card">
          <div class="process-toolbar">
            <label class="process-label">排序</label>
            <select v-model="processSortBy" class="process-select" @change="fetchProcesses">
              <option value="cpu_percent">CPU 使用率</option>
              <option value="memory_mb">内存占用</option>
              <option value="name">进程名</option>
              <option value="pid">PID</option>
            </select>
            <label class="process-label">条数</label>
            <select v-model="processLimit" class="process-select" @change="fetchProcesses">
              <option :value="20">20</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
              <option :value="200">200</option>
            </select>
            <input
              v-model="processNameFilter"
              type="text"
              class="process-filter"
              placeholder="按名称过滤"
              @keyup.enter="fetchProcesses"
            />
            <button type="button" class="btn-secondary" :disabled="processLoading" @click="fetchProcesses">
              {{ processLoading ? '加载中…' : '刷新' }}
            </button>
          </div>
          <p v-if="processError" class="form-error">{{ processError }}</p>
          <div v-else-if="processLoading && !processes.length" class="state state-loading">加载中…</div>
          <div v-else class="table-wrap">
            <table class="process-table">
              <thead>
                <tr>
                  <th>PID</th>
                  <th>名称</th>
                  <th>状态</th>
                  <th>用户</th>
                  <th>内存 (MB)</th>
                  <th>CPU %</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in processes" :key="p.pid">
                  <td>{{ p.pid }}</td>
                  <td class="process-name">{{ p.name }}</td>
                  <td>{{ p.status }}</td>
                  <td>{{ p.username }}</td>
                  <td>{{ p.memory_mb != null ? p.memory_mb : '—' }}</td>
                  <td>{{ p.cpu_percent != null ? p.cpu_percent : '—' }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="!processLoading && processes.length === 0" class="muted">暂无进程数据</p>
          </div>
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
          <div class="remote-actions">
            <button type="button" class="btn-secondary" @click="openRemoteForm">修改配置</button>
            <button type="button" class="btn-secondary" :disabled="installPsutilLoading" @click="installPsutilOnRemote">
              {{ installPsutilLoading ? '执行中…' : '在远程主机安装 psutil' }}
            </button>
          </div>
          <p v-if="installPsutilMessage" class="form-success">{{ installPsutilMessage }}</p>
          <p v-if="installPsutilError" class="form-error">{{ installPsutilError }}</p>
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

.card-gauge {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
}

.gauge-wrap {
  position: relative;
  width: 120px;
  height: 120px;
  flex-shrink: 0;
}

.gauge-svg {
  width: 100%;
  height: 100%;
  transform: rotate(0deg);
}

.gauge-bg {
  stroke: var(--bg-tertiary);
}

.gauge-fill {
  stroke: var(--accent);
  transition: stroke-dashoffset 0.3s ease;
}

.gauge-value {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--text-primary);
}

.gauge-detail {
  margin: 0;
  font-size: 0.9rem;
  color: var(--text-muted);
}

.gauge-wrap-sm {
  width: 80px;
  height: 80px;
}

.gauge-svg-sm {
  width: 100%;
  height: 100%;
}

.gauge-value-sm {
  font-size: 0.95rem;
}

.disk-item-gauge {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}

.traffic-chart-wrap {
  margin-top: 1rem;
  height: 200px;
  position: relative;
}

.remote-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.5rem;
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

.process-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  margin-bottom: 1rem;
}

.process-label {
  font-size: 0.85rem;
  color: var(--text-muted);
}

.process-select {
  padding: 0.35rem 0.6rem;
  font-size: 0.9rem;
  color: var(--text-primary);
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}

.process-filter {
  padding: 0.35rem 0.6rem;
  font-size: 0.9rem;
  width: 140px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
}

.process-filter:focus {
  outline: none;
  border-color: var(--accent);
}

.table-wrap {
  overflow-x: auto;
}

.process-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.process-table th,
.process-table td {
  padding: 0.5rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
}

.process-table th {
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
}

.process-table tbody tr:hover {
  background: var(--bg-tertiary);
}

.process-name {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const currentUser = ref({ username: '' })
const statusItems = ref([
  { label: 'CPU', value: '—', tip: '加载中…' },
  { label: '内存', value: '—', tip: '加载中…' },
  { label: '磁盘', value: '—', tip: '加载中…' },
])

onMounted(async () => {
  try {
    const res = await fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}` },
    })
    if (res.ok) {
      const data = await res.json()
      currentUser.value = data
    }
  } catch (_) {}
  try {
    const res = await fetch('/api/monitor/stats', {
      headers: { Authorization: `Bearer ${localStorage.getItem('panel_token')}` },
    })
    if (res.ok) {
      const data = await res.json()
      const rootDisk = data.disk && data.disk.length ? data.disk[0] : null
      statusItems.value = [
        { label: 'CPU', value: data.cpu_percent != null ? `${data.cpu_percent}%` : '—', tip: '' },
        { label: '内存', value: data.memory != null ? `${data.memory.percent}%` : '—', tip: '' },
        { label: '磁盘', value: rootDisk != null ? `${rootDisk.percent}%` : '—', tip: '' },
      ]
    } else {
      statusItems.value = [
        { label: 'CPU', value: '—', tip: '阶段 2 接入' },
        { label: '内存', value: '—', tip: '阶段 2 接入' },
        { label: '磁盘', value: '—', tip: '阶段 2 接入' },
      ]
    }
  } catch (_) {
    statusItems.value = [
      { label: 'CPU', value: '—', tip: '阶段 2 接入' },
      { label: '内存', value: '—', tip: '阶段 2 接入' },
      { label: '磁盘', value: '—', tip: '阶段 2 接入' },
    ]
  }
})

const shortcuts = [
  { path: '/monitor', name: '系统监控', icon: '📈', desc: 'CPU、内存、磁盘、网络等' },
  { path: '/sites', name: '网站与反向代理', icon: '🌐', desc: '站点列表与 Nginx 配置' },
  { path: '/databases', name: '数据库管理', icon: '🗄️', desc: 'MySQL 库与用户管理' },
]

function go(path) {
  router.push(path)
}
</script>

<template>
  <div class="page-overview">
    <section class="welcome">
      <h1>概览</h1>
      <p class="welcome-text" v-if="currentUser.username">
        你好，<strong>{{ currentUser.username }}</strong>。这里是系统概览与快捷入口。
      </p>
      <p class="welcome-text muted" v-else>系统概览与快捷入口。</p>
    </section>

    <section class="shortcuts">
      <h2 class="section-title">快捷入口</h2>
      <div class="shortcut-grid">
        <button
          v-for="item in shortcuts"
          :key="item.path"
          type="button"
          class="shortcut-card"
          @click="go(item.path)"
        >
          <span class="shortcut-icon">{{ item.icon }}</span>
          <span class="shortcut-name">{{ item.name }}</span>
          <span class="shortcut-desc">{{ item.desc }}</span>
        </button>
      </div>
    </section>

    <section class="status">
      <h2 class="section-title">基础状态</h2>
      <div class="status-card">
        <div
          v-for="item in statusItems"
          :key="item.label"
          class="status-row"
        >
          <span class="status-label">{{ item.label }}</span>
          <span class="status-value">{{ item.value }}</span>
          <span class="status-tip">{{ item.tip }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page-overview {
  max-width: 900px;
}

.welcome {
  margin-bottom: 2rem;
}

.page-overview h1,
.section-title {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.5rem 0;
}

.section-title {
  font-size: 1.125rem;
  margin-bottom: 1rem;
}

.welcome-text {
  margin: 0;
  font-size: 1rem;
  color: var(--text-secondary);
}

.welcome-text.muted {
  color: var(--text-muted);
}

.welcome-text strong {
  color: var(--text-primary);
}

.shortcuts {
  margin-bottom: 2rem;
}

.shortcut-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 1rem;
}

.shortcut-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.35rem;
  padding: 1.25rem;
  text-align: left;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.shortcut-card:hover {
  border-color: var(--accent);
  box-shadow: 0 2px 8px rgba(61, 126, 255, 0.12);
}

.shortcut-icon {
  font-size: 1.5rem;
}

.shortcut-name {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text-primary);
}

.shortcut-desc {
  font-size: 0.875rem;
  color: var(--text-muted);
}

.status-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.status-row {
  display: grid;
  grid-template-columns: 100px 1fr auto;
  align-items: center;
  gap: 1rem;
  padding: 0.75rem 1.25rem;
  border-bottom: 1px solid var(--border);
}

.status-row:last-child {
  border-bottom: none;
}

.status-label {
  font-size: 0.95rem;
  font-weight: 500;
  color: var(--text-primary);
}

.status-value {
  font-size: 0.95rem;
  color: var(--text-secondary);
}

.status-tip {
  font-size: 0.8rem;
  color: var(--text-muted);
}
</style>

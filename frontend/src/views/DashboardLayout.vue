<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getTheme, toggleTheme } from '../theme'
import ChangePasswordModal from '../components/ChangePasswordModal.vue'

const router = useRouter()
const route = useRoute()
const theme = ref(getTheme())
const currentUser = ref({ username: '' })
const showChangePassword = ref(false)

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
})

const navItems = [
  { path: '/', name: '概览', icon: '📊' },
  { path: '/monitor', name: '系统监控', icon: '📈' },
  { path: '/sites', name: '网站与反向代理', icon: '🌐' },
  { path: '/databases', name: '数据库管理', icon: '🗄️' },
]

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

function onLogout() {
  localStorage.removeItem('panel_token')
  router.push('/login')
}

function onToggleTheme() {
  theme.value = toggleTheme()
}

function goOverview() {
  router.push('/')
}

function openChangePassword() {
  showChangePassword.value = true
}
</script>

<template>
  <div class="dashboard-layout">
    <aside class="sidebar">
      <div class="sidebar-header" @click="goOverview">
        <span class="app-name">PanelLab</span>
      </div>
      <nav class="sidebar-nav">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: isActive(item.path) }"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          <span class="nav-text">{{ item.name }}</span>
        </router-link>
      </nav>
      <div class="sidebar-footer">
        <div class="user-info" v-if="currentUser.username">
          <span class="user-name">{{ currentUser.username }}</span>
        </div>
        <div class="footer-actions">
          <button
            type="button"
            class="btn-side"
            :aria-label="theme === 'light' ? '切换深色' : '切换浅色'"
            @click="onToggleTheme"
          >
            {{ theme === 'light' ? '🌙' : '☀️' }}
          </button>
          <button type="button" class="btn-side" @click="openChangePassword">修改密码</button>
          <button type="button" class="btn-side" @click="onLogout">退出</button>
        </div>
      </div>
    </aside>
    <main class="main">
      <router-view />
    </main>
    <ChangePasswordModal
      :show="showChangePassword"
      @close="showChangePassword = false"
      @success="showChangePassword = false"
    />
  </div>
</template>

<style scoped>
.dashboard-layout {
  min-height: 100vh;
  display: flex;
  background: var(--bg-primary);
  background-image: var(--texture);
}

.sidebar {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  box-shadow: 1px 0 0 var(--shadow);
}

.sidebar-header {
  padding: 1rem 1.25rem;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
}

.sidebar-header:hover .app-name {
  color: var(--text-primary);
}

.app-name {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-primary);
}

.sidebar-nav {
  flex: 1;
  padding: 0.75rem 0;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 1.25rem;
  margin: 0 0.5rem;
  border-radius: 8px;
  color: var(--text-secondary);
  text-decoration: none;
  transition: background 0.2s, color 0.2s;
}

.nav-item:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.nav-item.active {
  color: var(--text-primary);
  background: var(--bg-tertiary);
  font-weight: 500;
}

.nav-icon {
  font-size: 1.1rem;
}

.nav-text {
  font-size: 0.95rem;
}

.sidebar-footer {
  padding: 1rem 1.25rem;
  border-top: 1px solid var(--border);
}

.user-info {
  margin-bottom: 0.5rem;
}

.user-name {
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.footer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.btn-side {
  padding: 0.35rem 0.6rem;
  font-size: 0.85rem;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
}

.btn-side:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.main {
  flex: 1;
  min-width: 0;
  padding: 2rem 1.5rem;
}
</style>

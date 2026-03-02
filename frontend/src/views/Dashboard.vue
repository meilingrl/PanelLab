<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getTheme, toggleTheme } from '../theme'

const router = useRouter()
const theme = ref(getTheme())

function onLogout() {
  localStorage.removeItem('panel_token')
  router.push('/login')
}

function onToggleTheme() {
  theme.value = toggleTheme()
}
</script>

<template>
  <div class="dashboard">
    <header class="header">
      <div class="header-left">
        <span class="app-name">PanelLab</span>
      </div>
      <div class="header-right">
        <button type="button" class="btn-icon" :aria-label="theme === 'light' ? '切换深色' : '切换浅色'" @click="onToggleTheme">
          {{ theme === 'light' ? '🌙' : '☀️' }}
        </button>
        <button type="button" class="btn-logout" @click="onLogout">退出</button>
      </div>
    </header>
    <main class="main">
      <h1>仪表盘</h1>
      <p class="muted">登录成功。此处为仪表盘占位，后续接入系统状态与快捷入口。</p>
    </main>
  </div>
</template>

<style scoped>
.dashboard {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  background-image: var(--texture);
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1.5rem;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  box-shadow: 0 1px 0 var(--shadow);
}

.app-name {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-primary);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.btn-icon {
  padding: 0.4rem 0.6rem;
  font-size: 1.1rem;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  transition: background 0.2s, border-color 0.2s;
}

.btn-icon:hover {
  background: var(--bg-tertiary);
  border-color: var(--text-muted);
}

.btn-logout {
  padding: 0.4rem 0.75rem;
  font-size: 0.9rem;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  transition: background 0.2s, color 0.2s;
}

.btn-logout:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.main {
  flex: 1;
  padding: 2rem 1.5rem;
}

.main h1 {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.5rem 0;
}

.muted {
  color: var(--text-secondary);
  margin: 0;
  font-size: 0.95rem;
}
</style>

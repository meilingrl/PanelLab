<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { initTheme, getTheme, toggleTheme } from '../theme'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const usernameFocused = ref(false)
const passwordFocused = ref(false)
const logoSrc = ref(true)
const theme = ref(getTheme())

onMounted(() => {
  initTheme()
  theme.value = getTheme()
})

function onToggleTheme() {
  theme.value = toggleTheme()
}

const hasUsername = () => username.value.length > 0
const hasPassword = () => password.value.length > 0

async function onSubmit() {
  error.value = ''
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: username.value.trim(),
        password: password.value,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.detail || data.message || '用户名或密码错误'
      return
    }
    if (data.token) {
      localStorage.setItem('panel_token', data.token)
    }
    const redirect = route.query.redirect || '/'
    router.push(redirect)
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login">
    <div class="login-bg-lines" aria-hidden="true"></div>
    <button type="button" class="theme-toggle" :aria-label="theme === 'light' ? '切换深色' : '切换浅色'" @click="onToggleTheme">
      {{ theme === 'light' ? '🌙' : '☀️' }}
    </button>

    <div class="login-card">
      <!-- 左侧：Logo + 品牌 -->
      <div class="login-left">
        <div class="brand">
          <img v-if="logoSrc" src="/panellab-logo.svg" alt="PanelLab" class="logo-img" @error="logoSrc = false" />
          <div v-else class="logo-fallback">P</div>
          <h1 class="brand-name">PanelLab</h1>
          <p class="brand-desc">服务器运维管理面板</p>
        </div>
      </div>

      <!-- 右侧：表单 -->
      <div class="login-right">
        <div class="form-card">
          <h2 class="form-title">登录</h2>
          <form @submit.prevent="onSubmit" class="form">
            <div class="field">
              <input
                id="username"
                v-model="username"
                type="text"
                autocomplete="username"
                @focus="usernameFocused = true"
                @blur="usernameFocused = false"
              />
              <label for="username" :class="{ float: usernameFocused || hasUsername() }">用户名</label>
            </div>
            <div class="field">
              <input
                id="password"
                v-model="password"
                type="password"
                autocomplete="current-password"
                @focus="passwordFocused = true"
                @blur="passwordFocused = false"
              />
              <label for="password" :class="{ float: passwordFocused || hasPassword() }">密码</label>
            </div>
            <p v-if="error" class="error-msg">{{ error }}</p>
            <button type="submit" class="btn-login" :disabled="loading">
              {{ loading ? '登录中…' : '登录' }}
            </button>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  background: var(--bg-primary);
  background-image: var(--texture);
  position: relative;
}

.login-bg-lines {
  position: absolute;
  inset: 0;
  background-image: repeating-linear-gradient(
    -45deg,
    transparent,
    transparent 12px,
    var(--border) 12px,
    var(--border) 13px
  );
  opacity: 0.4;
  pointer-events: none;
}

.theme-toggle {
  position: absolute;
  top: 1rem;
  right: 1rem;
  z-index: 10;
  padding: 0.4rem 0.6rem;
  font-size: 1.1rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-primary);
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}

.theme-toggle:hover {
  background: var(--bg-tertiary);
  border-color: var(--text-muted);
}

.login-card {
  position: relative;
  z-index: 1;
  display: flex;
  width: 100%;
  max-width: 880px;
  min-height: 420px;
  background: var(--bg-secondary);
  border-radius: 12px;
  box-shadow: 0 8px 32px var(--shadow);
  overflow: hidden;
}

.login-left {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  padding: 2rem;
}

.brand {
  position: relative;
  z-index: 1;
  text-align: center;
  padding: 2rem;
}

.logo-img,
.logo-fallback {
  width: 80px;
  height: 80px;
  margin-bottom: 1rem;
}

.logo-img {
  object-fit: contain;
}

.logo-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2rem;
  font-weight: 600;
  color: var(--accent);
  background: var(--bg-tertiary);
  border-radius: 16px;
}

.brand-name {
  font-size: 1.75rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.25rem 0;
  letter-spacing: -0.02em;
}

.brand-desc {
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin: 0;
}

.login-right {
  width: 380px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2.5rem;
  border-left: 1px solid var(--border);
}

.form-card {
  width: 100%;
  max-width: 320px;
}

.form-title {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 1.5rem 0;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.field {
  position: relative;
}

.field input {
  width: 100%;
  padding: 1rem 0.75rem 0.5rem;
  font-size: 1rem;
  font-family: inherit;
  color: var(--text-primary);
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 8px;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
  box-sizing: border-box;
}

.field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(61, 126, 255, 0.15);
}

.field label {
  position: absolute;
  left: 0.75rem;
  top: 50%;
  transform: translateY(-50%);
  font-size: 1rem;
  color: var(--text-muted);
  pointer-events: none;
  transition: top 0.2s, font-size 0.2s, color 0.2s;
}

.field label.float {
  top: 0.5rem;
  font-size: 0.75rem;
  color: var(--accent);
}

.error-msg {
  font-size: 0.875rem;
  color: var(--error);
  margin: -0.25rem 0 0 0;
}

.btn-login {
  padding: 0.75rem 1.25rem;
  font-size: 1rem;
  font-weight: 500;
  color: #fff;
  background: var(--accent);
  border: none;
  border-radius: 8px;
  transition: background 0.2s;
}

.btn-login:hover:not(:disabled) {
  background: var(--accent-hover);
}

.btn-login:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

@media (max-width: 760px) {
  .login-card {
    flex-direction: column;
    max-width: 100%;
  }
  .login-left {
    padding: 2rem 1.5rem;
  }
  .login-right {
    width: 100%;
    border-left: none;
    border-top: 1px solid var(--border);
    padding: 1.5rem;
  }
}
</style>

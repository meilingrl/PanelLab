<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { initTheme, getTheme, toggleTheme } from '../theme'
import WavyLines from '../components/WavyLines.vue'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const error = ref('')
const loading = ref(false)
const usernameFocused = ref(false)
const passwordFocused = ref(false)
const confirmFocused = ref(false)
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
const hasConfirm = () => confirmPassword.value.length > 0

async function onSubmit() {
  error.value = ''
  const u = username.value.trim()
  if (!u) {
    error.value = '请输入用户名'
    return
  }
  if (u.length < 2) {
    error.value = '用户名至少 2 个字符'
    return
  }
  if (!password.value) {
    error.value = '请输入密码'
    return
  }
  if (password.value.length < 6) {
    error.value = '密码至少 6 位'
    return
  }
  if (password.value !== confirmPassword.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  try {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: u,
        password: password.value,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.detail || data.message || '注册失败'
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
  <div class="register">
    <WavyLines />
    <button type="button" class="theme-toggle" :aria-label="theme === 'light' ? '切换深色' : '切换浅色'" @click="onToggleTheme">
      {{ theme === 'light' ? '🌙' : '☀️' }}
    </button>

    <div class="login-card">
      <div class="login-left">
        <div class="brand">
          <div class="logo-fallback">P</div>
          <h1 class="brand-name">PanelLab</h1>
          <p class="brand-desc">注册新账号</p>
        </div>
      </div>

      <div class="login-right">
        <div class="form-card">
          <h2 class="form-title">注册</h2>
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
              <label for="username" :class="{ float: usernameFocused || hasUsername() }">用户名（至少 2 位）</label>
            </div>
            <div class="field">
              <input
                id="password"
                v-model="password"
                type="password"
                autocomplete="new-password"
                @focus="passwordFocused = true"
                @blur="passwordFocused = false"
              />
              <label for="password" :class="{ float: passwordFocused || hasPassword() }">密码（至少 6 位）</label>
            </div>
            <div class="field">
              <input
                id="confirmPassword"
                v-model="confirmPassword"
                type="password"
                autocomplete="new-password"
                @focus="confirmFocused = true"
                @blur="confirmFocused = false"
              />
              <label for="confirmPassword" :class="{ float: confirmFocused || hasConfirm() }">确认密码</label>
            </div>
            <p v-if="error" class="error-msg">{{ error }}</p>
            <button type="submit" class="btn-login" :disabled="loading">
              {{ loading ? '注册中…' : '注册' }}
            </button>
            <p class="switch-link">
              已有账号？<router-link to="/login">去登录</router-link>
            </p>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.register {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  background: var(--bg-primary);
  background-image: var(--texture);
  position: relative;
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
  padding: 2rem;
}

.brand {
  text-align: center;
  padding: 2rem;
}

.logo-fallback {
  width: 80px;
  height: 80px;
  margin: 0 auto 1rem;
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

.switch-link {
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin: 0.75rem 0 0 0;
  text-align: center;
}

.switch-link a {
  color: var(--accent);
  text-decoration: none;
}

.switch-link a:hover {
  text-decoration: underline;
}
</style>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { initTheme, getTheme, toggleTheme } from '../theme'
import WavyLines from '../components/WavyLines.vue'
import QRCode from 'qrcode'

const router = useRouter()
const route = useRoute()
const tab = ref('password') // 'password' | 'sms' | 'wechat' | 'qq'
const username = ref('')
const password = ref('')
const phone = ref('')
const smsCode = ref('')
const error = ref('')
const loading = ref(false)
const usernameFocused = ref(false)
const passwordFocused = ref(false)
const phoneFocused = ref(false)
const codeFocused = ref(false)
const logoSrc = ref(true)
const theme = ref(getTheme())
const smsCountdown = ref(0)
let smsTimer = null

const wechatQrDataUrl = ref('')
const wechatState = ref('')
const wechatMock = ref(true)
let wechatPollTimer = null

const qqAuthUrl = ref('')
const qqMock = ref(true)

function parseHash() {
  const hash = window.location.hash.slice(1) || ''
  if (!hash) return
  const params = new URLSearchParams(hash)
  const token = params.get('token')
  const err = params.get('error')
  if (token) {
    localStorage.setItem('panel_token', token)
    window.location.hash = ''
    router.replace(route.query.redirect || '/')
    return
  }
  if (err) {
    try {
      error.value = decodeURIComponent(err)
    } catch (_) {
      error.value = err
    }
    window.location.hash = ''
  }
}

onMounted(() => {
  initTheme()
  theme.value = getTheme()
  parseHash()
})

function onToggleTheme() {
  theme.value = toggleTheme()
}

const hasUsername = () => username.value.length > 0
const hasPassword = () => password.value.length > 0
const hasPhone = () => /^1\d{10}$/.test(phone.value)
const hasCode = () => smsCode.value.length >= 6

function setTokenAndRedirect(data) {
  if (data.token) localStorage.setItem('panel_token', data.token)
  const redirect = route.query.redirect || '/'
  router.push(redirect)
}

async function onSubmitPassword() {
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
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.detail || data.message || '用户名或密码错误'
      return
    }
    setTokenAndRedirect(data)
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function onSendSms() {
  error.value = ''
  const p = phone.value.trim()
  if (!/^1\d{10}$/.test(p)) {
    error.value = '请输入正确的11位手机号'
    return
  }
  loading.value = true
  try {
    const res = await fetch('/api/auth/sms/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: p }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.detail || data.message || '发送失败'
      return
    }
    smsCountdown.value = 60
    smsTimer = setInterval(() => {
      smsCountdown.value--
      if (smsCountdown.value <= 0 && smsTimer) {
        clearInterval(smsTimer)
        smsTimer = null
      }
    }, 1000)
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function onSubmitSms() {
  error.value = ''
  const p = phone.value.trim()
  if (!/^1\d{10}$/.test(p)) {
    error.value = '请输入正确的11位手机号'
    return
  }
  if (smsCode.value.length !== 6) {
    error.value = '请输入6位验证码'
    return
  }
  loading.value = true
  try {
    const res = await fetch('/api/auth/sms/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: p, code: smsCode.value }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.detail || data.message || '验证码错误或已过期'
      return
    }
    setTokenAndRedirect(data)
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function loadWechatQr() {
  wechatQrDataUrl.value = ''
  wechatState.value = ''
  wechatMock.value = true
  if (wechatPollTimer) {
    clearInterval(wechatPollTimer)
    wechatPollTimer = null
  }
  try {
    const res = await fetch('/api/auth/wechat/qr')
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return
    wechatMock.value = !!data.mock
    wechatState.value = data.state || ''
    if (!data.mock && data.qr_url) {
      wechatQrDataUrl.value = await QRCode.toDataURL(data.qr_url, { width: 220, margin: 1 })
      if (wechatState.value) wechatStartPoll()
    }
  } catch (_) {}
}

function wechatStartPoll() {
  if (wechatPollTimer) return
  wechatPollTimer = setInterval(async () => {
    if (!wechatState.value) return
    try {
      const r = await fetch(`/api/auth/wechat/poll?state=${encodeURIComponent(wechatState.value)}`)
      if (r.status !== 200) return
      const data = await r.json().catch(() => ({}))
      if (wechatPollTimer) {
        clearInterval(wechatPollTimer)
        wechatPollTimer = null
      }
      if (data.token) setTokenAndRedirect(data)
    } catch (_) {}
  }, 2000)
}

async function onWechatMockLogin() {
  error.value = ''
  loading.value = true
  try {
    const res = await fetch('/api/auth/wechat/callback', { method: 'POST' })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.detail || data.message || '登录失败'
      return
    }
    setTokenAndRedirect(data)
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function loadQqUrl() {
  qqAuthUrl.value = ''
  qqMock.value = true
  try {
    const res = await fetch('/api/auth/qq/url')
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return
    qqMock.value = !!data.mock
    if (!data.mock && data.auth_url) qqAuthUrl.value = data.auth_url
  } catch (_) {}
}

function onQqLogin() {
  if (qqAuthUrl.value) window.location.href = qqAuthUrl.value
}

async function onQqMockLogin() {
  error.value = ''
  loading.value = true
  try {
    const res = await fetch('/api/auth/qq/callback', { method: 'POST' })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.detail || data.message || '登录失败'
      return
    }
    setTokenAndRedirect(data)
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(tab, (t) => {
  if (t === 'wechat') loadWechatQr()
  if (t === 'qq') loadQqUrl()
})
</script>

<template>
  <div class="login">
    <WavyLines />
    <button type="button" class="theme-toggle" :aria-label="theme === 'light' ? '切换深色' : '切换浅色'" @click="onToggleTheme">
      {{ theme === 'light' ? '🌙' : '☀️' }}
    </button>

    <div class="login-card">
      <!-- 左侧：Logo + 品牌 -->
      <div class="login-left">
        <div class="brand">
          <img v-if="logoSrc" src="/panellab-logo.png" alt="PanelLab" class="logo-img" @error="logoSrc = false" />
          <div v-else class="logo-fallback">P</div>
          <h1 class="brand-name">PanelLab</h1>
          <p class="brand-desc">服务器运维管理面板</p>
        </div>
      </div>

      <!-- 右侧：Tab + 表单 -->
      <div class="login-right">
        <div class="form-card">
          <h2 class="form-title">登录</h2>
          <div class="tabs">
            <button type="button" :class="{ active: tab === 'password' }" @click="tab = 'password'; error = ''">账号密码</button>
            <button type="button" :class="{ active: tab === 'sms' }" @click="tab = 'sms'; error = ''">手机验证码</button>
            <button type="button" :class="{ active: tab === 'wechat' }" @click="tab = 'wechat'; error = ''">微信</button>
            <button type="button" :class="{ active: tab === 'qq' }" @click="tab = 'qq'; error = ''">QQ</button>
          </div>

          <!-- 账号密码 -->
          <form v-show="tab === 'password'" @submit.prevent="onSubmitPassword" class="form">
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
            <p class="switch-link">
              没有账号？<router-link to="/register">注册</router-link>
            </p>
          </form>

          <!-- 手机验证码 -->
          <form v-show="tab === 'sms'" @submit.prevent="onSubmitSms" class="form">
            <div class="field">
              <input
                id="phone"
                v-model="phone"
                type="tel"
                maxlength="11"
                placeholder=" "
                autocomplete="tel"
                @focus="phoneFocused = true"
                @blur="phoneFocused = false"
              />
              <label for="phone" :class="{ float: phoneFocused || hasPhone() }">手机号</label>
            </div>
            <div class="field row">
              <input
                id="smsCode"
                v-model="smsCode"
                type="text"
                maxlength="6"
                placeholder=" "
                inputmode="numeric"
                pattern="[0-9]*"
                @focus="codeFocused = true"
                @blur="codeFocused = false"
              />
              <label for="smsCode" :class="{ float: codeFocused || hasCode() }">验证码</label>
              <button type="button" class="btn-sms" :disabled="smsCountdown > 0 || loading" @click="onSendSms">
                {{ smsCountdown > 0 ? `${smsCountdown}s 后重发` : '获取验证码' }}
              </button>
            </div>
            <p v-if="error" class="error-msg">{{ error }}</p>
            <button type="submit" class="btn-login" :disabled="loading">
              {{ loading ? '登录中…' : '登录' }}
            </button>
          </form>

          <!-- 微信扫码：已配置则展示二维码 + 轮询，未配置则模拟扫码 -->
          <div v-show="tab === 'wechat'" class="form">
            <p v-if="wechatMock" class="third-desc">开发环境使用模拟扫码，点击下方按钮登录。</p>
            <p v-else class="third-desc">请使用微信扫描下方二维码登录。</p>
            <div v-if="!wechatMock && wechatQrDataUrl" class="qrcode-wrap">
              <img :src="wechatQrDataUrl" alt="微信登录二维码" class="qrcode-img" />
            </div>
            <p v-if="error" class="error-msg">{{ error }}</p>
            <button v-if="wechatMock" type="button" class="btn-login" :disabled="loading" @click="onWechatMockLogin">
              {{ loading ? '登录中…' : '模拟微信扫码登录' }}
            </button>
          </div>

          <!-- QQ：已配置则跳转 QQ 授权，未配置则模拟扫码 -->
          <div v-show="tab === 'qq'" class="form">
            <p v-if="qqMock" class="third-desc">开发环境使用模拟扫码，点击下方按钮登录。</p>
            <p v-else class="third-desc">点击下方按钮使用 QQ 授权登录。</p>
            <p v-if="error" class="error-msg">{{ error }}</p>
            <button v-if="qqMock" type="button" class="btn-login" :disabled="loading" @click="onQqMockLogin">
              {{ loading ? '登录中…' : '模拟 QQ 扫码登录' }}
            </button>
            <button v-else type="button" class="btn-login" :disabled="loading" @click="onQqLogin">
              使用 QQ 登录
            </button>
          </div>
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
  margin: 0 0 1rem 0;
}

.tabs {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}

.tabs button {
  padding: 0.4rem 0.6rem;
  font-size: 0.85rem;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: color 0.2s, background 0.2s, border-color 0.2s;
}

.tabs button:hover {
  color: var(--text-primary);
  background: var(--bg-secondary);
}

.tabs button.active {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--bg-secondary);
}

.field.row {
  display: flex;
  gap: 0.5rem;
}

.field.row input {
  flex: 1;
  min-width: 0;
}

.btn-sms {
  flex-shrink: 0;
  padding: 0 0.75rem;
  font-size: 0.875rem;
  color: var(--accent);
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
  white-space: nowrap;
}

.btn-sms:hover:not(:disabled) {
  background: var(--bg-secondary);
  border-color: var(--accent);
}

.btn-sms:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
}

.third-desc {
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin: 0 0 1rem 0;
}

.qrcode-wrap {
  display: flex;
  justify-content: center;
  margin: 0.5rem 0 1rem 0;
}

.qrcode-img {
  width: 220px;
  height: 220px;
  border-radius: 8px;
  background: #fff;
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

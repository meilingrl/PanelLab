<script setup>
import { ref } from 'vue'

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const error = ref('')
const success = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  success.value = ''
  if (!oldPassword.value) {
    error.value = '请输入原密码'
    return
  }
  if (!newPassword.value) {
    error.value = '请输入新密码'
    return
  }
  if (newPassword.value.length < 6) {
    error.value = '新密码至少 6 位'
    return
  }
  if (oldPassword.value === newPassword.value) {
    error.value = '新密码不能与原密码相同'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的新密码不一致'
    return
  }
  loading.value = true
  try {
    const res = await fetch('/api/auth/change-password', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
      },
      body: JSON.stringify({
        old_password: oldPassword.value,
        new_password: newPassword.value,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = data.detail || data.message || '修改失败'
      return
    }
    success.value = '密码已更新，请使用新密码登录'
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="page-settings">
    <h1 class="page-title">用户中心</h1>
    <p class="page-desc">管理账号、安全选项与获取帮助。</p>

    <section class="settings-section">
      <h2 class="section-title">快捷帮助</h2>
      <div class="help-links">
        <router-link to="/manual" class="btn-secondary">使用手册</router-link>
        <router-link to="/feedback" class="btn-secondary">反馈信箱</router-link>
      </div>
    </section>

    <section class="settings-section">
      <h2 class="section-title">修改密码</h2>
      <form class="settings-form" @submit.prevent="submit">
        <div class="field">
          <label for="cp-old">原密码</label>
          <input
            id="cp-old"
            v-model="oldPassword"
            type="password"
            autocomplete="current-password"
            placeholder="请输入原密码"
          />
        </div>
        <div class="field">
          <label for="cp-new">新密码</label>
          <input
            id="cp-new"
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            placeholder="至少 6 位"
          />
        </div>
        <div class="field">
          <label for="cp-confirm">确认新密码</label>
          <input
            id="cp-confirm"
            v-model="confirmPassword"
            type="password"
            autocomplete="new-password"
            placeholder="再次输入新密码"
          />
        </div>
        <p v-if="error" class="msg error-msg">{{ error }}</p>
        <p v-if="success" class="msg success-msg">{{ success }}</p>
        <button type="submit" class="btn-submit" :disabled="loading">
          {{ loading ? '提交中…' : '确认修改' }}
        </button>
      </form>
    </section>
  </div>
</template>

<style scoped>
.page-settings {
  max-width: 480px;
}

.page-title {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.5rem 0;
}

.page-desc {
  margin: 0 0 1.5rem 0;
  font-size: 0.95rem;
  color: var(--text-secondary);
}

.settings-section {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}

.help-links {
  display: flex;
  gap: 1rem;
}

.btn-secondary {
  display: inline-block;
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  text-decoration: none;
  transition: all 0.2s;
}

.btn-secondary:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.section-title {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 1.25rem 0;
}

.settings-form .field {
  margin-bottom: 1rem;
}

.settings-form .field label {
  display: block;
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin-bottom: 0.35rem;
}

.settings-form .field input {
  width: 100%;
  padding: 0.5rem 0.75rem;
  font-size: 0.95rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  box-sizing: border-box;
}

.settings-form .field input:focus {
  outline: none;
  border-color: var(--text-muted);
}

.msg {
  font-size: 0.9rem;
  margin: 0 0 1rem 0;
}

.error-msg {
  color: var(--error, #c00);
}

.success-msg {
  color: var(--text-secondary);
}

.btn-submit {
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: #fff;
  background: var(--accent, #2563eb);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: opacity 0.2s;
}

.btn-submit:hover:not(:disabled) {
  opacity: 0.9;
}

.btn-submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>

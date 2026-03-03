<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'success'])

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const error = ref('')
const loading = ref(false)

watch(() => props.show, (visible) => {
  if (!visible) {
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    error.value = ''
  }
})

async function submit() {
  error.value = ''
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
    emit('success')
    emit('close')
  } catch (e) {
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

function onClose() {
  if (!loading.value) emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="modal-backdrop" @click.self="onClose">
      <div class="modal-card" role="dialog" aria-labelledby="modal-title">
        <div class="modal-header">
          <h2 id="modal-title">修改密码</h2>
          <button type="button" class="btn-close" aria-label="关闭" @click="onClose">×</button>
        </div>
        <form @submit.prevent="submit" class="modal-body">
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
          <p v-if="error" class="error-msg">{{ error }}</p>
          <div class="modal-actions">
            <button type="button" class="btn-secondary" @click="onClose">取消</button>
            <button type="submit" class="btn-primary" :disabled="loading">
              {{ loading ? '提交中…' : '确认修改' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  min-width: 320px;
  max-width: 90vw;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border);
}

.modal-header h2 {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.btn-close {
  font-size: 1.5rem;
  line-height: 1;
  color: var(--text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0 0.25rem;
}

.btn-close:hover {
  color: var(--text-primary);
}

.modal-body {
  padding: 1.25rem;
}

.field {
  margin-bottom: 1rem;
}

.field label {
  display: block;
  font-size: 0.9rem;
  color: var(--text-secondary);
  margin-bottom: 0.35rem;
}

.field input {
  width: 100%;
  padding: 0.5rem 0.75rem;
  font-size: 0.95rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  box-sizing: border-box;
}

.field input:focus {
  outline: none;
  border-color: var(--text-muted);
}

.error-msg {
  color: var(--error, #c00);
  font-size: 0.9rem;
  margin: 0 0 1rem 0;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-top: 1rem;
}

.btn-secondary {
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}

.btn-secondary:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.btn-primary {
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: #fff;
  background: var(--accent, #2563eb);
  border: none;
  border-radius: 6px;
  cursor: pointer;
}

.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>

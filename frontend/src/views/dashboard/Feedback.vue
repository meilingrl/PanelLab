<script setup>
import { ref } from 'vue'

const content = ref('')
const contact = ref('')
const sending = ref(false)
const message = ref('')
const isError = ref(false)

async function onSubmit() {
  const text = content.value.trim()
  if (!text) {
    message.value = '请填写反馈内容'
    isError.value = true
    return
  }
  sending.value = true
  message.value = ''
  isError.value = false
  try {
    const res = await fetch('/api/feedback', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
      },
      body: JSON.stringify({
        content: text,
        contact: contact.value.trim() || undefined,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (res.ok) {
      content.value = ''
      contact.value = ''
      message.value = data.message || '感谢您的反馈，我们已收到。'
    } else {
      message.value = data.detail || '提交失败，请稍后重试'
      isError.value = true
    }
  } catch (_) {
    message.value = '网络错误，请稍后重试'
    isError.value = true
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <div class="page-feedback">
    <h1 class="page-title">反馈信箱</h1>
    <p class="page-desc">如有问题或建议，请填写下方表单，我们会尽快处理。</p>

    <form class="feedback-form" @submit.prevent="onSubmit">
      <div class="form-group">
        <label for="feedback-content">反馈内容 <span class="required">*</span></label>
        <textarea
          id="feedback-content"
          v-model="content"
          rows="6"
          placeholder="请描述您的问题或建议…"
          maxlength="10000"
          :disabled="sending"
        ></textarea>
      </div>
      <div class="form-group">
        <label for="feedback-contact">联系方式（选填）</label>
        <input
          id="feedback-contact"
          v-model="contact"
          type="text"
          placeholder="邮箱或手机，便于我们回复"
          maxlength="255"
          :disabled="sending"
        />
      </div>
      <div v-if="message" class="form-message" :class="{ error: isError }">
        {{ message }}
      </div>
      <button type="submit" class="btn-submit" :disabled="sending">
        {{ sending ? '提交中…' : '提交反馈' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.page-feedback {
  max-width: 560px;
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

.feedback-form {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
}

.form-group {
  margin-bottom: 1.25rem;
}

.form-group label {
  display: block;
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 0.4rem;
}

.required {
  color: var(--text-muted);
}

.form-group textarea,
.form-group input {
  width: 100%;
  padding: 0.6rem 0.75rem;
  font-size: 0.95rem;
  color: var(--text-primary);
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-sizing: border-box;
}

.form-group textarea:focus,
.form-group input:focus {
  outline: none;
  border-color: var(--text-muted);
}

.form-group textarea::placeholder,
.form-group input::placeholder {
  color: var(--text-muted);
}

.form-message {
  margin-bottom: 1rem;
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.form-message.error {
  color: var(--text-muted);
}

.btn-submit {
  padding: 0.5rem 1rem;
  font-size: 0.9rem;
  color: #fff;
  background: var(--accent);
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

<script setup>
import { ref, onMounted } from 'vue'
import { marked } from 'marked'

const content = ref('')
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const res = await fetch('/api/docs/user-manual')
    if (res.ok) {
      const text = await res.text()
      content.value = marked.parse(text, { async: false })
    } else {
      error.value = '加载使用手册失败'
    }
  } catch (_) {
    error.value = '加载使用手册失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="page-manual">
    <h1 class="page-title">使用手册</h1>
    <p class="page-desc">PanelLab 用户使用说明书，包含登录、各功能操作与常见问题。</p>
    <div v-if="loading" class="manual-loading">加载中…</div>
    <div v-else-if="error" class="manual-error">{{ error }}</div>
    <article v-else class="manual-body" v-html="content"></article>
  </div>
</template>

<style scoped>
.page-manual {
  max-width: 800px;
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

.manual-loading,
.manual-error {
  padding: 2rem;
  text-align: center;
  color: var(--text-secondary);
}

.manual-error {
  color: var(--text-muted);
}

.manual-body :deep(h1) { font-size: 1.35rem; margin: 1.5rem 0 0.75rem; color: var(--text-primary); }
.manual-body :deep(h2) { font-size: 1.2rem; margin: 1.25rem 0 0.5rem; color: var(--text-primary); }
.manual-body :deep(h3) { font-size: 1.05rem; margin: 1rem 0 0.5rem; color: var(--text-primary); }
.manual-body :deep(p) { margin: 0.5rem 0; line-height: 1.6; color: var(--text-secondary); }
.manual-body :deep(ul), .manual-body :deep(ol) { margin: 0.5rem 0; padding-left: 1.5rem; color: var(--text-secondary); }
.manual-body :deep(li) { margin: 0.25rem 0; }
.manual-body :deep(table) { border-collapse: collapse; width: 100%; margin: 0.75rem 0; font-size: 0.9rem; }
.manual-body :deep(th), .manual-body :deep(td) { border: 1px solid var(--border); padding: 0.5rem 0.75rem; text-align: left; }
.manual-body :deep(th) { background: var(--bg-tertiary); color: var(--text-primary); }
.manual-body :deep(td) { color: var(--text-secondary); }
.manual-body :deep(hr) { border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }
.manual-body :deep(code) { background: var(--bg-tertiary); padding: 0.15rem 0.4rem; border-radius: 4px; font-size: 0.9em; }
.manual-body :deep(pre) { background: var(--bg-tertiary); padding: 1rem; border-radius: 8px; overflow-x: auto; }
.manual-body :deep(pre code) { background: none; padding: 0; }
</style>

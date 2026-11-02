<script setup>
const props = defineProps({
  preview: { type: Object, required: true },
  expired: Boolean,
  executing: Boolean,
});
defineEmits(["confirm", "cancel"]);
const operations = {
  create: "新增",
  update: "编辑",
  apply: "生效",
  delete: "删除",
};
function expiresAt() {
  return new Date(props.preview.expires_at).toLocaleString("zh-CN");
}
</script>
<template>
  <section class="review" aria-label="待确认变更">
    <div class="review-heading">
      <div>
        <p class="eyebrow">待确认变更 · 面板本机</p>
        <h2>{{ operations[preview.operation] }} {{ preview.site_name }}</h2>
      </div>
      <span class="mode">{{
        preview.simulated
          ? "模拟模式 · 不写配置或运行 Nginx"
          : "实际执行 · 将修改本机配置"
      }}</span>
    </div>
    <p class="expiry">
      {{ expired ? "预览已过期，请重新预览" : `确认有效期至 ${expiresAt()}` }}
    </p>
    <ul class="warnings">
      <li v-for="(warning, index) in preview.warnings" :key="index">
        {{ warning }}
      </li>
    </ul>
    <h3>配置差异</h3>
    <pre class="diff">{{
      preview.diff || "配置文本无差异；请核对本次操作与执行模式。"
    }}</pre>
    <details>
      <summary>查看完整配置前后内容</summary>
      <div class="config-grid">
        <div>
          <h3>变更前</h3>
          <pre>{{ preview.before ?? "（无配置）" }}</pre>
        </div>
        <div>
          <h3>变更后</h3>
          <pre>{{ preview.after ?? "（无配置）" }}</pre>
        </div>
      </div>
    </details>
    <p class="scope">确认后仅执行本次预览。执行失败请先查看历史。</p>
    <div class="review-actions">
      <button
        type="button"
        class="cancel"
        :disabled="executing"
        @click="$emit('cancel')"
      >
        取消变更</button
      ><button
        type="button"
        class="confirm"
        :disabled="expired || executing"
        @click="$emit('confirm')"
      >
        确认执行变更
      </button>
    </div>
  </section>
</template>
<style scoped>
.review {
  margin: 1.25rem 0;
  padding: 1.25rem;
  background: var(--bg-secondary);
  border: 2px solid var(--accent);
  border-radius: 12px;
  color: var(--text-primary);
}
.review-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
}
.eyebrow {
  font-size: 0.8rem;
  color: var(--accent);
  margin: 0 0 0.4rem;
}
h2 {
  font-size: 1.2rem;
  margin: 0;
  overflow-wrap: anywhere;
}
.mode {
  font-size: 0.85rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.4rem 0.6rem;
  color: var(--text-secondary);
}
.expiry,
.scope {
  color: var(--text-secondary);
  font-size: 0.85rem;
  line-height: 1.6;
}
.warnings {
  padding-left: 1.25rem;
  line-height: 1.7;
  font-size: 0.9rem;
}
.diff,
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.8rem;
  max-height: 340px;
  overflow: auto;
  font-size: 0.8rem;
  line-height: 1.65;
}
h3 {
  font-size: 0.9rem;
  margin: 0.8rem 0 0.4rem;
}
.config-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}
summary {
  cursor: pointer;
  font-size: 0.9rem;
}
.review-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-top: 1rem;
}
button {
  font: inherit;
  font-size: 0.9rem;
  padding: 0.6rem 1rem;
  border-radius: 6px;
  cursor: pointer;
}
.cancel {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
.confirm {
  background: var(--accent);
  color: white;
  border: 1px solid var(--accent);
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
button:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}
@media (max-width: 600px) {
  .review {
    padding: 1rem;
  }
  .config-grid {
    grid-template-columns: 1fr;
  }
  .review-actions {
    flex-direction: column-reverse;
  }
  .review-actions button {
    width: 100%;
  }
}
</style>

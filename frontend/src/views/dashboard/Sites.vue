<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import SiteChangeReview from "../../components/SiteChangeReview.vue";

const loading = ref(true);
const items = ref([]);
const error = ref("");
const sitesError = ref("");
const notice = ref("");
const noticeStatus = ref("");
const showForm = ref(false);
const editingId = ref(null);
const form = ref(defaultForm());
const preview = ref(null);
const previewing = ref(false);
const executing = ref(false);
const history = ref([]);
const historyLoading = ref(false);
const historyError = ref("");
const historyLoaded = ref(false);
const hasMore = ref(false);
const now = ref(Date.now());
let revision = 0;
let sitesRevision = 0;
let historyTask = null;
let timer;
let disposed = false;
const isEditing = computed(() => editingId.value != null);
const expired = computed(
  () => preview.value && Date.parse(preview.value.expires_at) <= now.value,
);

function defaultForm() {
  return {
    name: "",
    domain: "",
    site_type: "proxy",
    root_path: "",
    proxy_target: "",
    listen_port: 80,
    enabled: true,
  };
}
function authHeaders(extra = {}) {
  return {
    Authorization: `Bearer ${localStorage.getItem("panel_token")}`,
    ...extra,
  };
}
function errorMessage(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map(
        (e) =>
          `${(e.loc || []).filter((p) => p !== "body").join(" / ")}：${e.msg || "格式不正确"}`,
      )
      .join("；");
  if (detail && typeof detail === "object") {
    const audit = detail.change_id ? `（变更 #${detail.change_id}）` : "";
    const review = [
      "manual_review_required",
      "recovery_failed",
      "pending",
    ].includes(detail.status)
      ? " 请人工核查配置与运行状态，勿重复执行。"
      : "";
    return `${detail.message || "操作失败"}${audit}${review}`;
  }
  return "请求失败，请稍后重试";
}
async function request(url, payload) {
  const res = await fetch(url, {
    headers: authHeaders(payload ? { "Content-Type": "application/json" } : {}),
    ...(payload ? { method: "POST", body: JSON.stringify(payload) } : {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorMessage(data.detail || data.message));
  return data;
}
function invalidatePreview() {
  revision++;
  preview.value = null;
  previewing.value = false;
}
watch(form, invalidatePreview, { deep: true, flush: "sync" });
function clearFeedback() {
  error.value = "";
  notice.value = "";
}
function openCreate() {
  if (executing.value) return;
  invalidatePreview();
  clearFeedback();
  showForm.value = true;
  editingId.value = null;
  form.value = defaultForm();
}
function openEdit(item) {
  if (executing.value) return;
  invalidatePreview();
  clearFeedback();
  showForm.value = true;
  editingId.value = item.id;
  form.value = Object.fromEntries(
    Object.keys(defaultForm()).map((key) => [
      key,
      item[key] ?? defaultForm()[key],
    ]),
  );
}
function closeForm() {
  if (executing.value) return;
  invalidatePreview();
  showForm.value = false;
  editingId.value = null;
}
function cancelPreview() {
  if (!executing.value) invalidatePreview();
}
async function fetchSites() {
  const current = ++sitesRevision;
  sitesError.value = "";
  loading.value = true;
  try {
    const data = await request("/api/sites");
    if (!disposed && current === sitesRevision) items.value = data.items || [];
  } catch (e) {
    if (!disposed && current === sitesRevision)
      sitesError.value = `站点列表：${e.message}`;
  } finally {
    if (!disposed && current === sitesRevision) loading.value = false;
  }
}
function fetchHistory(append = false, preserveLoaded = false) {
  if (historyTask) return historyTask;
  historyLoading.value = true;
  historyError.value = "";
  const cursor = append ? history.value.at(-1)?.id : null;
  const preservePages = preserveLoaded && history.value.length > 0;
  historyTask = (async () => {
    try {
      const data = await request(
        `/api/site-changes?limit=20${cursor ? `&before_id=${cursor}` : ""}`,
      );
      if (disposed) return;
      const incoming = data.items || [];
      if (preservePages) {
        // Failed execution must not remove evidence the user already opened.
        const merged = new Map(history.value.map((item) => [item.id, item]));
        for (const item of incoming) merged.set(item.id, item);
        history.value = [...merged.values()].sort((a, b) => b.id - a.id);
      } else {
        history.value = append
          ? [
              ...history.value,
              ...incoming.filter(
                (item) => !history.value.some((old) => old.id === item.id),
              ),
            ]
          : incoming;
        hasMore.value = incoming.length === 20;
      }
      // A preserved list keeps its oldest loaded cursor and pagination boundary.
      historyLoaded.value = true;
    } catch (e) {
      if (!disposed) historyError.value = e.message;
    } finally {
      if (!disposed) historyLoading.value = false;
      historyTask = null;
    }
  })();
  return historyTask;
}
async function startPreview(payload) {
  if (previewing.value || executing.value) return;
  invalidatePreview();
  clearFeedback();
  const current = revision;
  previewing.value = true;
  try {
    const data = await request("/api/site-changes/preview", payload);
    if (current !== revision || disposed) return;
    now.value = Date.now();
    preview.value = data;
  } catch (e) {
    if (current === revision && !disposed) error.value = e.message;
  } finally {
    if (current === revision && !disposed) previewing.value = false;
  }
}
async function saveSite() {
  if (previewing.value || executing.value) return;
  const config = {
    ...form.value,
    name: form.value.name.trim(),
    domain: form.value.domain.trim(),
    root_path: form.value.root_path.trim(),
    proxy_target: form.value.proxy_target.trim(),
    listen_port: Number(form.value.listen_port),
  };
  if (!config.name || !config.domain) {
    error.value = "请填写站点名称和域名";
    return;
  }
  if (config.site_type === "static" && !config.root_path) {
    error.value = "静态站点必须填写根目录";
    return;
  }
  if (config.site_type === "proxy" && !config.proxy_target) {
    error.value = "反向代理站点必须填写代理目标地址";
    return;
  }
  await startPreview({
    operation: isEditing.value ? "update" : "create",
    ...(isEditing.value ? { site_id: editingId.value } : {}),
    config,
  });
}
function previewAction(operation, item) {
  if (executing.value) return;
  invalidatePreview();
  showForm.value = false;
  return startPreview({ operation, site_id: item.id });
}
async function executePreview() {
  if (
    !preview.value ||
    executing.value ||
    Date.parse(preview.value.expires_at) <= Date.now()
  ) {
    now.value = Date.now();
    return;
  }
  const token = preview.value.token;
  executing.value = true;
  clearFeedback();
  invalidatePreview();
  let failed = false;
  try {
    const data = await request("/api/site-changes/execute", { token });
    if (disposed) return;
    noticeStatus.value = data.change.status;
    notice.value = data.change.message || statusText(data.change.status);
    showForm.value = false;
  } catch (e) {
    failed = true;
    if (!disposed) error.value = e.message;
  } finally {
    // Consume the token once even on transport errors; history is the recovery path.
    await historyTask;
    if (!disposed)
      await Promise.all([fetchSites(), fetchHistory(false, failed)]);
    if (!disposed) executing.value = false;
  }
}
function siteTypeText(type) {
  return type === "static" ? "静态站点" : "反向代理";
}
function statusText(status) {
  return (
    {
      active: "已生效",
      applied: "已执行",
      simulated: "模拟完成 · 未生效",
      error: "错误",
      draft: "未生效",
      pending: "待核查 · 执行结果未确认",
      recovery_failed: "恢复失败 · 需人工核查",
      failed_restored: "执行失败 · 已恢复",
    }[status] ||
    status ||
    "未知状态"
  );
}
function statusClass(status) {
  if (["active", "applied"].includes(status)) return "status-active";
  if (["error", "recovery_failed", "failed_restored"].includes(status))
    return "status-error";
  if (["pending", "simulated"].includes(status)) return "status-warning";
  return "status-draft";
}
function operationText(operation) {
  return (
    { create: "新增", update: "编辑", apply: "生效", delete: "删除" }[
      operation
    ] || operation
  );
}
function formatResource(item) {
  return item.site_type === "static"
    ? item.root_path || "—"
    : item.proxy_target || "—";
}
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(
    /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`,
  );
  return Number.isNaN(date.getTime())
    ? "时间未知"
    : date.toLocaleString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}
onMounted(() => {
  fetchSites();
  fetchHistory();
  timer = setInterval(() => {
    now.value = Date.now();
  }, 1000);
});
onUnmounted(() => {
  disposed = true;
  revision++;
  clearInterval(timer);
});
</script>

<template>
  <div class="page-sites">
    <div class="head">
      <div>
        <h1>网站与反向代理</h1>
        <p class="muted">
          作用于面板本机。先预览配置差异，再独立确认执行；执行结果请结合变更历史核查。
        </p>
      </div>
      <button
        type="button"
        class="btn-primary"
        :disabled="executing"
        @click="openCreate"
      >
        新增站点
      </button>
    </div>

    <p v-if="error" class="msg msg-error" role="alert">{{ error }}</p>
    <p
      v-if="notice"
      class="msg"
      :class="statusClass(noticeStatus)"
      role="status"
    >
      {{ notice }}
    </p>
    <p v-if="executing" class="state" role="status">
      正在执行变更，请等待并查看历史确认结果…
    </p>

    <section class="section" aria-label="站点列表">
      <h2 class="section-title">
        站点列表 <span class="muted">{{ items.length }} 个站点</span>
      </h2>
      <p v-if="sitesError" class="msg msg-error" role="alert">
        {{ sitesError }}
      </p>
      <div v-if="loading" class="state">加载中…</div>
      <div v-else-if="items.length === 0 && !sitesError" class="state">
        暂无站点，点击右上角「新增站点」开始。
      </div>
      <div v-else-if="items.length" class="table-wrap">
        <table class="sites-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>域名</th>
              <th>类型</th>
              <th>根目录/代理目标</th>
              <th>端口</th>
              <th>状态</th>
              <th>启用</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td>{{ item.name }}</td>
              <td>{{ item.domain }}</td>
              <td>{{ siteTypeText(item.site_type) }}</td>
              <td class="resource-col" :title="formatResource(item)">
                {{ formatResource(item) }}
              </td>
              <td>{{ item.listen_port }}</td>
              <td>
                <span class="status-pill" :class="statusClass(item.status)">{{
                  statusText(item.status)
                }}</span>
              </td>
              <td>{{ item.enabled ? "是" : "否" }}</td>
              <td class="date-col">{{ formatDate(item.updated_at) }}</td>
              <td class="actions">
                <button
                  type="button"
                  class="btn-secondary"
                  :disabled="executing"
                  :aria-label="`编辑 ${item.name}`"
                  @click="openEdit(item)"
                >
                  编辑
                </button>
                <button
                  type="button"
                  class="btn-secondary"
                  :disabled="executing"
                  :aria-label="`生效 ${item.name}`"
                  @click="previewAction('apply', item)"
                >
                  生效
                </button>
                <button
                  type="button"
                  class="btn-danger"
                  :disabled="executing"
                  :aria-label="`删除 ${item.name}`"
                  @click="previewAction('delete', item)"
                >
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="showForm" class="section">
      <div class="card">
        <h2>{{ isEditing ? "编辑站点" : "新增站点" }}</h2>
        <p class="muted form-hint">
          修改表单会作废已有预览。此处提交仅生成预览。
        </p>
        <form @submit.prevent="saveSite">
          <div class="form-grid">
            <div class="form-row">
              <label for="site-name">站点名称</label>
              <input
                id="site-name"
                :disabled="executing"
                v-model="form.name"
                type="text"
                maxlength="64"
                placeholder="例如 blog"
              />
            </div>

            <div class="form-row">
              <label for="site-domain">域名</label>
              <input
                id="site-domain"
                :disabled="executing"
                v-model="form.domain"
                type="text"
                maxlength="255"
                placeholder="例如 blog.example.com"
              />
            </div>

            <div class="form-row">
              <label for="site-type">类型</label>
              <select
                id="site-type"
                :disabled="executing"
                v-model="form.site_type"
              >
                <option value="proxy">反向代理</option>
                <option value="static">静态站点</option>
              </select>
            </div>

            <div class="form-row">
              <label for="site-port">监听端口</label>
              <input
                id="site-port"
                :disabled="executing"
                v-model.number="form.listen_port"
                type="number"
                min="1"
                max="65535"
              />
            </div>

            <div class="form-row form-row-checkbox full">
              <label>
                <input
                  :disabled="executing"
                  v-model="form.enabled"
                  type="checkbox"
                />
                启用站点
              </label>
            </div>

            <div v-if="form.site_type === 'static'" class="form-row full">
              <label for="root-path">根目录</label>
              <input
                id="root-path"
                :disabled="executing"
                v-model="form.root_path"
                type="text"
                placeholder="例如 /var/www/blog"
              />
            </div>

            <div v-else class="form-row full">
              <label for="proxy-target">代理目标地址</label>
              <input
                id="proxy-target"
                :disabled="executing"
                v-model="form.proxy_target"
                type="text"
                placeholder="例如 http://127.0.0.1:3000"
              />
            </div>
          </div>

          <div class="form-actions">
            <button
              type="button"
              class="btn-secondary"
              :disabled="executing"
              @click="closeForm"
            >
              取消
            </button>
            <button
              type="submit"
              class="btn-primary"
              :disabled="previewing || executing"
            >
              {{ previewing ? "正在预览…" : "预览变更" }}
            </button>
          </div>
        </form>
      </div>
    </section>
    <SiteChangeReview
      v-if="preview"
      :preview="preview"
      :expired="expired"
      :executing="executing"
      @confirm="executePreview"
      @cancel="cancelPreview"
    />
    <p v-else-if="previewing" class="state" role="status">
      正在读取本机配置并生成预览…
    </p>

    <section class="section history-section" aria-label="变更历史">
      <div class="section-head">
        <div>
          <h2 class="section-title">变更历史</h2>
          <p class="muted">
            仅记录本面板的操作。配置原文可能包含敏感内容，请谨慎展示。
          </p>
        </div>
        <button
          type="button"
          class="btn-secondary"
          :disabled="historyLoading || executing"
          @click="fetchHistory()"
        >
          刷新历史
        </button>
      </div>
      <p v-if="historyError" class="msg msg-error" role="alert">
        {{ historyError }}；已加载的历史仍保留。
      </p>
      <p v-if="historyLoading" class="muted" role="status">历史加载中…</p>
      <p
        v-if="
          historyLoaded && !history.length && !historyError && !historyLoading
        "
        class="state"
      >
        暂无变更记录。
      </p>
      <article
        v-for="change in history"
        :key="change.id"
        class="history-item"
        :data-change-id="change.id"
      >
        <details>
          <summary>
            <span class="history-identity"
              ><strong
                >#{{ change.id }} · {{ operationText(change.operation) }}
                {{ change.site_name }}</strong
              ><time>{{ formatDate(change.created_at) }}</time></span
            >
            <span class="status-pill" :class="statusClass(change.status)">{{
              statusText(change.status)
            }}</span>
          </summary>
          <p class="history-message">{{ change.message || "无附加说明" }}</p>
          <p
            v-if="['pending', 'recovery_failed'].includes(change.status)"
            class="msg msg-error"
          >
            需要人工核查文件、数据库与 Nginx 运行状态；不要重复执行此变更。
          </p>
          <p v-if="change.status === 'applied'" class="muted">
            校验与 reload 命令已完成；不代表 HTTP 可用或 worker 已替换。
          </p>
          <div class="config-grid">
            <div>
              <h3>变更前</h3>
              <pre>{{ change.before ?? "（无配置）" }}</pre>
            </div>
            <div>
              <h3>变更后</h3>
              <pre>{{ change.after ?? "（无配置）" }}</pre>
            </div>
          </div>
          <h3>执行步骤</h3>
          <ol v-if="change.steps?.length" class="steps">
            <li v-for="(step, index) in change.steps" :key="index">
              <strong>{{ step.name }}</strong> ·
              {{ step.status === "succeeded" ? "完成" : "失败" }}：{{
                step.message
              }}
            </li>
          </ol>
          <p v-else class="muted">无执行步骤记录。</p>
        </details>
      </article>
      <button
        v-if="hasMore"
        type="button"
        class="btn-secondary next-page"
        :disabled="historyLoading || executing"
        @click="fetchHistory(true)"
      >
        加载下一页
      </button>
    </section>
  </div>
</template>

<style scoped>
.page-sites {
  max-width: 1100px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}

.page-sites h1 {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 0.35rem 0;
}

.muted {
  color: var(--text-secondary);
  margin: 0;
  font-size: 0.95rem;
}

.section {
  margin-bottom: 1rem;
}

.state {
  padding: 1.25rem;
  text-align: center;
  color: var(--text-muted);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
}

.table-wrap {
  overflow-x: auto;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
}

.sites-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.sites-table th,
.sites-table td {
  padding: 0.65rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: middle;
}

.sites-table th {
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  white-space: nowrap;
}

.sites-table tbody tr:hover {
  background: var(--bg-tertiary);
}

.resource-col {
  max-width: 250px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}

.actions {
  white-space: nowrap;
}

.status-pill {
  display: inline-block;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  font-size: 0.8rem;
}

.status-active {
  color: #0a7c34;
  background: rgba(10, 124, 52, 0.12);
}

.status-draft {
  color: var(--text-secondary);
  background: var(--bg-tertiary);
}

.status-error {
  color: #a11;
  background: rgba(170, 17, 17, 0.12);
}

.card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem;
}

.card h2 {
  margin: 0 0 1rem 0;
  color: var(--text-primary);
  font-size: 1.1rem;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  gap: 0.75rem 1rem;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.form-row.full {
  grid-column: 1 / -1;
}

.form-row label {
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.form-row input,
.form-row select {
  width: 100%;
  box-sizing: border-box;
  padding: 0.5rem 0.65rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 0.95rem;
}

.form-row input:focus,
.form-row select:focus {
  outline: none;
  border-color: var(--accent);
}

.form-row-checkbox {
  justify-content: center;
}

.form-row-checkbox label {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  cursor: pointer;
  user-select: none;
}

.form-row-checkbox input[type="checkbox"] {
  width: auto;
  cursor: pointer;
}

.date-col {
  white-space: nowrap;
  font-size: 0.85rem;
  color: var(--text-secondary);
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 1rem;
}

.msg {
  margin: 0 0 0.75rem 0;
  font-size: 0.9rem;
}

.msg-error {
  color: var(--error, #c00);
}

.msg-success {
  color: var(--success, #0a7c34);
}

.btn-primary,
.btn-secondary,
.btn-danger {
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-size: 0.9rem;
  cursor: pointer;
}

.btn-primary {
  color: #fff;
  background: var(--accent);
  border: none;
}

.btn-primary:hover:not(:disabled) {
  opacity: 0.92;
}

.btn-secondary {
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
}

.btn-secondary:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.btn-danger {
  color: #b52222;
  background: transparent;
  border: 1px solid rgba(181, 34, 34, 0.35);
}

.btn-danger:hover {
  background: rgba(181, 34, 34, 0.09);
}

.btn-primary:disabled,
.btn-secondary:disabled,
.btn-danger:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@media (max-width: 900px) {
  .head {
    flex-direction: column;
    align-items: stretch;
  }

  .form-grid {
    grid-template-columns: 1fr;
  }
}

.section-title {
  font-size: 1.1rem;
  color: var(--text-primary);
  margin: 0 0 0.75rem;
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}
.section-title .muted {
  font-size: 0.8rem;
  font-weight: 400;
}
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}
.section-head .section-title {
  margin-bottom: 0.35rem;
}
.section-head .muted {
  font-size: 0.85rem;
  line-height: 1.6;
}
.section-head button {
  flex-shrink: 0;
}
.form-hint {
  margin-bottom: 1rem;
  font-size: 0.85rem;
}
.status-warning {
  color: var(--text-primary);
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
}
.history-section {
  margin-top: 2rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
}
.history-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem;
  margin: 0.65rem 0;
  background: var(--bg-secondary);
  color: var(--text-primary);
}
.history-item summary {
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  list-style: disclosure-closed;
}
.history-identity {
  display: flex;
  gap: 0.35rem;
  flex-direction: column;
  min-width: 0;
}
.history-identity strong {
  font-size: 0.9rem;
  overflow-wrap: anywhere;
}
.history-identity time {
  font-size: 0.8rem;
  color: var(--text-muted);
}
.history-message {
  font-size: 0.9rem;
  overflow-wrap: anywhere;
  line-height: 1.6;
}
.history-item h3 {
  font-size: 0.85rem;
  margin: 0.75rem 0 0.4rem;
}
.config-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}
.history-item pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  max-height: 300px;
  overflow: auto;
  padding: 0.75rem;
  background: var(--bg-primary);
  font-size: 0.8rem;
  line-height: 1.65;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.steps {
  font-size: 0.85rem;
  line-height: 1.8;
  padding-left: 1.25rem;
  overflow-wrap: anywhere;
}
.next-page {
  display: block;
  margin: 1rem auto;
}
.msg {
  line-height: 1.7;
  overflow-wrap: anywhere;
  padding: 0.5rem;
  border-radius: 6px;
}
button:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}
.actions button {
  margin: 0.2rem;
}
.page-sites {
  min-width: 0;
}
.table-wrap {
  max-width: 100%;
}
@media (max-width: 600px) {
  .config-grid {
    grid-template-columns: 1fr;
  }
  .section-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .history-item summary {
    align-items: flex-start;
    flex-direction: column;
    gap: 0.6rem;
  }
  .head h1 {
    font-size: 1.35rem;
  }
  .muted {
    font-size: 0.85rem;
    line-height: 1.6;
  }
  .history-item {
    padding: 0.85rem;
  }
  .form-actions {
    flex-wrap: wrap;
  }
}
</style>

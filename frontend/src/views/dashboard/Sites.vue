<script setup>
import { ref, computed, onMounted } from 'vue'

const loading = ref(true)
const submitting = ref(false)
const items = ref([])
const error = ref('')
const success = ref('')
let successTimer = null

function showSuccess(msg) {
  success.value = msg
  if (successTimer) clearTimeout(successTimer)
  successTimer = setTimeout(() => { success.value = '' }, 4000)
}

const showForm = ref(false)
const editingId = ref(null)
const form = ref(defaultForm())

function defaultForm() {
  return {
    name: '',
    domain: '',
    site_type: 'proxy',
    root_path: '',
    proxy_target: '',
    listen_port: 80,
    enabled: true,
  }
}

const isEditing = computed(() => editingId.value != null)

function authHeaders(extra = {}) {
  return {
    Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
    ...extra,
  }
}

function siteTypeText(siteType) {
  return siteType === 'static' ? '静态站点' : '反向代理'
}

function statusText(status) {
  if (status === 'active') return '已生效'
  if (status === 'error') return '错误'
  return '未生效'
}

function statusClass(status) {
  if (status === 'active') return 'status-active'
  if (status === 'error') return 'status-error'
  return 'status-draft'
}

function endpoint(id = '') {
  return id ? `/api/sites/${id}` : '/api/sites'
}

async function fetchSites() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/sites', {
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(data.detail || '获取站点列表失败')
    }
    items.value = data.items || []
  } catch (e) {
    error.value = e.message || '网络错误'
    items.value = []
  } finally {
    loading.value = false
  }
}

function openCreate() {
  showForm.value = true
  editingId.value = null
  form.value = defaultForm()
  error.value = ''
  success.value = ''
  if (successTimer) clearTimeout(successTimer)
}

function openEdit(item) {
  showForm.value = true
  editingId.value = item.id
  form.value = {
    name: item.name || '',
    domain: item.domain || '',
    site_type: item.site_type || 'proxy',
    root_path: item.root_path || '',
    proxy_target: item.proxy_target || '',
    listen_port: item.listen_port || 80,
    enabled: item.enabled ?? true,
  }
  error.value = ''
  success.value = ''
  if (successTimer) clearTimeout(successTimer)
}

function closeForm() {
  showForm.value = false
  editingId.value = null
  form.value = defaultForm()
}

function formatResource(item) {
  if (item.site_type === 'static') return item.root_path || '—'
  return item.proxy_target || '—'
}

function formatDate(dt) {
  if (!dt) return '—'
  const d = new Date(dt)
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function saveSite() {
  error.value = ''
  success.value = ''
  if (!form.value.name.trim() || !form.value.domain.trim()) {
    error.value = '请填写站点名称和域名'
    return
  }
  if (form.value.site_type === 'static' && !form.value.root_path.trim()) {
    error.value = '静态站点必须填写根目录'
    return
  }
  if (form.value.site_type === 'proxy' && !form.value.proxy_target.trim()) {
    error.value = '反向代理站点必须填写代理目标地址'
    return
  }

  submitting.value = true
  try {
    const id = editingId.value
    const res = await fetch(endpoint(id || ''), {
      method: id ? 'PUT' : 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        ...form.value,
        name: form.value.name.trim(),
        domain: form.value.domain.trim(),
        root_path: form.value.root_path.trim(),
        proxy_target: form.value.proxy_target.trim(),
        listen_port: Number(form.value.listen_port) || 80,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(data.detail || '保存失败')
    }
    showSuccess(id ? '站点已更新并尝试生效' : '站点已创建并尝试生效')
    closeForm()
    await fetchSites()
  } catch (e) {
    error.value = e.message || '网络错误'
  } finally {
    submitting.value = false
  }
}

async function deleteSite(item) {
  const ok = window.confirm(`确认删除站点「${item.name}」吗？这会影响系统 Nginx 配置。`)
  if (!ok) return
  error.value = ''
  success.value = ''
  try {
    const res = await fetch(endpoint(item.id), {
      method: 'DELETE',
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(data.detail || '删除失败')
    }
    showSuccess(data.message || '站点已删除')
    await fetchSites()
  } catch (e) {
    error.value = e.message || '网络错误'
  }
}

async function applySite(item) {
  error.value = ''
  success.value = ''
  try {
    const res = await fetch(`/api/sites/${item.id}/apply`, {
      method: 'POST',
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(data.detail || '生效失败')
    }
    showSuccess(`站点「${item.name}」已重新生效`)
    await fetchSites()
  } catch (e) {
    error.value = e.message || '网络错误'
  }
}

onMounted(fetchSites)
</script>

<template>
  <div class="page-sites">
    <div class="head">
      <div>
        <h1>网站与反向代理</h1>
        <p class="muted">管理静态站点与反向代理站点，保存后会尝试校验并重载 Nginx。</p>
      </div>
      <button type="button" class="btn-primary" @click="openCreate">新增站点</button>
    </div>

    <p v-if="error" class="msg msg-error">{{ error }}</p>
    <p v-if="success" class="msg msg-success">{{ success }}</p>

    <section class="section">
      <div v-if="loading" class="state">加载中…</div>
      <div v-else-if="items.length === 0" class="state">暂无站点，点击右上角「新增站点」开始。</div>
      <div v-else class="table-wrap">
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
              <td class="resource-col" :title="formatResource(item)">{{ formatResource(item) }}</td>
              <td>{{ item.listen_port }}</td>
              <td><span class="status-pill" :class="statusClass(item.status)">{{ statusText(item.status) }}</span></td>
              <td>{{ item.enabled ? '是' : '否' }}</td>
              <td class="date-col">{{ formatDate(item.updated_at) }}</td>
              <td class="actions">
                <button type="button" class="btn-secondary" @click="openEdit(item)">编辑</button>
                <button type="button" class="btn-secondary" @click="applySite(item)">生效</button>
                <button type="button" class="btn-danger" @click="deleteSite(item)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="showForm" class="section">
      <div class="card">
        <h2>{{ isEditing ? '编辑站点' : '新增站点' }}</h2>
        <form @submit.prevent="saveSite">
          <div class="form-grid">
            <div class="form-row">
              <label for="site-name">站点名称</label>
              <input id="site-name" v-model="form.name" type="text" maxlength="64" placeholder="例如 blog" />
            </div>

            <div class="form-row">
              <label for="site-domain">域名</label>
              <input id="site-domain" v-model="form.domain" type="text" maxlength="255" placeholder="例如 blog.example.com" />
            </div>

            <div class="form-row">
              <label for="site-type">类型</label>
              <select id="site-type" v-model="form.site_type">
                <option value="proxy">反向代理</option>
                <option value="static">静态站点</option>
              </select>
            </div>

            <div class="form-row">
              <label for="site-port">监听端口</label>
              <input id="site-port" v-model.number="form.listen_port" type="number" min="1" max="65535" />
            </div>

            <div class="form-row form-row-checkbox full">
              <label>
                <input v-model="form.enabled" type="checkbox" />
                启用站点
              </label>
            </div>

            <div v-if="form.site_type === 'static'" class="form-row full">
              <label for="root-path">根目录</label>
              <input id="root-path" v-model="form.root_path" type="text" placeholder="例如 /var/www/blog" />
            </div>

            <div v-else class="form-row full">
              <label for="proxy-target">代理目标地址</label>
              <input id="proxy-target" v-model="form.proxy_target" type="text" placeholder="例如 http://127.0.0.1:3000" />
            </div>
          </div>

          <div class="form-actions">
            <button type="button" class="btn-secondary" :disabled="submitting" @click="closeForm">取消</button>
            <button type="submit" class="btn-primary" :disabled="submitting">
              {{ submitting ? '保存中…' : '保存并生效' }}
            </button>
          </div>
        </form>
      </div>
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

.form-row-checkbox input[type='checkbox'] {
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
  padding: 0.45rem 0.85rem;
  border-radius: 6px;
  font-size: 0.85rem;
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
</style>

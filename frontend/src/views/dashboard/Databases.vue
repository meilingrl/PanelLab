<script setup>
import { ref, onMounted } from 'vue'

const error = ref('')
const success = ref('')
let successTimer = null
function showSuccess(msg) {
  success.value = msg
  if (successTimer) clearTimeout(successTimer)
  successTimer = setTimeout(() => { success.value = '' }, 4000)
}

const dbLoading = ref(true)
const dbItems = ref([])
const showDbForm = ref(false)
const dbSubmitting = ref(false)
const dbForm = ref({ name: '', charset: 'utf8mb4', collation: 'utf8mb4_unicode_ci' })

const userLoading = ref(true)
const userItems = ref([])
const showUserForm = ref(false)
const userSubmitting = ref(false)
const userForm = ref({
  username: '',
  password: '',
  host: '%',
  database: '',
  privileges: ['SELECT', 'INSERT', 'UPDATE', 'DELETE'],
})
const privilegeOptions = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'INDEX', 'ALTER', 'REFERENCES', 'EXECUTE', 'ALL PRIVILEGES']

function authHeaders(extra = {}) {
  return {
    Authorization: `Bearer ${localStorage.getItem('panel_token')}`,
    ...extra,
  }
}

async function fetchDatabases() {
  dbLoading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/db/databases', { headers: authHeaders() })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '获取数据库列表失败')
    dbItems.value = data.items || []
  } catch (e) {
    error.value = e.message || '网络错误'
    dbItems.value = []
  } finally {
    dbLoading.value = false
  }
}

async function fetchUsers() {
  userLoading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/db/users', { headers: authHeaders() })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '获取用户列表失败')
    userItems.value = data.items || []
  } catch (e) {
    error.value = e.message || '网络错误'
    userItems.value = []
  } finally {
    userLoading.value = false
  }
}

function openDbForm() {
  showDbForm.value = true
  dbForm.value = { name: '', charset: 'utf8mb4', collation: 'utf8mb4_unicode_ci' }
  error.value = ''
}
function closeDbForm() {
  showDbForm.value = false
}

async function createDatabase() {
  error.value = ''
  const name = (dbForm.value.name || '').trim()
  if (!name) {
    error.value = '请填写数据库名（仅允许字母、数字、下划线）'
    return
  }
  dbSubmitting.value = true
  try {
    const res = await fetch('/api/db/databases', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        name,
        charset: dbForm.value.charset || 'utf8mb4',
        collation: dbForm.value.collation || 'utf8mb4_unicode_ci',
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '创建失败')
    showSuccess('数据库已创建')
    closeDbForm()
    await fetchDatabases()
  } catch (e) {
    error.value = e.message || '网络错误'
  } finally {
    dbSubmitting.value = false
  }
}

async function deleteDatabase(item) {
  const ok = window.confirm(`确认删除数据库「${item.name}」吗？此操作不可恢复，且会删除该库内所有数据。`)
  if (!ok) return
  error.value = ''
  try {
    const res = await fetch(`/api/db/databases/${encodeURIComponent(item.name)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '删除失败')
    showSuccess(data.message || '数据库已删除')
    await fetchDatabases()
  } catch (e) {
    error.value = e.message || '网络错误'
  }
}

function openUserForm() {
  showUserForm.value = true
  userForm.value = {
    username: '',
    password: '',
    host: '%',
    database: dbItems.value.length ? dbItems.value[0].name : '',
    privileges: ['SELECT', 'INSERT', 'UPDATE', 'DELETE'],
  }
  error.value = ''
}
function closeUserForm() {
  showUserForm.value = false
}

function togglePrivilege(priv) {
  const p = userForm.value.privileges || []
  const i = p.indexOf(priv)
  if (i === -1) p.push(priv)
  else p.splice(i, 1)
  userForm.value.privileges = [...p]
}

async function createUser() {
  error.value = ''
  const u = userForm.value
  if (!(u.username || '').trim()) {
    error.value = '请填写用户名（仅允许字母、数字、下划线）'
    return
  }
  if (!(u.password || '').trim()) {
    error.value = '请填写密码（至少 8 位）'
    return
  }
  if (!(u.database || '').trim()) {
    error.value = '请选择要授权的数据库'
    return
  }
  if (!(u.privileges || []).length) {
    error.value = '请至少选择一项权限'
    return
  }
  userSubmitting.value = true
  try {
    const res = await fetch('/api/db/users', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        username: (u.username || '').trim(),
        password: u.password,
        host: (u.host || '').trim() || '%',
        database: (u.database || '').trim(),
        privileges: u.privileges || [],
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '创建失败')
    showSuccess('用户已创建并授权')
    closeUserForm()
    await fetchUsers()
  } catch (e) {
    error.value = e.message || '网络错误'
  } finally {
    userSubmitting.value = false
  }
}

async function deleteUser(item) {
  const ok = window.confirm(`确认删除用户「${item.user}」@「${item.host}」吗？该用户将无法再登录 MySQL。`)
  if (!ok) return
  error.value = ''
  try {
    const url = `/api/db/users/${encodeURIComponent(item.user)}?host=${encodeURIComponent(item.host || '%')}`
    const res = await fetch(url, { method: 'DELETE', headers: authHeaders() })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '删除失败')
    showSuccess(data.message || '用户已删除')
    await fetchUsers()
  } catch (e) {
    error.value = e.message || '网络错误'
  }
}

onMounted(() => {
  fetchDatabases().then(() => fetchUsers())
})
</script>

<template>
  <div class="page-databases">
    <div class="head">
      <div>
        <h1>数据库管理</h1>
        <p class="muted">管理 MySQL 数据库与用户，创建/删除操作不可逆，请谨慎操作。</p>
      </div>
    </div>

    <p v-if="error" class="msg msg-error">{{ error }}</p>
    <p v-if="success" class="msg msg-success">{{ success }}</p>

    <section class="section">
      <div class="card-head">
        <h2>数据库列表</h2>
        <button type="button" class="btn-primary" @click="openDbForm">新建数据库</button>
      </div>
      <div v-if="dbLoading" class="state">加载中…</div>
      <div v-else-if="dbItems.length === 0" class="state">暂无可管理数据库，点击「新建数据库」创建。</div>
      <div v-else class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>数据库名</th>
              <th>字符集</th>
              <th>排序规则</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in dbItems" :key="item.name">
              <td>{{ item.name }}</td>
              <td>{{ item.charset || '—' }}</td>
              <td>{{ item.collation || '—' }}</td>
              <td class="actions">
                <button type="button" class="btn-danger" @click="deleteDatabase(item)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="showDbForm" class="section">
      <div class="card">
        <h2>新建数据库</h2>
        <form @submit.prevent="createDatabase">
          <div class="form-grid">
            <div class="form-row">
              <label for="db-name">数据库名</label>
              <input id="db-name" v-model="dbForm.name" type="text" maxlength="64" placeholder="仅允许字母、数字、下划线" />
            </div>
            <div class="form-row">
              <label for="db-charset">字符集</label>
              <input id="db-charset" v-model="dbForm.charset" type="text" placeholder="utf8mb4" />
            </div>
            <div class="form-row full">
              <label for="db-collation">排序规则</label>
              <input id="db-collation" v-model="dbForm.collation" type="text" placeholder="utf8mb4_unicode_ci" />
            </div>
          </div>
          <div class="form-actions">
            <button type="button" class="btn-secondary" :disabled="dbSubmitting" @click="closeDbForm">取消</button>
            <button type="submit" class="btn-primary" :disabled="dbSubmitting">
              {{ dbSubmitting ? '创建中…' : '创建' }}
            </button>
          </div>
        </form>
      </div>
    </section>

    <section class="section">
      <div class="card-head">
        <h2>用户列表</h2>
        <button type="button" class="btn-primary" @click="openUserForm">新建用户</button>
      </div>
      <div v-if="userLoading" class="state">加载中…</div>
      <div v-else-if="userItems.length === 0" class="state">暂无用户记录，点击「新建用户」创建并授权。</div>
      <div v-else class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>用户名</th>
              <th>主机</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, idx) in userItems" :key="`${item.user}-${item.host}-${idx}`">
              <td>{{ item.user }}</td>
              <td>{{ item.host }}</td>
              <td class="actions">
                <button type="button" class="btn-danger" @click="deleteUser(item)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="showUserForm" class="section">
      <div class="card">
        <h2>新建用户并授权</h2>
        <form @submit.prevent="createUser">
          <div class="form-grid">
            <div class="form-row">
              <label for="user-name">用户名</label>
              <input id="user-name" v-model="userForm.username" type="text" maxlength="64" placeholder="仅允许字母、数字、下划线" />
            </div>
            <div class="form-row">
              <label for="user-host">主机</label>
              <input id="user-host" v-model="userForm.host" type="text" placeholder="% 表示任意主机" />
            </div>
            <div class="form-row">
              <label for="user-password">密码</label>
              <input id="user-password" v-model="userForm.password" type="password" placeholder="至少 8 位" />
            </div>
            <div class="form-row">
              <label for="user-database">授权数据库</label>
              <select id="user-database" v-model="userForm.database">
                <option value="">请选择</option>
                <option v-for="db in dbItems" :key="db.name" :value="db.name">{{ db.name }}</option>
              </select>
            </div>
            <div class="form-row full">
              <label>权限（至少选一项）</label>
              <div class="privilege-chips">
                <label v-for="p in privilegeOptions" :key="p" class="chip">
                  <input type="checkbox" :checked="(userForm.privileges || []).includes(p)" @change="togglePrivilege(p)" />
                  <span>{{ p }}</span>
                </label>
              </div>
            </div>
          </div>
          <div class="form-actions">
            <button type="button" class="btn-secondary" :disabled="userSubmitting" @click="closeUserForm">取消</button>
            <button type="submit" class="btn-primary" :disabled="userSubmitting">
              {{ userSubmitting ? '创建中…' : '创建并授权' }}
            </button>
          </div>
        </form>
      </div>
    </section>

    <section class="section">
      <div class="card card-muted">
        <h2>计划任务</h2>
        <p class="muted">计划任务（cron）功能预留，后续版本开放。</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page-databases {
  max-width: 1100px;
}
.head {
  margin-bottom: 1rem;
}
.page-databases h1 {
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
  margin-bottom: 1.5rem;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.75rem;
}
.card-head h2 {
  margin: 0;
  font-size: 1.1rem;
  color: var(--text-primary);
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
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
.data-table th,
.data-table td {
  padding: 0.65rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
  color: var(--text-primary);
  vertical-align: middle;
}
.data-table th {
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  white-space: nowrap;
}
.data-table tbody tr:hover {
  background: var(--bg-tertiary);
}
.actions {
  white-space: nowrap;
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
.card-muted .muted {
  margin-top: 0.25rem;
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
.privilege-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  cursor: pointer;
  user-select: none;
  font-size: 0.9rem;
  color: var(--text-secondary);
}
.chip input {
  width: auto;
  cursor: pointer;
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
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
